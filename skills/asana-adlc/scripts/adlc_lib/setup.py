"""Guided setup: connect Asana, choose a project and a task, choose the target
repository, confirm section mappings, and write the configuration.

Everything here is read-only towards Asana (GET requests only). Progress is
kept in `<home>/setup.json` so setup can be stopped and resumed at any step.
"""

import json
import os
import re
from pathlib import Path

from . import AdlcError, ConfigError
from . import asana, config, credentials, detect
from .util import git_try, now, sanitize_url

STEPS = ["auth", "project", "inspect", "task", "repo", "mapping", "config", "ready"]
STATUS_KEYS = ["in_progress", "in_review", "blocked", "approved", "merged"]


# --- State -------------------------------------------------------------------

def state_path():
    return credentials.home() / "setup.json"


def load_state():
    p = state_path()
    return json.loads(p.read_text()) if p.exists() else {}


def save_state(state):
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    state["updated_at"] = now()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, p)
    return state


def forget(keep=("repo",)):
    state = load_state()
    return save_state({k: v for k, v in state.items() if k in keep})


# --- Status ------------------------------------------------------------------

def status(repo_path=None, verify=True):
    """What is configured, what is verified, and the next setup step."""
    state = load_state()
    out = {"state_file": str(state_path()), "token": None, "user": None, "problems": [], "suggestions": []}
    try:
        token, source = credentials.load()
    except ConfigError as e:
        token, source = None, None
        out["problems"].append(str(e))
    out["token"] = source or "missing"
    if token and verify:
        try:
            me = asana.Client(token=token).get("/users/me?opt_fields=gid,email,name,workspaces.name")
            out["user"] = {"gid": me.get("gid"), "email": me.get("email"), "name": me.get("name")}
            out["workspaces"] = [{"gid": w["gid"], "name": w.get("name")} for w in me.get("workspaces") or []]
        except AdlcError as e:
            out["problems"].append(f"Asana connection failed: {e}")
            token = None

    repo_root = (state.get("repo") or {}).get("path") or repo_path
    existing = None
    if repo_root:
        try:
            repo = config.Repo(repo_root)
            cfg = config.load(repo)
            if not cfg.get("_missing"):
                existing = {"path": cfg["_path"], "location": cfg["_location"],
                            "project_gid": cfg["asana"].get("project_gid"), "gaps": config.setup_gaps(cfg)}
                if existing["project_gid"] and not state.get("project"):
                    out["suggestions"].append(
                        f"this repository's config already uses project {existing['project_gid']} — "
                        "offer it as one option; do not assume it")
        except AdlcError:
            pass
    out["existing_config"] = existing
    out["selection"] = {k: state.get(k) for k in ("project", "task", "repo", "mapping")}

    if not token:
        nxt = "auth"
    elif not state.get("project"):
        nxt = "project"
    elif not state["project"].get("inspected_at"):
        nxt = "inspect"
    elif not state.get("task"):
        nxt = "task"
    elif not state.get("repo"):
        nxt = "repo"
    elif not (state.get("mapping") or {}).get("confirmed_at"):
        nxt = "mapping"
    elif not existing or existing["project_gid"] != state["project"]["gid"] or \
            [g for g in existing["gaps"] if "needed for `next`" not in g]:
        nxt = "config"
    else:
        nxt = "ready"
    out["next_step"] = nxt
    return out


# --- Projects ----------------------------------------------------------------

def parse_project_ref(ref):
    ref = (ref or "").strip()
    if re.fullmatch(r"\d{1,30}", ref):
        return ref
    m = re.search(r"/project/(\d+)", ref) or re.search(r"app\.asana\.com/0/(\d+)", ref)
    if m:
        return m.group(1)
    raise AdlcError(f"not an Asana project GID or project URL: {ref!r}")


