"""Git hosting providers. PR state always comes from the provider, never from
the run record alone; creation is guarded against duplicates."""

import json
import re
import shutil
from pathlib import Path

from . import AdlcError, BlockedError, ConfigError
from . import gates
from .util import git_try, now, redact, run as run_cmd


class Provider:
    def __init__(self, kind, cwd):
        self.kind, self.cwd = kind, cwd
        self.cli = {"github": "gh", "gitlab": "glab"}.get(kind)

    def available(self):
        if self.kind == "none":
            return False, "git.provider is `none`: PRs are not created by this workflow"
        if not shutil.which(self.cli):
            return False, f"`{self.cli}` is not installed"
        result = run_cmd([self.cli, "auth", "status"], cwd=self.cwd, check=False)
        if result.returncode != 0:
            return False, f"`{self.cli} auth status` failed — run `{self.cli} auth login`"
        return True, "authenticated"

    def _json(self, args):
        if self.kind == "none":
            raise ConfigError("no Git provider configured")
        result = run_cmd([self.cli, *args], cwd=self.cwd)
        try:
            return json.loads(result.stdout or "null")
        except json.JSONDecodeError:
            raise AdlcError(f"unexpected `{self.cli}` output: {result.stdout[:200]}")

    def find(self, branch):
        if self.kind == "github":
            rows = self._json(["pr", "list", "--head", branch, "--state", "all", "--json", "number,url,state,isDraft"]) or []
            return [{"number": r["number"], "url": r["url"], "state": r["state"].lower(), "draft": r.get("isDraft", False)}
                    for r in rows]
        rows = self._json(["mr", "list", "--source-branch", branch, "--all", "--output", "json"]) or []
        return [{"number": r["iid"], "url": r["web_url"], "state": {"opened": "open"}.get(r["state"], r["state"]),
                 "draft": r.get("draft", False)} for r in rows]

    def view(self, ref):
        if self.kind == "github":
            r = self._json(["pr", "view", str(ref), "--json",
                            "number,url,state,isDraft,reviewDecision,mergedAt,mergeCommit,headRefName,baseRefName"])
            review = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes_requested"}.get(r.get("reviewDecision") or "", "none")
            return {"number": r["number"], "url": r["url"], "state": r["state"].lower(), "draft": r.get("isDraft", False),
                    "review": review, "merged_at": r.get("mergedAt"),
                    "merge_sha": (r.get("mergeCommit") or {}).get("oid"), "head": r.get("headRefName"),
                    "base": r.get("baseRefName")}
        r = self._json(["mr", "view", str(ref), "--output", "json"])
        return {"number": r["iid"], "url": r["web_url"], "state": {"opened": "open"}.get(r["state"], r["state"]),
                "draft": r.get("draft", False), "review": "approved" if r.get("approved") else "none",
                "merged_at": r.get("merged_at"), "merge_sha": r.get("merge_commit_sha"),
                "head": r.get("source_branch"), "base": r.get("target_branch")}

    def create(self, base, head, title, body_file, draft, reviewers):
        if self.kind == "github":
            args = ["pr", "create", "--base", base, "--head", head, "--title", title, "--body-file", str(body_file)]
            if draft:
                args.append("--draft")
            if reviewers:
                args += ["--reviewer", ",".join(reviewers)]
        else:
            args = ["mr", "create", "--source-branch", head, "--target-branch", base, "--title", title,
                    "--description", Path(body_file).read_text(), "--yes"]
            if draft:
                args.append("--draft")
            if reviewers:
                args += ["--reviewer", ",".join(reviewers)]
        out = run_cmd([self.cli, *args], cwd=self.cwd).stdout
        match = re.findall(r"https?://\S+/(?:pull|merge_requests)/\d+", out)
        if not match:
            raise AdlcError(f"`{self.cli}` did not return a PR URL")
        return match[-1]


def clean_title(title):
    title = re.sub(r"[\r\n`]+", " ", title or "")
    return re.sub(r"\s+", " ", title).strip()[:250]


def create_pr(store, run, cfg, title, body_file, validation_state, draft=None):
    """Create the PR once. Gate: pr. Returns the verified PR record."""
    if cfg["git"]["provider"] == "none":
        raise ConfigError("git.provider is `none`: this workflow does not open PRs — finish with "
                          "`adlc finish --status pushed`")
    prov = Provider(cfg["git"]["provider"], run.data["workspace"].get("worktree") or store.repo.root)
    branch = run.data["workspace"].get("branch")
    base = cfg["git"]["base_branch"]
    if not branch:
        raise AdlcError("no task branch recorded for this run")
    if validation_state in {"failing", "incomplete"}:
        raise BlockedError(f"validation is `{validation_state}`; not opening a PR")

    existing = prov.find(branch)
    open_prs = [p for p in existing if p["state"] == "open"]
    if open_prs:
        run.data["pr"] = {**prov.view(open_prs[0]["url"]), "verified_at": now(), "created_by_run": False}
        return {"status": "existing", **run.data["pr"]}
    if existing:
        raise BlockedError(f"a PR for {branch} was already {existing[0]['state']} ({existing[0]['url']}); "
                           "a new one needs the user's decision")

    pushed = run.data["git"].get("pushed_sha")
    code, remote = git_try(store.repo.root, "ls-remote", "--heads", cfg["git"]["remote"], f"refs/heads/{branch}")
    if not pushed or code != 0 or not remote.startswith(pushed):
        raise BlockedError("the branch is not pushed at the recorded commit; push first")

    gates.require(run, cfg, "pr", detail=branch)
    body_path = run.evidence_dir() / "pr-body.md"
    body_path.write_text(redact(Path(body_file).read_text()))
    draft = (validation_state == "draft") if draft is None else draft
    run.data["pr"] = {"status": "creating", "at": now()}
    run.save()
    try:
        url = prov.create(base, branch, clean_title(title), body_path, draft, cfg["git"].get("reviewers") or [])
    except AdlcError:
        again = [p for p in prov.find(branch) if p["state"] == "open"]
        if not again:
            run.data["pr"] = None
            raise
        url = again[0]["url"]
    run.data["pr"] = {**prov.view(url), "verified_at": now(), "created_by_run": True}
    run.log("pull request opened", url=url, draft=draft)
    return {"status": "created", **run.data["pr"]}
