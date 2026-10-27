"""Test fixtures: disposable git repos with a local bare remote, a fake Asana
API server, and stub `gh` / `glab` executables. Nothing touches a real service."""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
ADLC = ROOT / "skills" / "asana-adlc" / "scripts" / "adlc"
sys.path.insert(0, str(ADLC.parent))

TOKEN = "1/1234567890123:abcdef0123456789abcdef0123456789"


# --- Fake Asana ------------------------------------------------------------

class FakeAsana:
    def __init__(self):
        self.tasks, self.stories, self.sections = {}, {}, {}
        self.custom_fields = {}
        self.failures = []  # dicts: method, contains, code, times, apply
        self.requests = []
        self.me = {"gid": "u1", "email": "dev@example.com", "name": "Dev",
                   "workspaces": [{"gid": "w1", "name": "Acme"}]}
        self.projects = {"3001": {"gid": "3001", "name": "Web App", "notes": "Customer-facing web application.",
                                  "permalink_url": "https://app.asana.com/0/3001/list", "archived": False,
                                  "workspace": {"gid": "w1", "name": "Acme"}}}
        self.section_names = {"s-ready": "Ready for Dev", "s-progress": "In Progress", "s-review": "In Review",
                              "s-blocked": "Blocked"}
        self.project_sections = {"3001": ["s-ready", "s-progress", "s-review", "s-blocked"]}
        handler = self._handler()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def add_task(self, gid, name, notes, section, assignee="dev@example.com", **extra):
        self.tasks[gid] = {"gid": gid, "name": name, "notes": notes, "completed": False,
                           "permalink_url": f"https://app.asana.com/0/3001/{gid}",
                           "assignee": {"gid": "u1" if assignee == "dev@example.com" else "u2", "email": assignee},
                           "tags": [], "custom_fields": [], "created_at": "2026-10-01T00:00:00Z", **extra}
        self.stories.setdefault(gid, [])
        self.sections.setdefault(section, []).append(gid)

    def section_of(self, gid):
        return [s for s, members in self.sections.items() if gid in members]

    def comments(self, gid, contains=""):
        return [s for s in self.stories.get(gid, []) if contains in s["text"]]

    def _task_view(self, gid):
        task = copy.deepcopy(self.tasks[gid])
        task["memberships"] = [{"project": {"gid": p}, "section": {"gid": s, "name": self.section_names.get(s)}}
                               for s in self.section_of(gid)
                               for p, secs in self.project_sections.items() if s in secs]
        task.pop("_deps", None)
        return task

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, payload, headers=None):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _route(self, method):
                parsed = urlparse(self.path)
                path, query = parsed.path.replace("/api/1.0", ""), parse_qs(parsed.query)
                fake.requests.append((method, path))
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    return self._send(401, {"errors": [{"message": "Not Authorized"}]})
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else {}
                for f in fake.failures:
                    if f["method"] == method and f["contains"] in path and f["times"] > 0:
                        f["times"] -= 1
                        if f.get("apply"):
                            self._apply(method, path, body)
                        return self._send(f["code"], {"errors": [{"message": "injected"}]},
                                          {"Retry-After": "0"} if f["code"] == 429 else None)
                result = self._apply(method, path, body, query)
                if result is None:
                    return self._send(404, {"errors": [{"message": "Unknown object"}]})
                self._send(200, result)

            def _apply(self, method, path, body, query=None):
                parts = path.strip("/").split("/")
                query = query or {}
                if parts == ["users", "me"]:
                    return {"data": fake.me}
                if parts[0] == "tasks" and len(parts) >= 2 and parts[1] in fake.tasks:
                    gid = parts[1]
                    if len(parts) == 2 and method == "GET":
                        return {"data": fake._task_view(gid)}
                    if len(parts) == 2 and method == "PUT":
                        data = body.get("data", {})
                        if "completed" in data:
                            fake.tasks[gid]["completed"] = data["completed"]
                        for fg, og in (data.get("custom_fields") or {}).items():
                            fields = [f for f in fake.tasks[gid]["custom_fields"] if f["gid"] != fg]
                            fake.tasks[gid]["custom_fields"] = fields + [{"gid": fg, "enum_value": {"gid": og}}]
                        return {"data": fake._task_view(gid)}
                    if parts[2] == "stories" and method == "GET":
                        items = fake.stories[gid]
                        limit = int(query.get("limit", ["100"])[0])
                        offset = int(query.get("offset", ["0"])[0])
                        page = items[offset:offset + limit]
                        nxt = {"offset": str(offset + limit)} if offset + limit < len(items) else None
                        return {"data": page, "next_page": nxt}
                    if parts[2] == "stories" and method == "POST":
                        story = {"gid": f"s{len(fake.stories[gid]) + 1}", "resource_subtype": "comment_added",
                                 "text": body["data"]["text"], "created_by": {"gid": "u1", "name": "Dev"}}
                        fake.stories[gid].append(story)
                        return {"data": story}
                    if parts[2] == "subtasks":
                        return {"data": [], "next_page": None}
                    if parts[2] == "dependencies":
                        return {"data": fake.tasks[gid].get("_deps", []), "next_page": None}
                if parts[0] == "sections" and len(parts) == 3 and parts[2] == "tasks":
                    return {"data": [fake._task_view(g) for g in fake.sections.get(parts[1], [])
                                     if not fake.tasks[g]["completed"]], "next_page": None}
                if parts[0] == "sections" and len(parts) == 3 and parts[2] == "addTask":
                    gid = body["data"]["task"]
                    for members in fake.sections.values():
                        if gid in members:
                            members.remove(gid)
                    fake.sections.setdefault(parts[1], []).append(gid)
                    return {"data": {}}
                if parts == ["workspaces"]:
                    return {"data": fake.me["workspaces"], "next_page": None}
                if parts == ["projects"]:
                    ws = query.get("workspace", [""])[0]
                    return {"data": [{"gid": p["gid"], "name": p["name"], "permalink_url": p["permalink_url"]}
                                     for p in fake.projects.values() if p["workspace"]["gid"] == ws and not p["archived"]],
                            "next_page": None}
                if parts[0] == "projects" and len(parts) >= 2 and parts[1] in fake.projects:
                    pgid = parts[1]
                    if len(parts) == 2:
                        return {"data": fake.projects[pgid]}
                    if parts[2] == "sections":
                        return {"data": [{"gid": s, "name": fake.section_names.get(s)} for s in fake.project_sections.get(pgid, [])],
                                "next_page": None}
                    if parts[2] == "tasks":
                        gids = [g for s in fake.project_sections.get(pgid, []) for g in fake.sections.get(s, [])]
                        return {"data": [fake._task_view(g) for g in gids if not fake.tasks[g]["completed"]], "next_page": None}
                if parts[0] == "custom_fields" and parts[1] in fake.custom_fields:
                    return {"data": fake.custom_fields[parts[1]]}
                return None

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

            def do_PUT(self):
                self._route("PUT")

        return Handler