def list_projects(client, workspace=None, with_sections=False, section_limit=30):
    me = client.get("/users/me?opt_fields=workspaces.name")
    workspaces = [w for w in me.get("workspaces") or [] if not workspace or w["gid"] == workspace]
    if workspace and not workspaces:
        raise AdlcError(f"workspace {workspace} is not accessible to this token")
    projects = []
    for ws in workspaces:
        for p in client.get_all(f"/projects?workspace={ws['gid']}&archived=false&opt_fields=name,permalink_url"):
            projects.append({"gid": p["gid"], "name": p.get("name"), "url": p.get("permalink_url"),
                             "workspace": ws.get("name"), "workspace_gid": ws["gid"]})
    note = None
    if with_sections:
        for p in projects[:section_limit]:
            p["sections"] = [s.get("name") for s in client.get_all(f"/projects/{p['gid']}/sections?opt_fields=name")]
        if len(projects) > section_limit:
            note = f"sections shown for the first {section_limit} projects only"
    return {"workspaces": [{"gid": w["gid"], "name": w.get("name")} for w in workspaces],
            "projects": projects, "count": len(projects), "note": note,
            "empty_reason": None if projects else "the token's user has no access to any non-archived project"}


def select_project(client, ref):
    gid = parse_project_ref(ref)
    p = client.get(f"/projects/{gid}?opt_fields=name,permalink_url,archived,workspace.gid,workspace.name")
    state = load_state()
    previous = (state.get("project") or {}).get("gid")
    state["project"] = {"gid": gid, "name": p.get("name"), "url": p.get("permalink_url"),
                        "workspace_gid": (p.get("workspace") or {}).get("gid"),
                        "workspace": (p.get("workspace") or {}).get("name"), "archived": p.get("archived"),
                        "selected_at": now()}
    if previous != gid:
        for key in ("task", "mapping"):
            state.pop(key, None)
    save_state(state)
    return state["project"]


def _small(task, criteria):
    return 0 < len(criteria) <= 5 and len(task.get("notes") or "") < 1500 and not task.get("num_subtasks")


def inspect_project(client, limit=100, dependency_checks=25):
    state = load_state()
    project = state.get("project")
    if not project:
        raise AdlcError("no project selected yet")
    gid = project["gid"]
    info = client.get(f"/projects/{gid}?opt_fields=name,notes,permalink_url,workspace.name")
    sections = client.get_all(f"/projects/{gid}/sections?opt_fields=name")
    fields = ("name,notes,completed,permalink_url,created_at,due_on,num_subtasks,assignee.gid,assignee.email,"
              "assignee.name,tags.name,memberships.project.gid,memberships.section.gid,memberships.section.name")
    tasks = client.get_all(f"/projects/{gid}/tasks?completed_since=now&opt_fields={fields}")[:limit]
    heading_cfg = {"asana": {"acceptance_criteria": {"source": "notes_heading", "heading": "Acceptance Criteria"}}}
    by_section, rows, checked, deps_available = {s["gid"]: 0 for s in sections}, [], 0, True
    for task in tasks:
        section = next((m.get("section") or {} for m in task.get("memberships") or []
                        if (m.get("project") or {}).get("gid") == gid), {})
        if section.get("gid") in by_section:
            by_section[section["gid"]] += 1
        criteria = asana.extract_criteria(task, heading_cfg)
        row = {"gid": task["gid"], "name": task.get("name"), "url": task.get("permalink_url"),
               "section": section.get("name"), "section_gid": section.get("gid"),
               "assignee": (task.get("assignee") or {}).get("email") or (task.get("assignee") or {}).get("name"),
               "criteria_count": len(criteria), "subtasks": task.get("num_subtasks") or 0,
               "description_chars": len(task.get("notes") or ""), "blocked_by": []}
        if criteria and deps_available and checked < dependency_checks:
            deps = asana.fetch_dependencies(client, task["gid"], "name,completed")
            if deps is None:
                deps_available = False
            else:
                checked += 1
                row["blocked_by"] = [d.get("name") for d in deps if not d.get("completed")]
        row["candidate"] = bool(criteria) and not row["blocked_by"]
        row["small"] = row["candidate"] and _small(task, criteria)
        rows.append(row)
    summary = {
        "project": {"gid": gid, "name": info.get("name"), "url": info.get("permalink_url"),
                    "workspace": (info.get("workspace") or {}).get("name"),
                    "purpose": (info.get("notes") or "").strip()[:600] or None},
        "sections": [{"gid": s["gid"], "name": s.get("name"), "open_tasks": by_section.get(s["gid"], 0)} for s in sections],
        "open_tasks": len(rows),
        "truncated": len(tasks) >= limit,
        "without_criteria": sum(1 for r in rows if not r["criteria_count"]),
        "candidates": [r for r in rows if r["candidate"]],
        "suggested_small": [r["gid"] for r in rows if r["small"]][:5],
        "dependency_checks": (f"checked for {checked} task(s) with acceptance criteria" if deps_available else
                              "not available on this Asana plan — dependencies were not checked"),
    }
    snapshot = credentials.home() / "projects" / f"{gid}.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(json.dumps({"summary": summary, "tasks": rows, "fetched_at": now()}, indent=2))
    project.update({"inspected_at": now(), "snapshot": str(snapshot), "sections": summary["sections"]})
    save_state(state)
    return {**summary, "all_tasks": rows}


