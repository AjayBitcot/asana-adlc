"""Persistent run record ("ledger").

Layout, under the repository's common git dir so every worktree shares it and
it is never committed:

    <git-common-dir>/asana-adlc/<task_gid>/
        lock/owner.json              present while a run owns the task
        <run_id>/run.json            the run record
        <run_id>/task.json           Asana snapshot (untrusted data)
        <run_id>/logs/               check output, redacted
        <run_id>/evidence/           patches, PR body, reports
"""

import json
import os
import re
import secrets
import socket
import datetime
from pathlib import Path

from . import AdlcError, BlockedError, ExistingRunError
from .util import now

SCHEMA = 2
STAGES = {
    0: "preflight", 1: "intake", 2: "plan", 3: "workspace", 4: "implement",
    5: "validate", 6: "commit", 7: "pull-request", 8: "close-out",
}
ACTIVE = {"active", "paused", "blocked", "awaiting_approval"}
TERMINAL = {"pr_open", "pushed", "done", "cancelled", "soft_run_complete"}


def parse_task_ref(ref):
    """Accept a task GID or an Asana task URL; return the GID."""
    ref = (ref or "").strip()
    if re.fullmatch(r"\d{1,30}", ref):
        return ref
    match = re.search(r"/task/(\d+)", ref) or re.search(r"app\.asana\.com/\d+/[^/\s]+/(\d+)", ref)
    if match:
        return match.group(1)
    raise AdlcError(f"not an Asana task GID or task URL: {ref!r}")


def new_run_id():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(2)


class Run:
    def __init__(self, path, data):
        self.path = Path(path)
        self.data = data

    @property
    def dir(self):
        return self.path.parent

    @property
    def gid(self):
        return self.data["task"]["gid"]

    @property
    def run_id(self):
        return self.data["run_id"]

    @property
    def mode(self):
        return self.data["mode"]

    def save(self):
        self.data["updated_at"] = now()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=False))
        os.replace(tmp, self.path)

    def log(self, event, stage=None, **detail):
        entry = {"at": now(), "stage": self.data["stage"] if stage is None else stage, "event": event}
        entry.update({k: v for k, v in detail.items() if v is not None})
        self.data["history"].append(entry)

    def error(self, message, recovery=None):
        self.data["errors"].append({"at": now(), "stage": self.data["stage"], "message": message})
        if recovery:
            self.data["recovery"] = recovery

    def evidence_dir(self):
        path = self.dir / "evidence"
        path.mkdir(exist_ok=True)
        return path

    def logs_dir(self):
        path = self.dir / "logs"
        path.mkdir(exist_ok=True)
        return path


