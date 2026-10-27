"""`adlc` command line: the only way the skill performs gated or stateful actions.

Output is JSON on stdout (human-readable text for `status`); errors go to
stderr as `adlc: <kind>: <message>` with a distinct exit code:
1 error · 2 config/credentials · 3 approval missing/rejected · 4 blocked ·
5 existing run (choose a recovery option) · 75 remote outcome unknown.
"""

import argparse
import json
import sys
from pathlib import Path

from . import AdlcError, ConfigError, ExistingRunError
from . import asana, checks, config, credentials, detect, gates, guard, ledger, provider, setup, workspace
from .util import git_try, now


def out(data):
    print(json.dumps(data, indent=2, default=str))


def ctx(args):
    repo = config.Repo(args.repo)
    cfg = config.load(repo)
    # Local-only delivery on a repository without the configured remote: nothing to fetch or check remotely.
    cfg["_local_only"] = (cfg["git"]["provider"] == "none"
                          and git_try(repo.root, "remote", "get-url", cfg["git"]["remote"])[0] != 0)
    cfg["_offline"] = getattr(args, "offline", False) or cfg["_local_only"]
    return repo, cfg, ledger.Store(repo)


def get_run(store, args):
    return store.get(ledger.parse_task_ref(args.task), getattr(args, "run", None))


# --- Commands ----------------------------------------------------------------

def cmd_auth(args):
    if args.action == "status":
        token, source = credentials.load()
        if not token:
            return out({"token": "missing", "fix": "run `adlc auth login` in your own terminal"})
        me = asana.Client(token=token).get("/users/me?opt_fields=gid,email,name")
        return out({"token": source, "verified": True, "user": {k: me.get(k) for k in ("gid", "email", "name")}})
    if args.action == "logout":
        removed = credentials.delete()
        return out({"removed": removed, "path": str(credentials.path()),
                    "note": "an ASANA_ACCESS_TOKEN exported in your shell is not affected"})
    import getpass
    import os
    if args.from_env:
        token = os.environ.get("ASANA_ACCESS_TOKEN", "")
    elif args.token_stdin:
        token = sys.stdin.readline().strip()
    elif sys.stdin.isatty():
        token = getpass.getpass("Asana personal access token (input hidden): ").strip()
    else:
        raise ConfigError("no terminal for a hidden prompt — run `adlc auth login` in your own terminal, "
                          "or pipe the token in with --token-stdin")
    if not token:
        raise ConfigError("no token provided")
    me = asana.Client(token=token).get("/users/me?opt_fields=gid,email,name,workspaces.name")  # read-only check
    path = credentials.save(token, me)
    out({"stored": str(path), "permissions": "0600 (owner only)", "user": {k: me.get(k) for k in ("gid", "email", "name")},
         "workspaces": [w.get("name") for w in me.get("workspaces") or []]})


def _setup_repo_hint(args):
    try:
        return str(config.Repo(args.repo).root)
    except AdlcError:
        return None


def cmd_setup(args):
    if args.action == "status":
        return out(setup.status(_setup_repo_hint(args), verify=not args.offline))
    if args.action == "forget":
        return out(setup.forget(keep=() if args.all else ("repo",)))
    if args.action == "repo":
        if not args.path:
            raise AdlcError("setup repo needs --path")
        return out(setup.select_repo(args.path))
    if args.action == "map":
        statuses = {"in_progress": args.in_progress, "in_review": args.in_review, "blocked": args.blocked,
                    "approved": args.approved, "merged": args.merged}
        return out(setup.set_mapping(args.eligible, args.assignee, statuses))
    if args.action == "write-config":
        return out(setup.write_config(args.location, args.provider, args.base_branch, args.remote))
    client = asana.Client()
    if args.action == "projects":
        return out(setup.list_projects(client, args.workspace, args.with_sections))
    if args.action == "select-project":
        return out(setup.select_project(client, args.project))
    if args.action == "inspect":
        return out(setup.inspect_project(client, limit=args.limit))
    if args.action == "select-task":
        return out(setup.select_task(client, args.task))
    raise AdlcError(f"unknown setup action {args.action}")


