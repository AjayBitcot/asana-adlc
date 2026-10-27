"""Isolated workspaces, push, and verified cleanup.

All work happens in a git worktree the workflow creates outside the
repository, so the user's checkout (its branch, uncommitted changes, untracked
files) is never touched. Every resource created is registered in the run
record with an ownership marker, and cleanup removes only resources it can
prove it owns and that hold nothing that would be lost.
"""

import fnmatch
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from . import AdlcError, BlockedError, GateError
from . import gates
from .util import git, git_try, is_within, now, short_hash
from .util import run as run_cmd

MARKER = ".adlc-owner.json"
SOFT_PREFIX = "adlc-soft/"


def branch_name(gid, task_name, prefix="feature/"):
    slug = re.sub(r"[^a-z0-9]+", "-", (task_name or "").lower()).strip("-")[:40].strip("-") or "task"
    name = f"{prefix}ASANA-{gid}-{slug}"
    if git_try(".", "check-ref-format", "--branch", name)[0] != 0:
        raise AdlcError(f"generated branch name is invalid: {name}")
    return name


def is_protected(branch, cfg):
    patterns = list(cfg["git"].get("protected_branches", [])) + [cfg["git"]["base_branch"]]
    return any(fnmatch.fnmatch(branch, p) for p in patterns)


def tmp_root(cfg, repo):
    base = Path(cfg["isolation"].get("tmp_root") or tempfile.gettempdir()).expanduser().resolve()
    root = base / "asana-adlc" / short_hash(str(repo.root))
    if is_within(root, repo.root):
        raise AdlcError("isolation.tmp_root must be outside the repository")
    return root


def _register(run, rtype, ref, keep=False, **extra):
    res = {"id": f"{rtype}:{ref}", "type": rtype, "ref": str(ref), "run_id": run.run_id, "keep": keep,
           "created_at": now(), "status": "active", "detail": None}
    res.update(extra)
    run.data["resources"] = [r for r in run.data["resources"] if r["id"] != res["id"]] + [res]
    run.log(f"registered {rtype}", ref=str(ref))
    return res


def _worktrees(repo):
    _, out = git_try(repo.root, "worktree", "list", "--porcelain")
    return {Path(line[9:]).resolve() for line in out.splitlines() if line.startswith("worktree ")}