# --- Stub gh / glab ----------------------------------------------------------

STUB = r'''#!/usr/bin/env python3
import json, os, sys
state_path = os.environ["STUB_PROVIDER_STATE"]
state = json.load(open(state_path)) if os.path.exists(state_path) else {"prs": [], "creates": 0}
tool, args = os.path.basename(sys.argv[0]), sys.argv[1:]
def save(): json.dump(state, open(state_path, "w"))
def opt(name):
    return args[args.index(name) + 1] if name in args else None
if args[:2] == ["auth", "status"]:
    sys.exit(1 if os.environ.get("STUB_PROVIDER_AUTH_FAIL") else 0)
if os.environ.get("STUB_PROVIDER_DOWN"):
    print("error connecting to provider", file=sys.stderr); sys.exit(1)
def find(ref):
    for pr in state["prs"]:
        if str(pr["number"]) == str(ref) or pr["url"] == ref or pr["head"] == ref:
            return pr
if tool == "gh":
    if args[:2] == ["pr", "list"]:
        head = opt("--head")
        print(json.dumps([{"number": p["number"], "url": p["url"], "state": p["state"], "isDraft": p["draft"]}
                          for p in state["prs"] if p["head"] == head])); sys.exit(0)
    if args[:2] == ["pr", "create"]:
        n = len(state["prs"]) + 1
        pr = {"number": n, "url": f"https://github.com/acme/app/pull/{n}", "state": "OPEN", "draft": "--draft" in args,
              "head": opt("--head"), "base": opt("--base"), "title": opt("--title"),
              "body": open(opt("--body-file")).read(), "review": "", "reviewers": opt("--reviewer")}
        state["prs"].append(pr); state["creates"] += 1; save(); print(pr["url"]); sys.exit(0)
    if args[:2] == ["pr", "view"]:
        p = find(args[2])
        print(json.dumps({"number": p["number"], "url": p["url"], "state": p["state"], "isDraft": p["draft"],
                          "reviewDecision": p["review"], "mergedAt": p.get("merged_at"),
                          "mergeCommit": {"oid": p.get("merge_sha")} if p.get("merge_sha") else None,
                          "headRefName": p["head"], "baseRefName": p["base"]})); sys.exit(0)
if tool == "glab":
    if args[:2] == ["mr", "list"]:
        head = opt("--source-branch")
        print(json.dumps([{"iid": p["number"], "web_url": p["url"], "state": p["state"], "draft": p["draft"]}
                          for p in state["prs"] if p["head"] == head])); sys.exit(0)
    if args[:2] == ["mr", "create"]:
        n = len(state["prs"]) + 1
        pr = {"number": n, "url": f"https://gitlab.com/acme/app/-/merge_requests/{n}", "state": "opened",
              "draft": "--draft" in args, "head": opt("--source-branch"), "base": opt("--target-branch"),
              "title": opt("--title"), "body": opt("--description"), "review": ""}
        state["prs"].append(pr); state["creates"] += 1; save()
        print(f"Creating merge request\n{pr['url']}"); sys.exit(0)
    if args[:2] == ["mr", "view"]:
        p = find(args[2])
        print(json.dumps({"iid": p["number"], "web_url": p["url"], "state": p["state"], "draft": p["draft"],
                          "approved": p["review"] == "APPROVED", "merged_at": p.get("merged_at"),
                          "merge_commit_sha": p.get("merge_sha"), "source_branch": p["head"],
                          "target_branch": p["base"]})); sys.exit(0)
print("stub: unsupported " + " ".join(args), file=sys.stderr); sys.exit(2)
'''