def cmd_preflight(args):
    report = {"ok": True, "problems": [], "warnings": []}
    try:
        repo, cfg, store = ctx(args)
    except AdlcError as e:
        return out({"ok": False, "problems": [str(e)]})
    report["repo"] = repo.identity(cfg["git"]["remote"])
    report["config_found"] = not cfg.get("_missing")
    gaps = config.setup_gaps(cfg)
    if gaps:
        report["warnings"].append("config gaps: " + "; ".join(gaps))
    code, dirty = git_try(repo.root, "status", "--porcelain")
    report["user_checkout"] = {"branch": git_try(repo.root, "branch", "--show-current")[1],
                               "uncommitted_files": len(dirty.splitlines()) if code == 0 else None,
                               "note": "never modified — work happens in a separate worktree"}
    _, source = credentials.load()
    report["asana_token"] = source or "missing"
    if not args.offline and report["asana_token"] != "missing":
        try:
            me = asana.Client().get("/users/me?opt_fields=gid,email,name")
            report["asana_user"] = {"gid": me.get("gid"), "email": me.get("email")}
        except AdlcError as e:
            report["problems"].append(f"Asana: {e}")
    elif report["asana_token"] == "missing":
        report["problems"].append("no Asana token — run `adlc auth login` in your own terminal "
                                  "(Soft Run with --task-file still works)")
    prov = provider.Provider(cfg["git"]["provider"], repo.root)
    ok, msg = prov.available() if not args.offline else (None, "skipped (offline)")
    report["git_provider"] = {"kind": cfg["git"]["provider"], "ok": ok, "detail": msg}
    if ok is False and cfg["git"]["provider"] != "none":
        report["problems"].append(f"Git provider: {msg}")
    if cfg["_local_only"]:
        report["warnings"].append(f"no remote `{cfg['git']['remote']}`: local-only delivery, the task branch "
                                  f"starts from local `{cfg['git']['base_branch']}` and is never pushed")
    elif not args.offline:
        code, _ = git_try(repo.root, "ls-remote", "--heads", cfg["git"]["remote"], cfg["git"]["base_branch"])
        if code != 0:
            report["problems"].append(f"remote `{cfg['git']['remote']}` unreachable or base branch missing")
    report["unattended"] = gates.unattended_active(cfg)
    report["active_runs"] = [{"task": r.gid, "run": r.run_id, "mode": r.mode, "status": r.data["status"],
                              "stage": r.data["stage"]} for r in store.all_runs() if r.data["status"] in ledger.ACTIVE]
    report["ok"] = not report["problems"]
    out(report)


def cmd_detect(args):
    out(detect.detect(Path(args.path or config.Repo(args.repo).root)))


def cmd_guard(args):
    command = " ".join(args.command).lstrip("- ").strip() if args.command else ""
    out(guard.classify(command, args.cwd or "."))


def cmd_init(args):
    repo, cfg, store = ctx(args)
    gid = ledger.parse_task_ref(args.task)
    if args.mode == "full" and cfg.get("_missing"):
        raise ConfigError("this repository has no asana-adlc configuration — run the setup flow "
                          "(`adlc setup status`)")
    owner = store.lock_owner(gid)
    if owner and not args.take_over_stale:
        raise ExistingRunError(f"task {gid} is locked by run {owner.get('run_id')} — choose a recovery option")
    me = None
    if args.task_file:
        raw = json.loads(Path(args.task_file).read_text())
        snapshot = raw if "task" in raw else {"task": raw, "comments": [], "subtasks": [], "dependencies": []}
        snapshot["task"].setdefault("gid", gid)
        snapshot["source"] = f"file:{args.task_file}"
    else:
        client = asana.Client()
        me = client.get("/users/me?opt_fields=gid,email,name")
        snapshot = asana.fetch_task(client, gid)
    intake = asana.validate(snapshot, cfg, me)
    stack = detect.detect(repo.root)
    task = snapshot["task"]
    run = store.create(gid, args.mode, {"name": task.get("name"), "url": task.get("permalink_url")},
                       repo.identity(cfg["git"]["remote"]),
                       {k: stack[k] for k in ("stacks", "frameworks", "package_managers", "notes")})
    (run.dir / "task.json").write_text(json.dumps(snapshot, indent=2))
    run.data["intake"] = intake
    store.acquire(run, cfg["limits"]["lock_stale_minutes"], args.take_over_stale)
    run.log("intake validated" if intake["ok"] else "intake problems found", stage=1)
    run.save()
    out({"task": gid, "run_id": run.run_id, "mode": run.mode, "intake": intake, "stack": run.data["stack"],
         "task_file": str(run.dir / "task.json"), "run_dir": str(run.dir)})


