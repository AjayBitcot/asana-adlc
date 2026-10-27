"""Guided setup: Asana connection, project/board selection, task selection,
repository selection, mapping, config — all against a mocked Asana API."""

import json
import os
import stat
from pathlib import Path

from helpers import TASK_NOTES, TOKEN, WorkflowCase, sh

BAD_TOKEN = "2/999999999/888888888:deadbeefdeadbeefdeadbeef"


class SetupCase(WorkflowCase):
    """A repository with no asana-adlc config, and an empty credentials home."""

    def config(self, cfg):
        return None

    def setUp(self):
        super().setUp()
        self.home = Path(self.env["ASANA_ADLC_HOME"])
        self.env.pop("ASANA_ACCESS_TOKEN")  # setup must work from the stored credential
        self.outputs = []
        a = self.asana
        a.add_task("1002", "Add dark mode toggle", "Big change.\nAcceptance Criteria:\n- toggle\n- persists\n",
                   "s-ready", num_subtasks=2)
        a.add_task("1003", "Investigate slowness", "No criteria yet.", "s-ready")
        a.add_task("1004", "Add footer link", "Small.\nAcceptance Criteria:\n- link to /help\n", "s-progress",
                   _deps=[{"gid": "1003", "name": "Investigate slowness", "completed": False}])
        a.stories["1001"].append({"gid": "c1", "resource_subtype": "comment_added",
                                  "text": "Please keep it simple", "created_by": {"gid": "u2", "name": "PM"}})

    def adlc(self, *args, **kw):
        result = super().adlc(*args, **kw)
        self.outputs.append(json.dumps(result) if not isinstance(result, str) else result)
        self.outputs.append(self.last_stderr)
        return result

    def login(self, token=TOKEN, expect=0):
        import subprocess, sys
        from helpers import ADLC
        r = subprocess.run([sys.executable, str(ADLC), "auth", "login", "--token-stdin"], input=token + "\n",
                           cwd=self.repo, capture_output=True, text=True, env=self.env)
        self.outputs += [r.stdout, r.stderr]
        self.assertEqual(r.returncode, expect, r.stderr)
        return json.loads(r.stdout) if r.returncode == 0 else r.stderr

    def through_task(self):
        self.login()
        self.adlc("setup", "select-project", "--project", "3001")
        self.adlc("setup", "inspect")
        return self.adlc("setup", "select-task", "--task", "1001")


class MissingConfiguration(SetupCase):
    def test_fresh_install_starts_with_auth(self):
        status = self.adlc("setup", "status")
        self.assertEqual(status["token"], "missing")
        self.assertEqual(status["next_step"], "auth")

    def test_full_run_refused_without_config(self):
        self.login()
        self.adlc("init", "--task", "1001", "--mode", "full", expect=2)
        self.assertIn("setup", self.last_stderr)

    def test_existing_config_is_offered_not_assumed(self):
        cfg_dir = self.repo / ".claude"
        cfg_dir.mkdir()
        (cfg_dir / "asana-adlc.json").write_text(json.dumps({"asana": {"project_gid": "3001"}}))
        self.login()
        status = self.adlc("setup", "status")
        self.assertEqual(status["next_step"], "project")  # still asks which project
        self.assertIn("offer it as one option", status["suggestions"][0])