def _branch_exists(repo, branch):
    return git_try(repo.root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")[0] == 0


def _remote_sha(repo, remote, branch):
    code, out = git_try(repo.root, "ls-remote", "--heads", remote, f"refs/heads/{branch}")
    if code != 0:
        raise BlockedError(f"cannot reach remote `{remote}`: check network and credentials")
    return out.split()[0] if out else None


def create(store, run, cfg, offline=False):
    """Create (or reuse) the run's worktree. Gate: workspace."""
    repo = store.repo
    ws = run.data["workspace"]
    if ws.get("worktree") and Path(ws["worktree"]).resolve() in _worktrees(repo):
        return {"status": "existing", **ws}

    # Implementation happens in the worktree, so it cannot exist before the plan is approved.
    gates.require(run, cfg, "plan", consume=False)
    gates.require(run, cfg, "workspace")
    remote, base = cfg["git"]["remote"], cfg["git"]["base_branch"]
    if offline:
        base_ref = base
    else:
        git(repo.root, "fetch", "--quiet", remote, f"refs/heads/{base}:refs/remotes/{remote}/{base}")
        base_ref = f"{remote}/{base}"
    code, base_sha = git_try(repo.root, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    if code != 0:
        raise BlockedError(f"base branch `{base_ref}` not found")

    reuse = False
    if run.mode == "full":
        branch = branch_name(run.gid, run.data["task"].get("name"), cfg["git"]["branch_prefix"])
        if is_protected(branch, cfg):
            raise BlockedError(f"branch {branch} matches a protected branch pattern")
        owned = any(r.data.get("workspace", {}).get("branch") == branch for r in store.runs(run.gid))
        local = _branch_exists(repo, branch)
        remote_has = False if offline else _remote_sha(repo, remote, branch) is not None
        if (local or remote_has) and not owned:
            raise BlockedError(
                f"branch `{branch}` already exists ({'local' if local else 'remote'}) and was not created by this "
                "workflow — it may be someone's work. Leaving it untouched."
            )
        reuse = local
        if not local and remote_has:
            git(repo.root, "fetch", "--quiet", remote, f"refs/heads/{branch}:refs/heads/{branch}")
            reuse = True
    else:
        branch = f"{SOFT_PREFIX}{run.gid}-{run.run_id}"
        if _branch_exists(repo, branch):
            raise BlockedError(f"soft-run branch {branch} already exists")

    parent = tmp_root(cfg, repo) / f"{run.gid}-{run.run_id}"
    if parent.exists():
        raise BlockedError(f"workspace path already exists: {parent}")
    parent.mkdir(parents=True)
    (parent / MARKER).write_text(json.dumps({"run_id": run.run_id, "task": run.gid, "repo": str(repo.root), "at": now()}))
    _register(run, "dir", parent)
    worktree = parent / "wt"
    try:
        if reuse:
            git(repo.root, "worktree", "add", "--quiet", str(worktree), branch)
        else:
            git(repo.root, "worktree", "add", "--quiet", "-b", branch, str(worktree), base_sha)
    except AdlcError as e:
        run.error(str(e))
        raise BlockedError(f"could not create worktree: {e}")
    _register(run, "worktree", worktree)
    if not reuse:
        # A full-mode branch is the deliverable and is never deleted by cleanup.
        _register(run, "branch", branch, keep=(run.mode == "full"), created_sha=base_sha)

    head = git(worktree, "rev-parse", "HEAD")
    if not reuse and head != base_sha:
        raise BlockedError(f"worktree HEAD {head} does not match base {base_sha}")
    ws.update({"base_branch": base, "base_ref": base_ref, "base_sha": base_sha, "branch": branch,
               "worktree": str(worktree), "reused_branch": reuse, "created_at": now()})
    run.log("workspace ready", branch=branch, worktree=str(worktree))
    return {"status": "created", **ws}


def worktree_path(run):
    path = run.data["workspace"].get("worktree")
    if not path or not Path(path).is_dir():
        raise AdlcError("no workspace for this run yet (run `adlc workspace create` after approval)")
    return Path(path)


def _diff_including_untracked(wt, base, *args):
    """Diff base..working tree, untracked files included, via a throwaway index."""
    git_dir = Path(git(wt, "rev-parse", "--absolute-git-dir"))
    with tempfile.TemporaryDirectory() as tmp:
        index = Path(tmp) / "index"
        if (git_dir / "index").exists():
            shutil.copy(git_dir / "index", index)
        env = {**os.environ, "GIT_INDEX_FILE": str(index)}
        run_cmd(["git", "add", "-A"], cwd=wt, env=env)
        return run_cmd(["git", "diff", "--cached", *args, base], cwd=wt, env=env).stdout


def record_changes(run):
    """Record changed files and save a patch of all work as evidence."""
    wt = worktree_path(run)
    base = run.data["workspace"]["base_sha"]
    names = _diff_including_untracked(wt, base, "--name-status")
    run.data["files_changed"] = [
        {"status": line.split("\t")[0], "path": line.split("\t")[-1]} for line in names.splitlines() if line.strip()
    ]
    patch = _diff_including_untracked(wt, base, "--binary")
    (run.evidence_dir() / "changes.patch").write_text(patch)
    return run.data["files_changed"]


def snapshot(run):
    """Soft Run: commit all work on the temporary branch so cleanup is lossless."""
    if run.mode != "soft":
        raise AdlcError("snapshot is only for Soft Runs; full runs commit with the repository's conventions")
    wt = worktree_path(run)
    files = record_changes(run)
    git(wt, "add", "-A")
    if git_try(wt, "diff", "--cached", "--quiet")[0] != 0:
        git(wt, "-c", "user.name=asana-adlc", "-c", "user.email=asana-adlc@localhost", "commit", "--quiet",
            "--no-verify", "-m", f"adlc soft-run snapshot (ASANA-{run.gid}, run {run.run_id})")
    sha = git(wt, "rev-parse", "HEAD")
    run.data["workspace"]["snapshot_sha"] = sha
    for res in run.data["resources"]:
        if res["type"] == "branch":
            res["expected_sha"] = sha
    run.log("soft-run snapshot", sha=sha)
    return {"snapshot_sha": sha, "files_changed": files}


def push(store, run, cfg, validation_state):
    """Push the task branch (never forced). Gate: push."""
    repo = store.repo
    wt = worktree_path(run)
    branch = run.data["workspace"]["branch"]
    remote = cfg["git"]["remote"]
    if is_protected(branch, cfg):
        raise BlockedError(f"refusing to push protected branch {branch}")
    if validation_state not in {"ready", "draft"}:
        raise BlockedError(f"validation is `{validation_state}`; mandatory checks must pass (or be explicitly "
                           "classified pre-existing) before pushing")
    if git(wt, "status", "--porcelain"):
        raise BlockedError("worktree has uncommitted changes; commit them before pushing")
    head = git(wt, "rev-parse", "HEAD")
    remote_sha = _remote_sha(repo, remote, branch)
    if remote_sha == head:
        run.data["git"].update({"pushed_sha": head, "push_status": "pushed"})
        return {"status": "already-pushed", "sha": head}
    gates.require(run, cfg, "push", detail=branch)
    if remote_sha:
        git_try(repo.root, "fetch", "--quiet", remote, f"refs/heads/{branch}")
        if git_try(wt, "merge-base", "--is-ancestor", remote_sha, head)[0] != 0:
            raise BlockedError(f"remote branch {branch} has commits not in the local branch; not force-pushing")
    run.data["git"]["push_status"] = "pushing"
    run.save()
    code, _ = git_try(wt, "push", "--quiet", "-u", remote, f"{branch}:refs/heads/{branch}")
    after = _remote_sha(repo, remote, branch)
    if after != head:
        run.data["git"]["push_status"] = "failed"
        raise BlockedError(f"push did not land (exit {code}); remote has {after or 'nothing'}")
    base = run.data["workspace"]["base_sha"]
    commits = git(wt, "log", "--format=%H", f"{base}..{head}").split()
    run.data["git"].update({"pushed_sha": head, "push_status": "pushed", "commits": commits})
    run.log("pushed", branch=branch, sha=head)
    return {"status": "pushed", "sha": head, "commits": commits}


# --- Cleanup -----------------------------------------------------------------

def _assess(store, run, cfg, res):
    """Return (certain: bool, reason) for removing one resource."""
    repo = store.repo
    if res.get("run_id") != run.run_id:
        return False, "registered by a different run"
    if res["type"] == "dir":
        path = Path(res["ref"])
        if not path.exists():
            return True, "already gone"
        if not is_within(path, tmp_root(cfg, repo)):
            return False, "outside the workflow's temp root"
        marker = path / MARKER
        if not marker.exists() or json.loads(marker.read_text()).get("run_id") != run.run_id:
            return False, "ownership marker missing or belongs to another run"
        # Removable only once everything inside was itself removed: a kept or
        # uncertain worktree must never disappear with its parent.
        leftovers = [p.name for p in path.iterdir() if p.name != MARKER]
        if leftovers:
            return False, f"still contains {', '.join(leftovers[:5])} (remove or review those first)"
        return True, "owned temp directory, empty apart from its marker"
    if res["type"] == "worktree":
        path = Path(res["ref"]).resolve()
        if path not in _worktrees(repo):
            return (True, "already gone") if not path.exists() else (False, "not a registered git worktree")
        marker = path.parent / MARKER
        if not marker.exists() or json.loads(marker.read_text()).get("run_id") != run.run_id:
            return False, "ownership marker missing or belongs to another run"
        if git(path, "status", "--porcelain"):
            return False, "has uncommitted changes"
        if run.mode == "soft":
            expected = run.data["workspace"].get("snapshot_sha") or run.data["workspace"].get("base_sha")
            if git(path, "rev-parse", "HEAD") != expected:
                return False, "HEAD moved since the snapshot (someone else may have committed)"
        return True, "owned worktree, no uncommitted work"
    if res["type"] == "branch":
        if res.get("keep"):
            return False, "deliverable branch (kept)"
        branch = res["ref"]
        if not branch.startswith(SOFT_PREFIX):
            return False, "only temporary soft-run branches are ever deleted"
        code, sha = git_try(repo.root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        if code != 0:
            return True, "already gone"
        expected = res.get("expected_sha") or res.get("created_sha")
        if sha != expected:
            return False, f"tip {sha[:10]} differs from the recorded {str(expected)[:10]}"
        if branch in _worktree_branches(repo):
            return False, "checked out in a worktree"
        if not cfg.get("_offline"):
            remote = cfg["git"]["remote"]
            if git_try(repo.root, "ls-remote", "--heads", remote, f"refs/heads/{branch}")[1]:
                return False, "exists on the remote"
        return True, "temporary branch at its recorded tip"
    return False, f"type `{res['type']}` is never removed automatically"


def _worktree_branches(repo):
    _, out = git_try(repo.root, "worktree", "list", "--porcelain")
    mapping, current = {}, None
    for line in out.splitlines():
        if line.startswith("worktree "):
            current = line[9:]
        elif line.startswith("branch refs/heads/"):
            mapping.setdefault(line[18:], []).append(current)
    return mapping


def _remove(store, run, res):
    repo = store.repo
    if res["type"] == "worktree":
        path = Path(res["ref"])
        if path.exists():
            if git_try(path, "status", "--porcelain")[1]:
                # Approved removal of a dirty worktree: preserve its contents first.
                git(path, "add", "-A")
                _, patch = git_try(path, "diff", "--cached", "--binary")
                out = run.evidence_dir() / f"uncommitted-{now().replace(':', '')}.patch"
                out.write_text(patch + "\n")
                res["saved_patch"] = str(out)
            git(repo.root, "worktree", "remove", "--force", str(path))
        return Path(res["ref"]).resolve() not in _worktrees(repo) and not path.exists()
    if res["type"] == "branch":
        branch = res["ref"]
        code, sha = git_try(repo.root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        if code == 0:
            res["deleted_sha"] = sha
            git(repo.root, "branch", "-D", branch)
        return not _branch_exists(repo, branch)
    if res["type"] == "dir":
        path = Path(res["ref"])
        if path.exists():
            shutil.rmtree(path)
        return not path.exists()
    return False


def cleanup(store, run, cfg, include_uncertain=False):
    """Remove workflow-owned temporary resources; report everything else."""
    report = {"removed": [], "kept": [], "needs_approval": [], "failed": []}
    order = {"worktree": 0, "branch": 1, "dir": 2}
    for res in sorted(run.data["resources"], key=lambda r: order.get(r["type"], 9)):
        if res["status"] in {"removed", "kept"}:
            continue
        certain, reason = _assess(store, run, cfg, res)
        if res.get("keep"):
            res["status"], res["detail"] = "kept", reason
            report["kept"].append({"id": res["id"], "reason": reason})
            continue
        if not certain:
            # A deliverable or another run's resource is never removable, even with approval.
            hard_stop = reason.startswith(("registered by a different run", "outside the workflow",
                                           "only temporary", "type `", "exists on the remote",
                                           "still contains", "ownership marker"))
            if include_uncertain and not hard_stop:
                try:
                    gates.require(run, cfg, "cleanup-uncertain", detail=res["id"])
                except GateError as e:
                    report["needs_approval"].append({"id": res["id"], "reason": reason, "gate": str(e)})
                    continue
            else:
                res["detail"] = reason
                report["needs_approval" if not hard_stop else "kept"].append({"id": res["id"], "reason": reason})
                continue
        try:
            ok = _remove(store, run, res)
        except AdlcError as e:
            ok, reason = False, str(e)
        res["status"] = "removed" if ok else "failed"
        res["detail"] = reason if ok else f"removal not verified: {reason}"
        res["removed_at"] = now() if ok else None
        (report["removed"] if ok else report["failed"]).append({"id": res["id"], "reason": res["detail"]})
        run.log("cleanup " + res["status"], ref=res["ref"])
    run.data["cleanup"] = {"at": now(), **{k: len(v) for k, v in report.items()}}
    return report