def cmd_plan(args):
    """Record the plan file and its high-impact items (they always need individual approval)."""
    repo, cfg, store = ctx(args)
    run = get_run(store, args)
    if not Path(args.file).is_file():
        raise AdlcError(f"plan file not found: {args.file}")
    run.data["plan"] = {"path": str(Path(args.file).resolve()), "high_impact": args.high_impact or [],
                        "open_questions": args.open_question or [], "at": now()}
    run.log("plan recorded", high_impact=len(args.high_impact or []))
    run.save()
    out(run.data["plan"])


def cmd_refresh(args):
    """Re-read the task from Asana (read-only) and re-validate it, e.g. after a requirements blocker."""
    repo, cfg, store = ctx(args)
    run = get_run(store, args)
    client = asana.Client()
    me = client.get("/users/me?opt_fields=gid,email,name")
    snapshot = asana.fetch_task(client, run.gid)
    (run.dir / "task.json").write_text(json.dumps(snapshot, indent=2))
    run.data["intake"] = asana.validate(snapshot, cfg, me)
    run.data["task"]["name"] = snapshot["task"].get("name")
    run.log("task re-read from Asana", stage=1)
    run.save()
    out({"intake": run.data["intake"], "comments": len(snapshot["comments"]), "task_file": str(run.dir / "task.json")})


def cmd_status(args):
    repo, cfg, store = ctx(args)
    if not args.task:
        runs = store.all_runs()
        if args.json:
            return out([{"task": r.gid, "run": r.run_id, "mode": r.mode, "status": r.data["status"],
                         "stage": r.data["stage"], "name": r.data["task"]["name"]} for r in runs])
        if not runs:
            return print("No workflow runs recorded in this repository.")
        for r in runs:
            print(f"{r.gid}  {r.run_id}  {r.mode:4}  {r.data['status']:17}  stage {r.data['stage']}  {r.data['task']['name']}")
        return
    run = get_run(store, args)
    if args.json:
        return out({**run.data, "run_dir": str(run.dir)})
    print(format_status(store, cfg, run))