class Authentication(SetupCase):
    def test_successful_login_stores_token_privately(self):
        result = self.login()
        self.assertEqual(result["user"]["email"], "dev@example.com")
        cred = self.home / "credentials.json"
        self.assertEqual(stat.S_IMODE(cred.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.home.stat().st_mode), 0o700)
        status = self.adlc("setup", "status")
        self.assertIn("credentials file", status["token"])
        self.assertEqual(status["user"]["email"], "dev@example.com")
        self.assertEqual(status["next_step"], "project")

    def test_failed_login_stores_nothing(self):
        err = self.login(BAD_TOKEN, expect=1)
        self.assertIn("401", err)
        self.assertNotIn("deadbeef", err)
        self.assertFalse((self.home / "credentials.json").exists())

    def test_no_terminal_no_flag_refuses(self):
        self.adlc("auth", "login", expect=2)
        self.assertIn("your own terminal", self.last_stderr)

    def test_expired_stored_token_routes_back_to_auth(self):
        self.login()
        data = json.loads((self.home / "credentials.json").read_text())
        data["token"] = BAD_TOKEN
        (self.home / "credentials.json").write_text(json.dumps(data))
        status = self.adlc("setup", "status")
        self.assertEqual(status["next_step"], "auth")
        self.assertIn("401", status["problems"][0])

    def test_world_readable_credentials_refused(self):
        self.login()
        os.chmod(self.home / "credentials.json", 0o644)
        self.adlc("auth", "status", expect=2)
        self.assertIn("chmod 600", self.last_stderr)

    def test_logout(self):
        self.login()
        self.assertTrue(self.adlc("auth", "logout")["removed"])
        self.assertEqual(self.adlc("setup", "status")["next_step"], "auth")


class Projects(SetupCase):
    def test_lists_projects_with_sections(self):
        self.login()
        listing = self.adlc("setup", "projects", "--with-sections")
        self.assertEqual(listing["count"], 1)
        p = listing["projects"][0]
        self.assertEqual((p["gid"], p["name"], p["workspace"]), ("3001", "Web App", "Acme"))
        self.assertEqual(p["sections"], ["Ready for Dev", "In Progress", "In Review", "Blocked"])

    def test_empty_project_list(self):
        self.login()
        self.asana.projects.clear()
        listing = self.adlc("setup", "projects")
        self.assertEqual(listing["projects"], [])
        self.assertIn("no access", listing["empty_reason"])

    def test_api_errors(self):
        self.login()
        self.asana.failures.append({"method": "GET", "contains": "/projects", "code": 500, "times": 10})
        self.adlc("setup", "projects", expect=1)
        self.assertIn("500", self.last_stderr)
        self.asana.failures.clear()
        self.adlc("setup", "select-project", "--project", "9999", expect=1)
        self.assertIn("404", self.last_stderr)

    def test_select_by_id_and_urls(self):
        self.login()
        for ref in ("3001", "https://app.asana.com/0/3001/list",
                    "https://app.asana.com/1/w1/project/3001/board/555"):
            with self.subTest(ref=ref):
                self.assertEqual(self.adlc("setup", "select-project", "--project", ref)["name"], "Web App")
        self.adlc("setup", "select-project", "--project", "web app please", expect=1)

    def test_inspect_summarises_structure_and_candidates(self):
        self.login()
        self.adlc("setup", "select-project", "--project", "3001")
        summary = self.adlc("setup", "inspect")
        self.assertEqual(summary["project"]["purpose"], "Customer-facing web application.")
        self.assertEqual({s["name"]: s["open_tasks"] for s in summary["sections"]},
                         {"Ready for Dev": 3, "In Progress": 1, "In Review": 0, "Blocked": 0})
        candidates = {c["gid"] for c in summary["candidates"]}
        self.assertEqual(candidates, {"1001", "1002"})  # 1003: no criteria; 1004: blocked by a dependency
        self.assertEqual(summary["without_criteria"], 1)
        self.assertEqual(summary["suggested_small"], ["1001"])  # 1002 has subtasks
        blocked = next(t for t in summary["all_tasks"] if t["gid"] == "1004")
        self.assertEqual(blocked["blocked_by"], ["Investigate slowness"])