def select_task(client, ref):
    state = load_state()
    project = state.get("project") or {}
    if not project.get("inspected_at"):
        raise AdlcError("inspect the selected project first (`adlc setup inspect`)")
    rows = json.loads(Path(project["snapshot"]).read_text())["tasks"]
    ref = (ref or "").strip()
    gid = None
    if re.fullmatch(r"\d{1,30}", ref) or "asana.com" in ref:
        from .ledger import parse_task_ref
        gid = parse_task_ref(ref)
    else:
        exact = [r for r in rows if (r["name"] or "").lower() == ref.lower()]
        partial = [r for r in rows if ref.lower() in (r["name"] or "").lower()]
        matches = exact or partial
        if not matches:
            raise AdlcError(f"no open task in {project['name']!r} matches {ref!r}")
        if len(matches) > 1:
            raise AdlcError("several tasks match — choose one by ID: " +
                            "; ".join(f"{r['gid']} {r['name']}" for r in matches[:10]))
        gid = matches[0]["gid"]
    snapshot = asana.fetch_task(client, gid)
    task = snapshot["task"]
    in_project = any((m.get("project") or {}).get("gid") == project["gid"] for m in task.get("memberships") or [])
    heading_cfg = {"asana": {**config.DEFAULTS["asana"]}, "git": config.DEFAULTS["git"]}
    validation = asana.validate(snapshot, heading_cfg)
    path = credentials.home() / "tasks" / f"{gid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2))
    state["task"] = {"gid": gid, "name": task.get("name"), "url": task.get("permalink_url"),
                     "in_selected_project": in_project, "snapshot": str(path), "selected_at": now()}
    save_state(state)
    section = next(((m.get("section") or {}).get("name") for m in task.get("memberships") or []
                    if (m.get("project") or {}).get("gid") == project["gid"]), None)
    return {"task": state["task"], "section": section,
            "assignee": (task.get("assignee") or {}).get("email"),
            "description": task.get("notes"), "criteria": validation["criteria"],
            "open_subtasks": validation["open_subtasks"], "problems": validation["problems"],
            "warnings": validation["warnings"],
            "comments": [{"by": (c.get("created_by") or {}).get("name"), "text": c.get("text")} for c in snapshot["comments"]],
            "dependencies": [{"name": d.get("name"), "completed": d.get("completed")} for d in snapshot["dependencies"]]
                            if snapshot["dependencies_available"] else None,
            "warning": None if in_project else "this task is not in the selected project"}


# --- Repository ----------------------------------------------------------------