def format_status(store, cfg, run):
    d, lines = run.data, []
    ws = d["workspace"]
    owner = store.lock_owner(run.gid)
    lines += [f"Task      {run.gid} · {d['task']['name']}", f"          {d['task'].get('url') or ''}",
              f"Run       {run.run_id} ({run.mode})   status: {d['status']}   lock: "
              + (f"held by {owner['run_id']}" if owner else "free"),
              f"Stage     current {d['stage']} ({ledger.STAGES[d['stage']]}), last completed "
              f"{d['last_completed_stage']} ({ledger.STAGES[d['last_completed_stage']]})",
              f"Next      {d['next_action']}"]
    if d.get("stack"):
        lines.append(f"Stack     {', '.join(d['stack'].get('stacks', []) + d['stack'].get('frameworks', [])) or 'unknown'}")
    if ws:
        wt = ws.get("worktree")
        exists = wt and Path(wt).is_dir()
        lines.append(f"Branch    {ws.get('branch')} from {ws.get('base_ref')} @ {str(ws.get('base_sha'))[:10]}")
        lines.append(f"Worktree  {wt} ({'present' if exists else 'missing'})")
        if exists:
            _, st = git_try(wt, "status", "--porcelain")
            _, head = git_try(wt, "rev-parse", "--short", "HEAD")
            lines.append(f"          HEAD {head}, {len(st.splitlines())} uncommitted path(s)")
    if d["files_changed"]:
        lines.append("Changed   " + ", ".join(f"{f['status']} {f['path']}" for f in d["files_changed"][:12])
                     + (" …" if len(d["files_changed"]) > 12 else ""))
    for c in d["checks"]:
        b, f = (c.get("baseline") or {}).get("status", "—"), (c.get("final") or {}).get("status", "—")
        cls = (c.get("classification") or {}).get("as")
        lines.append(f"Check     {c['name']}: baseline {b} → final {f}{f' ({cls})' if cls else ''}"
                     f"{'' if c['mandatory'] else ' [optional]'} — `{c['command']}`")
    if d.get("validation"):
        lines.append(f"Validation {d['validation']['state']}")
    g = d["git"]
    lines.append(f"Push      {g.get('push_status')}" + (f" @ {g['pushed_sha'][:10]}" if g.get("pushed_sha") else ""))
    if d.get("pr"):
        lines.append(f"PR        {d['pr'].get('url')} ({d['pr'].get('state')}{', draft' if d['pr'].get('draft') else ''})")
    for key, op in d["asana"].items():
        lines.append(f"Asana     {key}: {op['status']}{' — ' + op['detail'] if op.get('detail') else ''}")
    for res in d["resources"]:
        lines.append(f"Resource  {res['type']} {res['ref']}: {res['status']}{' (keep)' if res.get('keep') else ''}"
                     f"{' — ' + res['detail'] if res.get('detail') else ''}")
    for line in gates.pending_gate_summary(run):
        lines.append(f"Decision  {line}")
    if d["pending_actions"]:
        lines.append("Pending   " + "; ".join(d["pending_actions"]))
    if d.get("blocker"):
        lines.append(f"Blocked   {d['blocker']['reason']} → {d['blocker']['required_action']}")
    for e in d["errors"][-3:]:
        lines.append(f"Error     [{e['at']}] stage {e['stage']}: {e['message']}")
    if d.get("recovery"):
        lines.append(f"Recovery  {d['recovery']}")
    if d["status"] in ledger.ACTIVE:
        lines.append("Options   A review · B continue · C retry current stage · D stop and clean up")
    return "\n".join(lines)


def cmd_decide(args, decision):
    repo, cfg, store = ctx(args)
    run = get_run(store, args)
    entry = gates.record(run, args.gate, decision, scope=getattr(args, "scope", "once"), detail=args.detail,
                         soft_run_override=getattr(args, "soft_run_override", False), note=args.note)
    if decision == "rejected":
        store.release(run)
    run.save()
    out(entry)


def cmd_checkpoint(args):
    repo, cfg, store = ctx(args)
    run = get_run(store, args)
    ledger.checkpoint(run, args.stage, done=args.done, action=args.action, next_action=args.next,
                      pending=args.pending if args.pending is not None else None)
    run.save()
    out({"stage": run.data["stage"], "last_completed_stage": run.data["last_completed_stage"],
         "status": run.data["status"], "next_action": run.data["next_action"]})


def cmd_pause(args):
    repo, cfg, store = ctx(args)
    run = get_run(store, args)
    run.data["status"] = "paused"
    run.data["pause_reason"] = args.reason
    run.log("paused", reason=args.reason)
    store.release(run)
    run.save()
    print(format_status(store, cfg, run))


def cmd_resume(args, retry=False):
    repo, cfg, store = ctx(args)
    run = get_run(store, args)
    if run.data["status"] in ledger.TERMINAL:
        raise AdlcError(f"run is {run.data['status']}; nothing to resume (use `sync` for open PRs)")
    store.acquire(run, cfg["limits"]["lock_stale_minutes"], args.take_over_stale)
    run.data["status"] = "active"
    stage = run.data["stage"]
    if retry:
        run.log(f"retry stage {stage} ({ledger.STAGES[stage]})")
        run.data["next_action"] = f"Retry stage {stage} ({ledger.STAGES[stage]}) after reviewing its previous result"
    else:
        run.log("resumed")
    run.save()
    previous = [a for a in run.data["completed_actions"] if a["stage"] == stage]
    errors = [e for e in run.data["errors"] if e["stage"] == stage]
    out({"status": "active", "stage": stage, "stage_name": ledger.STAGES[stage], "next_action": run.data["next_action"],
         "previous_actions_this_stage": previous, "errors_this_stage": errors})