class Tasks(SetupCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.adlc("setup", "select-project", "--project", "3001")
        self.adlc("setup", "inspect")

    def test_select_by_id_url_and_name(self):
        for ref in ("1001", "https://app.asana.com/0/3001/1001", "greeting helper", "Add greeting helper"):
            with self.subTest(ref=ref):
                detail = self.adlc("setup", "select-task", "--task", ref)
                self.assertEqual(detail["task"]["gid"], "1001")
        self.assertEqual(detail["criteria"], ["greet(name) returns 'Hello, <name>!'", "greet('') raises ValueError"])
        self.assertEqual(detail["section"], "Ready for Dev")
        self.assertEqual(detail["comments"][0]["text"], "Please keep it simple")
        self.assertEqual(detail["problems"], [])

    def test_ambiguous_and_unknown_names(self):
        self.adlc("setup", "select-task", "--task", "add", expect=1)
        self.assertIn("several tasks match", self.last_stderr)
        self.adlc("setup", "select-task", "--task", "nothing like this", expect=1)

    def test_task_problems_are_reported(self):
        detail = self.adlc("setup", "select-task", "--task", "1003")
        self.assertTrue(any("acceptance criteria" in p for p in detail["problems"]))

    def test_free_plan_without_dependencies(self):
        # Asana answers 402 for /dependencies on free workspaces; setup carries on and says so.
        self.asana.failures.append({"method": "GET", "contains": "/dependencies", "code": 402, "times": 100})
        summary = self.adlc("setup", "inspect")
        self.assertIn("not available", summary["dependency_checks"])
        detail = self.adlc("setup", "select-task", "--task", "1001")
        self.assertEqual(detail["task"]["gid"], "1001")
        self.assertIsNone(detail["dependencies"])
        self.assertEqual(detail["problems"], [])
        self.assertTrue(any("dependencies" in w for w in detail["warnings"]))


class Repositories(SetupCase):
    def test_invalid_paths(self):
        self.adlc("setup", "repo", "--path", str(self.tmp / "missing"), expect=2)
        self.assertIn("does not exist", self.last_stderr)
        (self.tmp / "file.txt").write_text("x")
        self.adlc("setup", "repo", "--path", str(self.tmp / "file.txt"), expect=2)
        (self.tmp / "plain").mkdir()
        self.adlc("setup", "repo", "--path", str(self.tmp / "plain"), expect=2)
        self.assertIn("not a git repository", self.last_stderr)
        self.adlc("setup", "repo", expect=1)

    def test_valid_repository_is_inspected(self):
        (self.repo / "scratch.txt").write_text("uncommitted")
        info = self.adlc("setup", "repo", "--path", str(self.repo / "tests"))  # any path inside works
        self.assertEqual(info["path"], str(self.repo))
        self.assertEqual(info["uncommitted_files"], 1)
        self.assertIn("python", info["stacks"])
        self.assertEqual(info["default_branch_guess"], "main")
        self.assertEqual(info["checks"][0]["command"], "python3 -m unittest discover -s tests")


class EndToEndSetup(SetupCase):
    def test_full_setup_is_read_only_redacted_and_ends_ready(self):
        before = self.git("status", "--porcelain")
        self.asana.requests.clear()
        self.through_task()
        self.adlc("setup", "repo", "--path", str(self.repo))
        self.assertEqual(self.adlc("setup", "status")["next_step"], "mapping")
        self.adlc("setup", "map", "--eligible", "nope", expect=1)  # unknown section rejected
        mapping = self.adlc("setup", "map", "--eligible", "s-ready", "--in-progress", "s-progress",
                            "--in-review", "s-review", "--assignee", "dev@example.com")
        self.assertIsNone(mapping["status"]["blocked"])  # unmapped stays unmapped
        self.assertEqual(self.adlc("setup", "status")["next_step"], "config")
        written = self.adlc("setup", "write-config", "--provider", "none")
        self.assertEqual(written["location"], "user")
        self.assertFalse(Path(written["path"]).is_relative_to(self.repo))
        self.assertEqual(self.adlc("setup", "status")["next_step"], "ready")

        # Read-only towards Asana for the whole setup:
        self.assertEqual({m for m, _ in self.asana.requests}, {"GET"})
        # The user's checkout is unchanged:
        self.assertEqual(self.git("status", "--porcelain"), before)
        # The token appears nowhere: outputs, setup state, snapshots, config.
        files = [p.read_text() for p in self.home.rglob("*.json") if p.name != "credentials.json"]
        for text in self.outputs + files:
            self.assertNotIn(TOKEN, text)
            self.assertNotIn("abcdef0123456789abcdef", text)

        # The ADLC workflow takes over with the generated (user-level) config.
        intake = self.adlc("init", "--task", "1001", "--mode", "full")
        self.assertTrue(intake["intake"]["ok"])

    def test_write_config_into_repo_only_when_asked(self):
        self.through_task()
        self.adlc("setup", "repo", "--path", str(self.repo))
        self.adlc("setup", "map", "--eligible", "s-ready")
        written = self.adlc("setup", "write-config", "--location", "repo", "--provider", "none")
        self.assertEqual(written["path"], str(self.repo / ".claude/asana-adlc.json"))
        self.assertIn("new file in your checkout", written["note"])
        self.assertNotIn(TOKEN, (self.repo / ".claude/asana-adlc.json").read_text())

    def test_assignee_none_means_no_filter(self):
        self.through_task()
        self.adlc("setup", "repo", "--path", str(self.repo))
        mapping = self.adlc("setup", "map", "--eligible", "none", "--assignee", "none")
        self.assertIsNone(mapping["assignee"])
        self.adlc("setup", "write-config", "--provider", "none")
        intake = self.adlc("init", "--task", "1001", "--mode", "full")
        self.assertEqual(intake["intake"]["eligibility_failures"], [])


class CancelAndResume(SetupCase):
    def test_setup_resumes_where_it_stopped(self):
        self.login()
        self.adlc("setup", "select-project", "--project", "3001")
        # user stops here; a new session picks up from the saved state
        status = self.adlc("setup", "status")
        self.assertEqual(status["next_step"], "inspect")
        self.assertEqual(status["selection"]["project"]["name"], "Web App")
        self.adlc("setup", "inspect")
        self.adlc("setup", "select-task", "--task", "1001")
        self.assertEqual(self.adlc("setup", "status")["next_step"], "repo")

    def test_changing_project_clears_dependent_choices(self):
        self.through_task()
        self.asana.projects["3002"] = {**self.asana.projects["3001"], "gid": "3002", "name": "Mobile"}
        self.adlc("setup", "select-project", "--project", "3002")
        status = self.adlc("setup", "status")
        self.assertIsNone(status["selection"]["task"])
        self.assertEqual(status["next_step"], "inspect")

    def test_forget(self):
        self.through_task()
        self.adlc("setup", "forget")
        self.assertEqual(self.adlc("setup", "status")["next_step"], "project")
        self.assertTrue((self.home / "credentials.json").exists())  # token kept


class ConfirmationBeforeImplementation(SetupCase):
    def test_workspace_needs_plan_approval(self):
        self.through_task()
        self.adlc("setup", "repo", "--path", str(self.repo))
        self.adlc("setup", "map", "--eligible", "s-ready")
        self.adlc("setup", "write-config", "--provider", "none")
        self.adlc("init", "--task", "1001", "--mode", "full")
        self.approve("1001", "workspace")
        self.adlc("workspace", "--task", "1001", expect=3)
        self.assertIn("`plan`", self.last_stderr)
        self.assertNotIn("asana-adlc", sh("git worktree list", self.repo, self.env))
        self.adlc("reject", "--task", "1001", "--gate", "plan", "--note", "change the approach")
        self.adlc("workspace", "--task", "1001", expect=3)
        self.assertIn("rejected", self.last_stderr)
        self.adlc("resume", "--task", "1001")
        self.approve("1001", "plan")
        self.assertEqual(self.adlc("workspace", "--task", "1001")["status"], "created")

    def test_local_only_repository_without_remote(self):
        sh("git remote remove origin", self.repo, self.env)
        self.through_task()
        self.adlc("setup", "repo", "--path", str(self.repo))
        self.adlc("setup", "map", "--eligible", "none")
        self.adlc("setup", "write-config", "--provider", "none")
        report = self.adlc("preflight", expect=None)
        self.assertEqual(report["problems"], [])
        self.assertTrue(any("local-only" in w for w in report["warnings"]))
        self.adlc("init", "--task", "1001", "--mode", "full")
        self.approve("1001", "plan")
        self.approve("1001", "workspace")
        ws = self.adlc("workspace", "--task", "1001")
        self.assertEqual(ws["status"], "created")
        self.assertEqual(ws["base_ref"], "main")