# --- Base test case ----------------------------------------------------------

def sh(cmd, cwd, env=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise AssertionError(f"{cmd} failed: {r.stderr}{r.stdout}")
    return r.stdout.strip()


PY_PROJECT = {
    "app.py": "def add(a, b):\n    return a + b\n",
    "tests/__init__.py": "",
    "tests/test_app.py": "import unittest\nfrom app import add\n\n\nclass T(unittest.TestCase):\n"
                         "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n",
    "pyproject.toml": "[project]\nname = \"demo\"\nversion = \"0.1.0\"\n",
}

TASK_NOTES = ("Add a greeting helper.\n\nAcceptance Criteria:\n"
              "- greet(name) returns 'Hello, <name>!'\n- greet('') raises ValueError\n")


def default_config(tmp_root, **overrides):
    cfg = {
        "asana": {
            "workspace_gid": "w1", "project_gid": "3001",
            "eligibility": {"section_gid": "s-ready", "assignee": "dev@example.com", "tag": None, "custom_field": None},
            "acceptance_criteria": {"source": "notes_heading", "heading": "Acceptance Criteria"},
            "status": {"in_progress": {"type": "section", "section_gid": "s-progress"},
                       "in_review": {"type": "section", "section_gid": "s-review"},
                       "blocked": {"type": "section", "section_gid": "s-blocked"},
                       "approved": None, "merged": {"type": "complete"}},
            "approvers": [],
        },
        "git": {"provider": "github", "remote": "origin", "base_branch": "main", "branch_prefix": "feature/",
                "reviewers": []},
        "checks": None,
        "isolation": {"tmp_root": str(tmp_root), "env": {}, "timeout_seconds": 120},
        "unattended": {"enabled": False, "allowed_gates": [], "allowed_commands": []},
        "limits": {"lock_stale_minutes": 120, "max_fix_iterations": 3},
    }
    for key, value in overrides.items():
        cfg[key] = value
    return cfg


class WorkflowCase(unittest.TestCase):
    """A disposable repo + bare remote + fake Asana + stub provider per test."""

    project = PY_PROJECT
    provider = "github"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adlc-test-"))
        self.asana = FakeAsana()
        self.asana.add_task("1001", "Add greeting helper", TASK_NOTES, "s-ready")
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for tool in ("gh", "glab"):
            stub = bin_dir / tool
            stub.write_text(STUB)
            stub.chmod(0o755)
        gitconfig = self.tmp / "gitconfig"
        gitconfig.write_text("[user]\n\tname = Test Dev\n\temail = dev@example.com\n[init]\n\tdefaultBranch = main\n")
        self.provider_state = self.tmp / "provider.json"
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "ASANA_", "GH_", "GITHUB_"))},
                    "PATH": f"{bin_dir}:{os.environ['PATH']}", "GIT_CONFIG_GLOBAL": str(gitconfig),
                    "GIT_CONFIG_NOSYSTEM": "1", "ASANA_ACCESS_TOKEN": TOKEN, "ASANA_BASE_URL": self.asana.url,
                    "STUB_PROVIDER_STATE": str(self.provider_state), "PYTHONDONTWRITEBYTECODE": "1",
                    "ASANA_ADLC_HOME": str(self.tmp / "adlc-home")}
        self.remote = self.tmp / "remote.git"
        sh(["git", "init", "--bare", "-q", "-b", "main", str(self.remote)], self.tmp, self.env)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.tmp_root = self.tmp / "sandbox"
        files = dict(self.project)
        cfg = default_config(self.tmp_root)
        cfg["git"]["provider"] = self.provider
        cfg = self.config(cfg)
        if cfg is not None:
            files[".claude/asana-adlc.json"] = json.dumps(cfg, indent=2)
        self.write(self.repo, files)
        sh("git init -q -b main && git add -A && git commit -qm init", self.repo, self.env)
        sh(["git", "remote", "add", "origin", str(self.remote)], self.repo, self.env)
        sh("git push -q -u origin main", self.repo, self.env)

    def config(self, cfg):
        return cfg

    def tearDown(self):
        self.asana.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def write(root, files):
        for rel, content in files.items():
            path = Path(root) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    def adlc(self, *args, expect=0, env=None, cwd=None):
        # No stdin: from an interactive terminal, `auth login` would otherwise wait for a token.
        r = subprocess.run([sys.executable, str(ADLC), *args], cwd=cwd or self.repo, capture_output=True,
                           stdin=subprocess.DEVNULL, text=True, env={**self.env, **(env or {})})
        if expect is not None and r.returncode != expect:
            raise AssertionError(f"adlc {' '.join(args)} exited {r.returncode} (wanted {expect})\n"
                                 f"stdout: {r.stdout}\nstderr: {r.stderr}")
        try:
            data = json.loads(r.stdout) if r.stdout.strip().startswith(("{", "[")) else r.stdout
        except json.JSONDecodeError:
            data = r.stdout
        self.last_stderr = r.stderr
        return data

    def approve(self, task, gate, *extra):
        return self.adlc("approve", "--task", task, "--gate", gate, *extra)

    def git(self, *args, cwd=None):
        return sh(["git", *args], cwd or self.repo, self.env)

    def provider_prs(self):
        return json.loads(self.provider_state.read_text()) if self.provider_state.exists() else {"prs": [], "creates": 0}

    def user_state(self):
        """Everything about the user's checkout that the workflow must never change."""
        files = {}
        for path in sorted(self.repo.rglob("*")):
            if ".git" in path.parts or not path.is_file():
                continue
            files[str(path.relative_to(self.repo))] = path.read_bytes()
        return {"status": self.git("status", "--porcelain"), "head": self.git("rev-parse", "HEAD"),
                "branch": self.git("branch", "--show-current"), "stash": self.git("stash", "list"),
                "index": sh("git diff --cached", self.repo, self.env), "files": files}

    def start_full(self, task="1001"):
        intake = self.adlc("init", "--task", task, "--mode", "full")
        self.approve(task, "plan")
        self.approve(task, "workspace")
        ws = self.adlc("workspace", "--task", task)
        self.adlc("checks", "--task", task, "from-detect")
        return intake, Path(ws["worktree"])

    def implement_greet(self, wt, correct=True):
        body = "    return f'Hello, {name}!'\n" if correct else "    return 'Hi'\n"
        self.write(wt, {
            "greet.py": "def greet(name):\n    if not name:\n        raise ValueError('name required')\n" + body,
            "tests/test_greet.py": "import unittest\nfrom greet import greet\n\n\nclass G(unittest.TestCase):\n"
                                   "    def test_greet(self):\n        self.assertEqual(greet('Ada'), 'Hello, Ada!')\n\n"
                                   "    def test_empty(self):\n        with self.assertRaises(ValueError):\n"
                                   "            greet('')\n",
        })