class Store:
    def __init__(self, repo):
        self.repo = repo
        self.root = repo.common_dir / "asana-adlc"

    def task_dir(self, gid):
        return self.root / gid

    def runs(self, gid):
        out = []
        for path in sorted(self.task_dir(gid).glob("*/run.json")):
            out.append(Run(path, json.loads(path.read_text())))
        return out

    def all_runs(self):
        out = []
        if self.root.exists():
            for task in sorted(p for p in self.root.iterdir() if p.is_dir()):
                out.extend(self.runs(task.name))
        return out

    def get(self, gid, run_id=None, mode=None):
        runs = self.runs(gid)
        if run_id:
            for run in runs:
                if run.run_id == run_id:
                    return run
            raise AdlcError(f"no run {run_id} for task {gid}")
        if mode:
            runs = [r for r in runs if r.mode == mode]
        if not runs:
            raise AdlcError(f"no workflow run recorded for task {gid}")
        active = [r for r in runs if r.data["status"] in ACTIVE]
        return (active or runs)[-1]

    def create(self, gid, mode, task, repo_identity, stack=None):
        for existing in self.runs(gid):
            if existing.data["status"] in ACTIVE and existing.mode == mode:
                raise ExistingRunError(
                    f"task {gid} already has a {mode} run {existing.run_id} "
                    f"(status {existing.data['status']}, stage {existing.data['stage']}). "
                    "Choose: review, continue, retry, or stop."
                )
        run_id = new_run_id()
        run_dir = self.task_dir(gid) / run_id
        run_dir.mkdir(parents=True)
        data = {
            "schema": SCHEMA,
            "run_id": run_id,
            "mode": mode,
            "task": {"gid": gid, "url": task.get("url"), "name": task.get("name")},
            "repo": repo_identity,
            "stack": stack or {},
            "status": "active",
            "stage": 1,
            "last_completed_stage": 0,
            "checkpoints": {"0": {"completed_at": now()}},
            "next_action": "Stage 1: validate the task and confirm intake",
            "completed_actions": [],
            "pending_actions": [],
            "decisions": [],
            "workspace": {},
            "files_changed": [],
            "checks": [],
            "resources": [],
            "git": {"commits": [], "pushed_sha": None, "push_status": "not_pushed"},
            "pr": None,
            "asana": {},
            "validation": None,
            "errors": [],
            "blocker": None,
            "recovery": None,
            "history": [],
            "created_at": now(),
            "updated_at": now(),
        }
        run = Run(run_dir / "run.json", data)
        run.log("run created", stage=0, mode=mode)
        run.save()
        return run

    # --- Lock -----------------------------------------------------------

    def lock_path(self, gid):
        return self.task_dir(gid) / "lock"

    def lock_owner(self, gid):
        owner = self.lock_path(gid) / "owner.json"
        if owner.exists():
            try:
                return json.loads(owner.read_text())
            except json.JSONDecodeError:
                return {"run_id": "?", "at": None}
        return None

    def acquire(self, run, stale_minutes=120, take_over_stale=False):
        lock = self.lock_path(run.gid)
        lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock.mkdir()
        except FileExistsError:
            owner = self.lock_owner(run.gid) or {}
            if owner.get("run_id") == run.run_id:
                return owner
            age = _age_minutes(owner.get("at"))
            if age is not None and age > stale_minutes and take_over_stale:
                run.log("took over stale lock", previous_owner=owner.get("run_id"))
            else:
                state = "stale" if age is not None and age > stale_minutes else "held"
                raise BlockedError(
                    f"task {run.gid} is locked by run {owner.get('run_id')} ({state}, "
                    f"{'?' if age is None else int(age)} min old). "
                    + ("Take over only with the user's confirmation (--take-over-stale)." if state == "stale"
                       else "Another run is processing this task.")
                )
        owner = {"run_id": run.run_id, "pid": os.getpid(), "host": socket.gethostname(), "at": now()}
        (lock / "owner.json").write_text(json.dumps(owner))
        return owner

    def release(self, run):
        lock = self.lock_path(run.gid)
        owner = self.lock_owner(run.gid)
        if owner and owner.get("run_id") != run.run_id:
            return False
        (lock / "owner.json").unlink(missing_ok=True)
        try:
            lock.rmdir()
        except FileNotFoundError:
            pass
        return True


def _age_minutes(stamp):
    if not stamp:
        return None
    then = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    return (datetime.datetime.now(datetime.timezone.utc) - then).total_seconds() / 60


def checkpoint(run, stage, done=False, action=None, next_action=None, pending=None):
    stage = int(stage)
    if stage not in STAGES:
        raise AdlcError(f"unknown stage {stage}")
    if action:
        run.data["completed_actions"].append({"at": now(), "stage": stage, "action": action})
    if pending is not None:
        run.data["pending_actions"] = list(pending)
    if done:
        run.data["checkpoints"][str(stage)] = {"completed_at": now()}
        run.data["last_completed_stage"] = max(run.data["last_completed_stage"], stage)
        run.data["stage"] = min(stage + 1, 8) if stage < 8 else 8
        run.log(f"stage {stage} ({STAGES[stage]}) completed", stage=stage)
    else:
        run.data["stage"] = stage
        run.log(f"stage {stage} ({STAGES[stage]}) started", stage=stage)
    if next_action:
        run.data["next_action"] = next_action
    if run.data["status"] in {"paused", "blocked", "awaiting_approval"} and not done:
        run.data["status"] = "active"
