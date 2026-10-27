"""Validation checks: registry, guarded execution in the workspace, and the
validation state that decides whether a branch may be pushed."""

import os
import subprocess
import time

from . import AdlcError, BlockedError
from . import gates, guard
from .util import now, redact
from .workspace import worktree_path

# Credentials the workflow holds are never handed to project commands.
WITHHELD_ENV = {"ASANA_ACCESS_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "GITLAB_TOKEN", "GLAB_TOKEN",
                "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}


def find(run, name):
    for check in run.data["checks"]:
        if check["name"] == name:
            return check
    raise AdlcError(f"no check named {name!r}; add it with `adlc checks add`")


def add(run, name, command, mandatory=True, source="user", cwd="."):
    verdict = guard.classify(command, cwd)
    existing = [c for c in run.data["checks"] if c["name"] == name]
    if existing and existing[0]["command"] != command and (existing[0].get("baseline") or existing[0].get("final")):
        raise AdlcError(f"check {name!r} already ran with a different command; use a new name")
    entry = existing[0] if existing else {"name": name, "baseline": None, "final": None, "classification": None}
    entry.update({"command": command, "mandatory": bool(mandatory), "source": source,
                  "class": verdict["class"], "gate": verdict["gate"], "reasons": verdict["reasons"]})
    if not existing:
        run.data["checks"].append(entry)
    return entry


def execute(run, cfg, name, phase):
    """Run one registered check in the run's worktree; record a redacted log."""
    if phase not in {"baseline", "final"}:
        raise AdlcError("phase must be baseline or final")
    check = find(run, name)
    wt = worktree_path(run)
    command = check["command"]

    verdict = guard.classify(command, wt)  # re-classify against the worktree's actual scripts
    env = {k: v for k, v in os.environ.items() if k not in WITHHELD_ENV}
    env.update({k: str(v) for k, v in (cfg["isolation"].get("env") or {}).items()})
    env["ASANA_ADLC_RUN_ID"] = run.run_id
    prod = guard.production_env(env)
    if prod:
        verdict["class"], verdict["gate"] = "production", "production"
        verdict["reasons"].append(f"environment points at production: {', '.join(prod)}")
    if verdict["class"] == "production" and run.mode == "soft":
        raise BlockedError("Soft Run never runs commands against production: " + "; ".join(verdict["reasons"]))
    if verdict["gate"]:
        gates.require(run, cfg, verdict["gate"], detail=command)

    seq = len(list(run.logs_dir().glob(f"{phase}-{name}-*.log"))) + 1
    log_path = run.logs_dir() / f"{phase}-{name}-{seq}.log"
    started = time.monotonic()
    timeout = int(cfg["isolation"].get("timeout_seconds") or 1800)
    try:
        proc = subprocess.run(["bash", "-c", command], cwd=wt, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, timeout=timeout)
        output, code = proc.stdout, proc.returncode
        status = "pass" if code == 0 else "fail"
    except subprocess.TimeoutExpired as e:
        output = (e.stdout or "") if isinstance(e.stdout, str) else (e.stdout or b"").decode(errors="replace")
        code, status = None, "timeout"
    log_path.write_text(redact(output, env=os.environ))
    result = {"status": status, "exit": code, "log": str(log_path), "at": now(),
              "seconds": round(time.monotonic() - started, 1), "class": verdict["class"]}
    check[phase] = result
    if phase == "final":
        check["classification"] = None  # a new final result invalidates an old classification
    run.log(f"check {name} ({phase}) {status}", exit=code)
    tail = redact("\n".join(output.splitlines()[-40:]), env=os.environ)
    return {**result, "name": name, "tail": tail}


def skip(run, name, phase, reason):
    check = find(run, name)
    check[phase] = {"status": "not_run", "reason": reason, "at": now()}
    run.log(f"check {name} ({phase}) not run", reason=reason)
    return check


def classify(run, name, as_, reason):
    """Agent's recorded analysis of a final failure."""
    check = find(run, name)
    if as_ == "pre-existing":
        base = (check.get("baseline") or {}).get("status")
        if base not in {"fail", "timeout"}:
            raise AdlcError(f"{name}: cannot be pre-existing — baseline was {base or 'not run'}")
    if as_ not in {"pre-existing", "introduced"}:
        raise AdlcError("classification must be pre-existing or introduced")
    check["classification"] = {"as": as_, "reason": reason, "at": now()}
    return check


def validation_state(run):
    """ready | draft | failing | incomplete, with per-check detail."""
    state, details = "ready", []
    rank = {"ready": 0, "draft": 1, "incomplete": 2, "failing": 3}

    def worsen(new):
        nonlocal state
        if rank[new] > rank[state]:
            state = new

    if not run.data["checks"]:
        worsen("draft")
        details.append("no checks registered — no automated validation")
    for check in run.data["checks"]:
        final = (check.get("final") or {}).get("status")
        label = check["name"] + ("" if check["mandatory"] else " (optional)")
        if final == "pass":
            details.append(f"{label}: pass")
        elif final is None:
            if check["mandatory"]:
                worsen("incomplete")
            details.append(f"{label}: not run yet")
        elif final == "not_run":
            if check["mandatory"]:
                worsen("draft")
            details.append(f"{label}: not run — {check['final'].get('reason')}")
        else:
            cls = (check.get("classification") or {}).get("as")
            if not check["mandatory"]:
                details.append(f"{label}: {final} (does not block)")
            elif cls == "pre-existing":
                worsen("draft")
                details.append(f"{label}: {final}, pre-existing — {check['classification']['reason']}")
            else:
                worsen("failing")
                details.append(f"{label}: {final}" + (" (introduced)" if cls == "introduced" else " (unclassified)"))
    run.data["validation"] = {"state": state, "details": details, "at": now()}
    return state, details