def cmd_block(args):
    repo, cfg, store = ctx(args)
    run = get_run(store, args)
    run.data["status"] = "blocked"
    run.data["blocker"] = {"reason": args.reason, "required_action": args.action, "at_stage": run.data["stage"], "at": now()}
    run.data["recovery"] = args.action
    run.log("blocked", reason=args.reason)
    store.release(run)
    run.save()
    out(run.data["blocker"])


def cmd_workspace(args):
    repo, cfg, store = ctx(args)
    run = get_run(store, args)
    try:
        result = workspace.create(store, run, cfg, offline=cfg["_offline"])
    finally:
        run.save()
    out(result)


def cmd_changes(args):
    repo, cfg, store = ctx(args)
    run = get_run(store, args)
    files = workspace.record_changes(run)
    run.save()
    out({"files_changed": files, "patch": str(run.evidence_dir() / "changes.patch")})


def cmd_snapshot(args):
    repo, cfg, store = ctx(args)
    run = get_run(store, args)
    result = workspace.snapshot(run)
    run.save()
    out(result)


def cmd_checks(args):
    repo, cfg, store = ctx(args)
    run = get_run(store, args)
    cwd = run.data["workspace"].get("worktree") or repo.root
    if args.action == "add":
        result = checks.add(run, args.name, args.command, mandatory=not args.optional, source=args.source or "user", cwd=cwd)
    elif args.action == "from-detect":
        found = detect.detect(Path(cwd))["checks"]
        wanted = set(args.names.split(",")) if args.names else None
        result = [checks.add(run, c["name"], c["command"], mandatory=True, source=c["source"], cwd=cwd)
                  for c in found if wanted is None or c["name"] in wanted]
    elif args.action == "from-config":
        if not cfg.get("checks"):
            raise ConfigError("no `checks` list in the config")
        result = [checks.add(run, c["name"], c["command"], mandatory=c.get("mandatory", True), source="config", cwd=cwd)
                  for c in cfg["checks"]]
    elif args.action == "skip":
        result = checks.skip(run, args.name, args.phase, args.reason)
    elif args.action == "classify":
        result = checks.classify(run, args.name, args.as_, args.reason)
    else:
        result = run.data["checks"]
    run.save()
    out(result)


def cmd_run(args):
    repo, cfg, store = ctx(args)
    run = get_run(store, args)
    try:
        result = checks.execute(run, cfg, args.check, args.phase)
    finally:
        run.save()
    out(result)
    return 0 if result["status"] == "pass" else 6


def cmd_validation(args):
    repo, cfg, store = ctx(args)
    run = get_run(store, args)
    state, details = checks.validation_state(run)
    run.save()
    out({"state": state, "details": details})


def cmd_push(args):
    repo, cfg, store = ctx(args)
    run = get_run(store, args)
    state, _ = checks.validation_state(run)
    try:
        result = workspace.push(store, run, cfg, state)
    finally:
        run.save()
    out(result)


def cmd_pr(args):
    repo, cfg, store = ctx(args)
    run = get_run(store, args)
    state, _ = checks.validation_state(run)
    draft = True if args.draft else (False if args.ready else None)
    if draft is False and state != "ready":
        raise AdlcError(f"validation is `{state}`; a ready-for-review PR needs all mandatory checks passing")
    try:
        result = provider.create_pr(store, run, cfg, args.title, args.body_file, state, draft)
    finally:
        run.save()
    out(result)


