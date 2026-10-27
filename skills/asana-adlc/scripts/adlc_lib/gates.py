"""Approval gates.

Every action with effects beyond the workflow's own sandbox calls `require()`.
It passes only when the user's latest recorded decision for that gate is an
unconsumed approval (or a narrowly configured unattended rule applies). A
rejection always wins over an earlier approval.
"""

import os

from . import AdlcError, GateError
from .util import now

GATES = {
    "intake": "Claim the task and start the workflow",
    "plan": "Start implementation from the approved plan",
    "workspace": "Create the branch / worktree in the repository",
    "environment": "Install dependencies, run migrations, or other environment-changing commands",
    "side-effect-tests": "Run commands that may change data, touch services, or cannot be verified as side-effect free",
    "destructive": "A destructive operation",
    "production": "An operation that targets a production environment",
    "push": "Push the task branch to the remote",
    "pr": "Create or update the Pull Request",
    "asana-comment": "Post progress comments on the Asana task",
    "asana-status": "Change the Asana task's status / section",
    "asana-sync": "Apply review-state updates from the Git provider to Asana",
    "cleanup-uncertain": "Remove a resource whose ownership or contents are uncertain",
    "corrective-change": "Make a significant corrective change after a failure",
}

# Gates that touch something outside the local sandbox; a Soft Run needs an
# explicit, separate approval (soft_run_override) for each.
REMOTE_GATES = {"push", "pr", "asana-comment", "asana-status", "asana-sync"}

# Never satisfied by unattended configuration — always a human decision.
NEVER_UNATTENDED = {"destructive", "production", "cleanup-uncertain", "corrective-change"}


def record(run, gate, decision, scope="once", detail=None, by="user", soft_run_override=False, note=None):
    if gate not in GATES:
        raise AdlcError(f"unknown gate {gate!r}; known: {', '.join(sorted(GATES))}")
    if decision not in {"approved", "rejected"}:
        raise AdlcError("decision must be approved or rejected")
    if scope not in {"once", "run"}:
        raise AdlcError("scope must be once or run")
    entry = {
        "gate": gate, "decision": decision, "scope": scope, "detail": detail, "by": by,
        "soft_run_override": bool(soft_run_override), "note": note, "at": now(), "consumed": False,
    }
    run.data["decisions"].append(entry)
    run.log(f"{gate} {decision}", detail=detail, by=by)
    if decision == "rejected":
        run.data["status"] = "paused"
        run.data["next_action"] = f"User rejected `{gate}`{f' ({detail})' if detail else ''}; ask how to proceed"
    return entry


def unattended_active(cfg):
    return bool(cfg.get("unattended", {}).get("enabled")) and os.environ.get("ASANA_ADLC_UNATTENDED") == "1"


def _matches(entry, gate, detail):
    if entry["gate"] != gate:
        return False
    # An approval scoped to a specific detail (command, stage, key) covers only that.
    return not entry.get("detail") or detail is None or entry["detail"] == detail


def require(run, cfg, gate, detail=None, consume=True):
    """Return the approval covering this action, or raise GateError."""
    if gate not in GATES:
        raise AdlcError(f"unknown gate {gate!r}")
    soft = run.mode == "soft" and gate in REMOTE_GATES

    for entry in reversed(run.data["decisions"]):
        if not _matches(entry, gate, detail):
            continue
        if entry["decision"] == "rejected":
            raise GateError(f"`{gate}` was rejected by the user{f' ({detail})' if detail else ''}; not proceeding")
        if entry["consumed"]:
            continue
        if soft and not entry.get("soft_run_override"):
            continue
        if entry["scope"] == "once" and consume:
            entry["consumed"] = True
            entry["used_at"] = now()
        return entry

    if soft:
        raise GateError(
            f"Soft Run: `{gate}` is a remote action and needs its own explicit approval "
            "(record it with --soft-run-override after the user agrees)"
        )
    if unattended_active(cfg) and gate not in NEVER_UNATTENDED:
        rules = cfg["unattended"]
        allowed = gate in rules.get("allowed_gates", []) or (
            gate in {"environment", "side-effect-tests"} and detail in rules.get("allowed_commands", [])
        )
        if gate == "plan" and ((run.data.get("plan") or {}).get("high_impact") or not run.data.get("plan")):
            allowed = False
        if allowed:
            entry = record(run, gate, "approved", scope="once", detail=detail, by="unattended-config")
            entry["consumed"] = True
            return entry
    raise GateError(f"approval required: `{gate}` — {GATES[gate]}{f' ({detail})' if detail else ''}")


def pending_gate_summary(run):
    out = []
    for entry in run.data["decisions"]:
        state = "rejected" if entry["decision"] == "rejected" else ("used" if entry["consumed"] else "available")
        out.append(f"{entry['gate']}{f' [{entry['detail']}]' if entry.get('detail') else ''}: "
                   f"{entry['decision']} ({entry['scope']}, {state}) by {entry['by']} at {entry['at']}")
    return out