def select_repo(path):
    p = Path(path).expanduser()
    if not p.exists():
        raise ConfigError(f"repository path does not exist: {p}")
    if not p.is_dir():
        raise ConfigError(f"repository path is not a directory: {p}")
    try:
        repo = config.Repo(p)
    except ConfigError:
        raise ConfigError(f"{p.resolve()} is not a git repository — choose a repository (or run `git init` there yourself)")
    root = repo.root
    _, branch = git_try(root, "branch", "--show-current")
    _, dirty = git_try(root, "status", "--porcelain")
    _, remotes = git_try(root, "remote")
    remote_info = {}
    for name in remotes.split():
        _, url = git_try(root, "remote", "get-url", name)
        remote_info[name] = sanitize_url(url)
    code, head = git_try(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    default_branch = head.split("/", 1)[1] if code == 0 and "/" in head else (branch or "main")
    found = detect.detect(root)
    cfg = config.load(repo)
    host = next((u for u in remote_info.values()), "") or ""
    provider = "github" if "github" in host else "gitlab" if "gitlab" in host else "none"
    info = {"path": str(root), "branch": branch, "uncommitted_files": len(dirty.splitlines()),
            "remotes": remote_info, "default_branch_guess": default_branch, "provider_guess": provider,
            "stacks": found["stacks"], "frameworks": found["frameworks"],
            "checks": [{"name": c["name"], "command": c["command"], "class": c["class"]} for c in found["checks"]],
            "notes": found["notes"], "existing_config": None if cfg.get("_missing") else cfg["_path"],
            "selected_at": now()}
    state = load_state()
    state["repo"] = info
    save_state(state)
    return info


# --- Mapping and config ----------------------------------------------------------

def set_mapping(eligible=None, assignee=None, statuses=None):
    state = load_state()
    project = state.get("project") or {}
    if not project.get("inspected_at"):
        raise AdlcError("inspect the selected project first")
    known = {s["gid"]: s["name"] for s in project.get("sections") or []}

    def resolve(value, label):
        if value in (None, "", "none"):
            return None
        if value == "complete":
            if label != "merged":
                raise AdlcError("`complete` is only valid for merged")
            return {"type": "complete"}
        if value not in known:
            raise AdlcError(f"{label}: section {value} is not a section of {project.get('name')!r}")
        return {"type": "section", "section_gid": value, "section_name": known[value]}

    mapping = {"eligible_section": resolve(eligible, "eligible"),
               "assignee": None if (assignee or "").lower() in ("", "none") else assignee,
               "status": {k: resolve((statuses or {}).get(k), k) for k in STATUS_KEYS},
               "confirmed_at": now()}
    state["mapping"] = mapping
    save_state(state)
    return mapping


def write_config(location="user", provider=None, base_branch=None, remote=None):
    state = load_state()
    for key in ("project", "repo", "mapping"):
        if not state.get(key):
            raise AdlcError(f"setup step `{key}` is not complete")
    repo = config.Repo(state["repo"]["path"])
    target = repo.root / config.CONFIG_PATH if location == "repo" else config.user_config_path(repo.root)
    current = json.loads(target.read_text()) if target.exists() else {}
    mapping = state["mapping"]
    cfg = config._merge(current, {
        "asana": {
            "workspace_gid": state["project"]["workspace_gid"],
            "project_gid": state["project"]["gid"],
            "eligibility": {"section_gid": (mapping.get("eligible_section") or {}).get("section_gid"),
                            "assignee": mapping.get("assignee")},
            "status": {k: ({kk: vv for kk, vv in v.items() if kk != "section_name"} if v else None)
                       for k, v in mapping["status"].items()},
        },
        "git": {"provider": provider or current.get("git", {}).get("provider") or state["repo"]["provider_guess"],
                "base_branch": base_branch or current.get("git", {}).get("base_branch") or state["repo"]["default_branch_guess"],
                "remote": remote or current.get("git", {}).get("remote") or "origin"},
    })
    if cfg["git"]["provider"] not in config.PROVIDERS:
        raise ConfigError(f"provider must be one of {sorted(config.PROVIDERS)}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(cfg, indent=2) + "\n")
    state["config"] = {"path": str(target), "location": location, "written_at": now()}
    save_state(state)
    note = ("written into the repository — it will show as a new file in your checkout; commit it only if the team "
            "should share it (it contains no secrets)") if location == "repo" else \
           "stored outside the repository; your checkout is unchanged"
    return {"path": str(target), "location": location, "note": note, "config": cfg}