def cmd_asana(args):
    repo, cfg, store = ctx(args)
    client = asana.Client()
    if args.action == "me":
        return out(client.get("/users/me?opt_fields=gid,email,name"))
    if args.action == "candidates":
        me = client.get("/users/me?opt_fields=gid,email") if (cfg["asana"]["eligibility"].get("assignee") or "") == "me" else None
        busy = {r.gid for r in store.all_runs() if r.data["status"] in ledger.ACTIVE}
        return out([{"gid": t["gid"], "name": t.get("name"), "created_at": t.get("created_at")}
                    for t in asana.candidates(client, cfg, me, exclude=busy)])
    if args.action == "lookup":
        paths = {"workspaces": "/workspaces?opt_fields=name",
                 "projects": f"/projects?workspace={args.gid}&archived=false&opt_fields=name",
                 "sections": f"/projects/{args.gid}/sections?opt_fields=name",
                 "fields": f"/projects/{args.gid}/custom_field_settings?opt_fields=custom_field.name,custom_field.enum_options.name",
                 "user": f"/users/{args.gid}?opt_fields=name,email"}
        if args.what != "workspaces" and not args.gid:
            raise AdlcError(f"lookup {args.what} needs --gid")
        path = paths[args.what]
        return out(client.get(path) if args.what == "user" else client.get_all(path))
    run = get_run(store, args)
    try:
        if args.action == "comment":
            result = asana.post_comment(client, run, cfg, args.key, Path(args.file).read_text())
        elif args.action == "status":
            result = asana.apply_status(client, run, cfg, args.stage)
        else:
            raise AdlcError(f"unknown asana action {args.action}")
    finally:
        run.save()
    out(result)


SYNC_TEXT = {
    "merged": "🤖 ADLC: PR #{n} was merged ({sha}). {url}",
    "approved": "🤖 ADLC: PR #{n} is approved. {url}",
    "changes-requested": "🤖 ADLC: PR #{n} has changes requested. {url}",
    "pr-closed": "🤖 ADLC: PR #{n} was closed without merging. {url}",
}


def cmd_sync(args):
    repo, cfg, store = ctx(args)
    runs = [get_run(store, args)] if args.task else [r for r in store.all_runs() if r.mode == "full" and r.data["status"] == "pr_open"]
    client = asana.Client() if args.apply else None
    report = []
    for run in runs:
        pr = run.data.get("pr") or {}
        if not pr.get("url"):
            report.append({"task": run.gid, "action": "none", "reason": "no PR recorded"})
            continue
        prov = provider.Provider(cfg["git"]["provider"], repo.root)
        view = prov.view(pr["url"])
        run.data["pr"].update({**view, "verified_at": now()})
        if view["state"] == "merged":
            key, status, new = "merged", "merged", "done"
        elif view["state"] == "closed":
            key, status, new = "pr-closed", "blocked", "blocked"
        elif view["review"] == "approved":
            key, status, new = "approved", "approved", None
        elif view["review"] == "changes_requested":
            key, status, new = "changes-requested", None, None
        else:
            report.append({"task": run.gid, "pr": view["url"], "state": view["state"], "action": "none"})
            run.save()
            continue
        entry = {"task": run.gid, "pr": view["url"], "state": view["state"], "review": view["review"],
                 "comment": f"{key}-{view['number']}", "asana_status": status}
        if args.apply:
            text = SYNC_TEXT[key].format(n=view["number"], url=view["url"], sha=(view.get("merge_sha") or "")[:10])
            try:
                entry["comment_result"] = asana.post_comment(client, run, cfg, f"{key}-{view['number']}", text, gate="asana-sync")
                if status:
                    entry["status_result"] = asana.apply_status(client, run, cfg, status, gate="asana-sync")
                if new:
                    run.data["status"] = new
                    if new == "blocked":
                        run.data["blocker"] = {"reason": "PR closed without merging", "required_action":
                                               "decide whether to reopen, replace, or cancel", "at_stage": 8, "at": now()}
            except AdlcError as e:
                entry["error"] = str(e)
        else:
            entry["planned"] = True
        run.save()
        report.append(entry)
    out(report)


def cmd_cleanup(args):
    repo, cfg, store = ctx(args)
    run = get_run(store, args)
    try:
        report = workspace.cleanup(store, run, cfg, include_uncertain=args.include_uncertain)
    finally:
        run.save()
    report["remaining"] = [{"id": r["id"], "status": r["status"], "detail": r.get("detail")}
                           for r in run.data["resources"] if r["status"] not in {"removed"}]
    out(report)


def cmd_finish(args):
    repo, cfg, store = ctx(args)
    run = get_run(store, args)
    status = args.status
    if status == "pushed":
        if cfg["git"]["provider"] != "none":
            raise AdlcError("`pushed` is only for git.provider `none`; open a PR instead")
        if run.data["git"].get("push_status") != "pushed":
            raise AdlcError("no verified push recorded; run `adlc push` first")
    if status == "pr_open" and not (run.data.get("pr") or {}).get("verified_at"):
        raise AdlcError("no verified PR recorded; cannot mark pr_open")
    if status == "done" and (run.data.get("pr") or {}).get("state") != "merged":
        raise AdlcError("done requires a PR verified as merged (run `sync`)")
    if status == "soft_run_complete":
        if run.mode != "soft":
            raise AdlcError("soft_run_complete is only for Soft Runs")
        if not run.data.get("cleanup"):
            raise AdlcError("run `adlc cleanup` first; a Soft Run completes only after verified cleanup")
    run.data["status"] = status
    run.data["stage"] = 8
    run.data["next_action"] = {"pr_open": "Run `/asana-adlc sync` after review",
                               "pushed": "Branch delivered to the remote; open a PR by hand if wanted",
                               "done": "Nothing — complete",
                               "soft_run_complete": "Review the Soft Run report; start a full run if wanted",
                               "cancelled": "Nothing — cancelled by the user"}[status]
    run.log(f"finished: {status}")
    store.release(run)
    run.save()
    out({"status": status, "next_action": run.data["next_action"]})


# --- Parser ------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="adlc", description="asana-adlc workflow engine")
    p.add_argument("--repo", default=".", help="path inside the target repository")
    sub = p.add_subparsers(dest="cmd", required=True)

    def task_cmd(name, func, **kw):
        sp = sub.add_parser(name, **kw)
        sp.add_argument("--task", required=True, help="Asana task GID or URL")
        sp.add_argument("--run", help="specific run id (default: latest active)")
        sp.set_defaults(func=func)
        return sp

    sp = sub.add_parser("auth", help="store / check / remove the Asana token (never printed)")
    sp.add_argument("action", choices=["login", "status", "logout"])
    g = sp.add_mutually_exclusive_group(); g.add_argument("--token-stdin", action="store_true"); g.add_argument("--from-env", action="store_true")
    sp.set_defaults(func=cmd_auth)

    sp = sub.add_parser("setup", help="guided, read-only Asana project/task/repository selection")
    sp.add_argument("action", choices=["status", "projects", "select-project", "inspect", "select-task", "repo",
                                       "map", "write-config", "forget"])
    sp.add_argument("--offline", action="store_true"); sp.add_argument("--workspace"); sp.add_argument("--with-sections", action="store_true")
    sp.add_argument("--project"); sp.add_argument("--task"); sp.add_argument("--path"); sp.add_argument("--limit", type=int, default=100)
    sp.add_argument("--eligible"); sp.add_argument("--assignee")
    for flag in ("--in-progress", "--in-review", "--blocked", "--approved", "--merged"):
        sp.add_argument(flag)
    sp.add_argument("--location", choices=["user", "repo"], default="user")
    sp.add_argument("--provider", choices=["github", "gitlab", "none"]); sp.add_argument("--base-branch"); sp.add_argument("--remote")
    sp.add_argument("--all", action="store_true", help="forget: also forget the repository")
    sp.set_defaults(func=cmd_setup)

    sp = sub.add_parser("preflight"); sp.add_argument("--offline", action="store_true"); sp.set_defaults(func=cmd_preflight)
    sp = sub.add_parser("detect"); sp.add_argument("--path"); sp.set_defaults(func=cmd_detect)
    sp = sub.add_parser("guard"); sp.add_argument("--cwd"); sp.add_argument("command", nargs=argparse.REMAINDER); sp.set_defaults(func=cmd_guard)

    sp = sub.add_parser("init")
    sp.add_argument("--task", required=True); sp.add_argument("--mode", choices=["full", "soft"], required=True)
    sp.add_argument("--task-file"); sp.add_argument("--offline", action="store_true")
    sp.add_argument("--take-over-stale", action="store_true"); sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("status"); sp.add_argument("--task"); sp.add_argument("--run"); sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_status)

    sp = task_cmd("approve", lambda a: cmd_decide(a, "approved"))
    sp.add_argument("--gate", required=True); sp.add_argument("--scope", choices=["once", "run"], default="once")
    sp.add_argument("--detail"); sp.add_argument("--note"); sp.add_argument("--soft-run-override", action="store_true")
    sp = task_cmd("reject", lambda a: cmd_decide(a, "rejected"))
    sp.add_argument("--gate", required=True); sp.add_argument("--detail"); sp.add_argument("--note")

    sp = task_cmd("checkpoint", cmd_checkpoint)
    sp.add_argument("--stage", type=int, required=True); sp.add_argument("--done", action="store_true")
    sp.add_argument("--action"); sp.add_argument("--next"); sp.add_argument("--pending", action="append")

    sp = task_cmd("pause", cmd_pause); sp.add_argument("--reason")
    sp = task_cmd("resume", cmd_resume); sp.add_argument("--take-over-stale", action="store_true")
    sp = task_cmd("retry", lambda a: cmd_resume(a, retry=True)); sp.add_argument("--take-over-stale", action="store_true")
    sp = task_cmd("block", cmd_block); sp.add_argument("--reason", required=True); sp.add_argument("--action", required=True)

    sp = task_cmd("workspace", cmd_workspace); sp.add_argument("--offline", action="store_true")
    task_cmd("refresh", cmd_refresh)
    sp = task_cmd("plan", cmd_plan); sp.add_argument("--file", required=True)
    sp.add_argument("--high-impact", action="append"); sp.add_argument("--open-question", action="append")
    task_cmd("changes", cmd_changes)
    task_cmd("snapshot", cmd_snapshot)

    sp = task_cmd("checks", cmd_checks)
    sp.add_argument("action", choices=["list", "add", "from-detect", "from-config", "skip", "classify"])
    sp.add_argument("--name"); sp.add_argument("--command"); sp.add_argument("--optional", action="store_true")
    sp.add_argument("--source"); sp.add_argument("--names"); sp.add_argument("--phase", choices=["baseline", "final"])
    sp.add_argument("--reason"); sp.add_argument("--as", dest="as_", choices=["pre-existing", "introduced"])

    sp = task_cmd("run", cmd_run); sp.add_argument("--check", required=True)
    sp.add_argument("--phase", choices=["baseline", "final"], required=True)
    task_cmd("validation", cmd_validation)
    sp = task_cmd("push", cmd_push); sp.add_argument("--offline", action="store_true")
    sp = task_cmd("pr", cmd_pr); sp.add_argument("--title", required=True); sp.add_argument("--body-file", required=True)
    g = sp.add_mutually_exclusive_group(); g.add_argument("--draft", action="store_true"); g.add_argument("--ready", action="store_true")

    sp = sub.add_parser("asana")
    sp.add_argument("action", choices=["me", "candidates", "lookup", "comment", "status"])
    sp.add_argument("--what", choices=["workspaces", "projects", "sections", "fields", "user"])
    sp.add_argument("--gid", help="workspace gid (projects), project gid (sections/fields), email or gid (user)")
    sp.add_argument("--task"); sp.add_argument("--run"); sp.add_argument("--key"); sp.add_argument("--file")
    sp.add_argument("--stage", choices=["in_progress", "in_review", "blocked", "approved", "merged"])
    sp.set_defaults(func=cmd_asana)

    sp = sub.add_parser("sync"); sp.add_argument("--task"); sp.add_argument("--run"); sp.add_argument("--apply", action="store_true")
    sp.set_defaults(func=cmd_sync)
    sp = task_cmd("cleanup", cmd_cleanup); sp.add_argument("--include-uncertain", action="store_true")
    sp.add_argument("--offline", action="store_true")
    sp = task_cmd("finish", cmd_finish)
    sp.add_argument("--status", choices=["pr_open", "pushed", "done", "soft_run_complete", "cancelled"], required=True)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        code = args.func(args)
        return code or 0
    except AdlcError as e:
        kind = {"AdlcError": "error", "OutcomeUnknown": "outcome-unknown"}.get(
            type(e).__name__, type(e).__name__.replace("Error", "").lower())
        print(f"adlc: {kind}: {e}", file=sys.stderr)
        return e.exit_code
