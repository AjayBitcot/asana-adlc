"""End-to-end workflow scenarios, driven through the real `adlc` CLI against a
disposable repository, a local bare remote, a fake Asana API, and stub gh/glab."""

import json
import unittest
from pathlib import Path

from helpers import TOKEN, WorkflowCase, sh


class Scenario01FullSuccess(WorkflowCase):
    def test_task_to_pr_to_merged(self):
        T = "1001"
        pre = self.adlc("preflight")
        self.assertTrue(pre["ok"], pre)
        self.assertEqual(pre["asana_user"]["email"], "dev@example.com")

        intake = self.adlc("init", "--task", f"https://app.asana.com/0/p1/{T}", "--mode", "full")
        self.assertTrue(intake["intake"]["ok"], intake)
        self.assertEqual(len(intake["intake"]["criteria"]), 2)
        self.assertIn("python", intake["stack"]["stacks"])

        # Stage 1: claim in Asana (approved by the user as one grouped step)
        self.approve(T, "asana-comment", "--scope", "run")
        self.approve(T, "asana-status", "--detail", "in_progress")
        Path(self.tmp / "c.txt").write_text("picked up")
        self.assertEqual(self.adlc("asana", "comment", "--task", T, "--key", "claimed", "--file", str(self.tmp / "c.txt"))["status"], "done")
        self.assertEqual(self.adlc("asana", "status", "--task", T, "--stage", "in_progress")["status"], "done")
        self.assertEqual(self.asana.section_of(T), ["s-progress"])
        self.adlc("checkpoint", "--task", T, "--stage", "1", "--done")

        # Stage 2-3: plan approved, workspace created, baseline
        self.approve(T, "plan")
        self.adlc("checkpoint", "--task", T, "--stage", "2", "--done")
        self.adlc("workspace", "--task", T, expect=3)  # not yet approved
        self.approve(T, "workspace")
        ws = self.adlc("workspace", "--task", T)
        wt = Path(ws["worktree"])
        self.assertTrue(ws["branch"].startswith(f"feature/ASANA-{T}-add-greeting-helper"))
        self.assertTrue(str(wt).startswith(str(self.tmp_root)))
        checks = self.adlc("checks", "--task", T, "from-detect")
        self.assertEqual(checks[0]["command"], "python3 -m unittest discover -s tests")
        self.assertEqual(self.adlc("run", "--task", T, "--check", "tests", "--phase", "baseline")["status"], "pass")

        # Stage 4-5: implement and validate
        self.implement_greet(wt)
        self.assertEqual(self.adlc("run", "--task", T, "--check", "tests", "--phase", "final")["status"], "pass")
        self.assertEqual(self.adlc("validation", "--task", T)["state"], "ready")
        changes = self.adlc("changes", "--task", T)
        self.assertEqual({f["path"] for f in changes["files_changed"]}, {"greet.py", "tests/test_greet.py"})

        # Stage 6: commit (repo conventions, explicit paths) then push
        sh("git add greet.py tests/test_greet.py && git commit -qm 'feat: greet helper (ASANA-1001)'", wt, self.env)
        self.adlc("push", "--task", T, expect=3)
        self.approve(T, "push")
        pushed = self.adlc("push", "--task", T)
        self.assertEqual(pushed["status"], "pushed")
        self.assertEqual(self.git("ls-remote", "--heads", "origin", ws["branch"]).split()[0], pushed["sha"])

        # Stage 7: PR
        body = self.tmp / "body.md"
        body.write_text(f"## Summary\nAdds greet. token {TOKEN}\n")
        self.adlc("pr", "--task", T, "--title", "[ASANA-1001] Add greeting helper", "--body-file", str(body), expect=3)
        self.approve(T, "pr")
        pr = self.adlc("pr", "--task", T, "--title", "[ASANA-1001] Add greeting helper", "--body-file", str(body))
        self.assertEqual(pr["status"], "created")
        self.assertFalse(pr["draft"])
        state = self.provider_prs()
        self.assertEqual(state["creates"], 1)
        self.assertNotIn(TOKEN, state["prs"][0]["body"])  # redacted

        # Stage 8: Asana in review, finish, cleanup
        self.approve(T, "asana-status", "--detail", "in_review")
        self.adlc("asana", "status", "--task", T, "--stage", "in_review")
        self.assertEqual(self.asana.section_of(T), ["s-review"])
        self.adlc("finish", "--task", T, "--status", "pr_open")
        report = self.adlc("cleanup", "--task", T)
        self.assertFalse(wt.exists())
        self.assertEqual(report["failed"], [])
        self.assertIn(f"refs/heads/{ws['branch']}", self.git("for-each-ref", "refs/heads"))  # deliverable kept

        # Sync: merged on the provider -> Asana updated only after approval
        state["prs"][0].update(state="MERGED", merged_at="2026-10-23T10:00:00Z", merge_sha="abc123def456")
        self.provider_state.write_text(json.dumps(state))
        plan = self.adlc("sync")
        self.assertTrue(plan[0]["planned"])
        self.assertFalse(self.asana.tasks[T]["completed"])
        self.approve(T, "asana-sync", "--scope", "run")
        self.adlc("sync", "--apply")
        self.assertTrue(self.asana.tasks[T]["completed"])
        self.assertEqual(len(self.asana.comments(T, "asana-adlc:merged-1")), 1)
        self.assertEqual(self.adlc("status", "--task", T, "--json")["status"], "done")


class Scenario02Rejection(WorkflowCase):
    def test_rejected_confirmation_stops_and_records(self):
        T = "1001"
        self.adlc("init", "--task", T, "--mode", "full")
        self.approve(T, "plan")
        self.adlc("reject", "--task", T, "--gate", "workspace", "--note", "not now")
        err = self.adlc("workspace", "--task", T, expect=3)
        self.assertIn("rejected", self.last_stderr)
        run = self.adlc("status", "--task", T, "--json")
        self.assertEqual(run["status"], "paused")
        self.assertEqual(run["workspace"], {})
        self.assertEqual(run["decisions"][-1]["decision"], "rejected")
        self.assertFalse(self.tmp_root.exists() and any(self.tmp_root.rglob("wt")))
        # A later, explicit approval supersedes the rejection.
        self.adlc("resume", "--task", T)
        self.approve(T, "plan")
        self.approve(T, "workspace")
        self.assertEqual(self.adlc("workspace", "--task", T)["status"], "created")

    def test_rejected_push_leaves_remote_untouched(self):
        T = "1001"
        _, wt = self.start_full()
        self.adlc("run", "--task", T, "--check", "tests", "--phase", "final")
        self.adlc("reject", "--task", T, "--gate", "push")
        self.adlc("push", "--task", T, expect=3)
        self.assertEqual(self.git("ls-remote", "--heads", "origin"), self.git("ls-remote", "--heads", "origin", "main"))


class Scenario03PauseResume(WorkflowCase):
    def test_stop_between_stages_then_continue(self):
        T = "1001"
        _, wt = self.start_full()
        self.adlc("run", "--task", T, "--check", "tests", "--phase", "baseline")
        self.adlc("checkpoint", "--task", T, "--stage", "3", "--done", "--next", "Stage 4: implement greet")
        text = self.adlc("pause", "--task", T, "--reason", "user said stop")
        self.assertIn("paused", text)
        self.assertIn("A review", text)
        self.assertFalse((self.repo / ".git/asana-adlc/1001/lock").exists())

        # Starting again does not silently restart or continue.
        self.adlc("init", "--task", T, "--mode", "full", expect=5)
        self.assertIn("review, continue, retry, or stop", self.last_stderr)

        resumed = self.adlc("resume", "--task", T)
        self.assertEqual(resumed["stage"], 4)
        self.assertEqual(resumed["next_action"], "Stage 4: implement greet")
        again = self.adlc("workspace", "--task", T)
        self.assertEqual(again["status"], "existing")  # no duplicate worktree/branch
        self.assertEqual(len([l for l in self.git("worktree", "list").splitlines()]), 2)
        baseline = self.adlc("status", "--task", T, "--json")["checks"][0]["baseline"]["status"]
        self.assertEqual(baseline, "pass")  # completed work preserved

        retry = self.adlc("retry", "--task", T)
        self.assertIn("Retry stage 4", retry["next_action"])

    def test_concurrent_run_is_refused(self):
        T = "1001"
        self.adlc("init", "--task", T, "--mode", "full")
        self.adlc("init", "--task", T, "--mode", "soft", "--task-file", self._task_file(), expect=5)

    def _task_file(self):
        path = self.tmp / "task.json"
        path.write_text(json.dumps({"name": "Add greeting helper", "notes": "x\nAcceptance Criteria:\n- works"}))
        return str(path)


class Scenario04FailedTests(WorkflowCase):
    def test_introduced_failure_blocks_push_and_pr(self):
        T = "1001"
        _, wt = self.start_full()
        self.adlc("run", "--task", T, "--check", "tests", "--phase", "baseline")
        self.implement_greet(wt, correct=False)
        self.adlc("run", "--task", T, "--check", "tests", "--phase", "final", expect=6)
        self.assertEqual(self.adlc("validation", "--task", T)["state"], "failing")
        self.adlc("checks", "--task", T, "classify", "--name", "tests", "--as", "pre-existing", "--reason", "x", expect=1)
        sh("git add -A && git commit -qm wip", wt, self.env)
        self.approve(T, "push")
        self.adlc("push", "--task", T, expect=4)
        self.adlc("finish", "--task", T, "--status", "pr_open", expect=1)
        self.assertEqual(self.provider_prs()["creates"], 0)
        log = Path(self.adlc("status", "--task", T, "--json")["checks"][0]["final"]["log"]).read_text()
        self.assertIn("FAIL", log)

    def test_preexisting_failure_gives_draft_pr(self):
        T = "1001"
        self.write(self.repo, {"tests/test_broken.py": "import unittest\n\n\nclass B(unittest.TestCase):\n"
                                                       "    def test_x(self):\n        self.fail('old bug')\n"})
        sh("git add -A && git commit -qm broken && git push -q", self.repo, self.env)
        _, wt = self.start_full()
        self.adlc("run", "--task", T, "--check", "tests", "--phase", "baseline", expect=6)
        self.implement_greet(wt)
        self.adlc("run", "--task", T, "--check", "tests", "--phase", "final", expect=6)
        self.adlc("checks", "--task", T, "classify", "--name", "tests", "--as", "pre-existing",
                  "--reason", "test_broken fails on main too")
        self.assertEqual(self.adlc("validation", "--task", T)["state"], "draft")
        sh("git add -A && git commit -qm feat", wt, self.env)
        self.approve(T, "push")
        self.adlc("push", "--task", T)
        self.approve(T, "pr")
        body = self.tmp / "b.md"
        body.write_text("x")
        self.adlc("pr", "--task", T, "--title", "t", "--body-file", str(body), "--ready", expect=1)
        pr = self.adlc("pr", "--task", T, "--title", "t", "--body-file", str(body))
        self.assertTrue(pr["draft"])


class Scenario05SoftRunCleanup(WorkflowCase):
    def soft(self, task, name):
        path = self.tmp / f"task-{task}.json"
        path.write_text(json.dumps({"name": name, "notes": "Do it.\nAcceptance Criteria:\n- works"}))
        self.adlc("init", "--task", task, "--mode", "soft", "--task-file", str(path))
        self.approve(task, "plan")
        self.approve(task, "workspace")
        return Path(self.adlc("workspace", "--task", task)["worktree"])

    def test_cleanup_removes_only_own_resources(self):
        # Resources the workflow did not create in this run:
        user_wt = self.tmp / "user-wt"
        self.git("worktree", "add", "-q", "-b", "user-branch", str(user_wt))
        other_b = self.soft("2002", "Other task")
        foreign = Path(str(other_b.parent.parent)) / "not-ours"
        foreign.mkdir()

        wt = self.soft("1001", "Add greeting helper")
        self.adlc("checks", "--task", "1001", "from-detect")
        self.adlc("run", "--task", "1001", "--check", "tests", "--phase", "baseline")
        self.implement_greet(wt)
        self.adlc("run", "--task", "1001", "--check", "tests", "--phase", "final")
        snap = self.adlc("snapshot", "--task", "1001")
        branch = f"adlc-soft/1001-{self.adlc('status', '--task', '1001', '--json')['run_id']}"
        self.assertIn(branch, self.git("branch", "--list", "adlc-soft/*"))

        self.adlc("finish", "--task", "1001", "--status", "soft_run_complete", expect=1)  # cleanup first
        report = self.adlc("cleanup", "--task", "1001", "--offline")
        self.assertEqual(len(report["removed"]), 3, report)
        self.assertFalse(wt.parent.exists())
        self.assertNotIn(branch, self.git("branch", "--list", "adlc-soft/*"))
        # Untouched:
        self.assertTrue(user_wt.exists() and other_b.exists() and foreign.exists())
        self.assertIn("user-branch", self.git("branch", "--list"))
        self.assertEqual(len(self.git("branch", "--list", "adlc-soft/*").splitlines()), 1)  # 2002's
        # Evidence preserved:
        run = self.adlc("status", "--task", "1001", "--json")
        patch = Path(run["repo"]["root"]) / ".git/asana-adlc/1001" / run["run_id"] / "evidence/changes.patch"
        self.assertIn("def greet", patch.read_text())
        self.assertTrue(snap["snapshot_sha"])
        self.adlc("finish", "--task", "1001", "--status", "soft_run_complete")

    def test_uncertain_resource_needs_approval(self):
        wt = self.soft("1001", "Add greeting helper")
        self.implement_greet(wt)
        self.adlc("snapshot", "--task", "1001")
        (wt / "notes.txt").write_text("user edited the sandbox")  # ownership of this content is unclear
        report = self.adlc("cleanup", "--task", "1001", "--offline")
        self.assertTrue((wt / "notes.txt").exists())  # neither the worktree nor its parent dir removed
        self.assertTrue(any("uncommitted" in r["reason"] for r in report["needs_approval"]))
        self.assertTrue(any("still contains" in r["reason"] for r in report["kept"]))
        report = self.adlc("cleanup", "--task", "1001", "--offline", "--include-uncertain")
        self.assertTrue(wt.exists())  # still needs an explicit approval
        self.approve("1001", "cleanup-uncertain", "--scope", "run")
        report = self.adlc("cleanup", "--task", "1001", "--offline", "--include-uncertain")
        self.assertFalse(wt.exists())
        saved = [r for r in self.adlc("status", "--task", "1001", "--json")["resources"] if r.get("saved_patch")]
        self.assertIn("user edited the sandbox", Path(saved[0]["saved_patch"]).read_text())

    def test_soft_run_never_writes_remote_without_separate_approval(self):
        wt = self.soft("1001", "Add greeting helper")
        self.implement_greet(wt)
        sh("git add -A && git commit -qm x", wt, self.env)
        self.adlc("checks", "--task", "1001", "add", "--name", "tests", "--command", "python3 -m unittest discover -s tests")
        self.adlc("run", "--task", "1001", "--check", "tests", "--phase", "final")
        self.approve("1001", "push")
        self.approve("1001", "asana-comment", "--scope", "run")
        self.adlc("push", "--task", "1001", expect=3)
        self.assertIn("Soft Run", self.last_stderr)
        msg = self.tmp / "m.txt"
        msg.write_text("hi")
        self.adlc("asana", "comment", "--task", "1001", "--key", "x", "--file", str(msg), expect=3)
        self.assertEqual(self.asana.stories["1001"], [])
        self.assertEqual(self.git("ls-remote", "--heads", "origin"), self.git("ls-remote", "--heads", "origin", "main"))


class Scenario06UserChangesUntouched(WorkflowCase):
    def test_full_and_soft_runs_leave_checkout_identical(self):
        self.write(self.repo, {"app.py": "def add(a, b):\n    return a + b  # local edit\n", "scratch.txt": "mine"})
        self.write(self.repo, {"staged.txt": "staged"})
        self.git("add", "staged.txt")
        before = self.user_state()

        _, wt = self.start_full()
        self.adlc("run", "--task", "1001", "--check", "tests", "--phase", "baseline")
        self.implement_greet(wt)
        self.adlc("run", "--task", "1001", "--check", "tests", "--phase", "final")
        sh("git add -A && git commit -qm feat", wt, self.env)
        self.approve("1001", "push")
        self.adlc("push", "--task", "1001")
        self.adlc("pause", "--task", "1001")
        self.adlc("cleanup", "--task", "1001")
        self.assertEqual(self.user_state(), before)


class Scenario07Duplicates(WorkflowCase):
    def test_no_duplicate_pr_or_comment(self):
        T = "1001"
        _, wt = self.start_full()
        self.implement_greet(wt)
        self.adlc("run", "--task", T, "--check", "tests", "--phase", "final")
        sh("git add -A && git commit -qm feat", wt, self.env)
        self.approve(T, "push")
        self.adlc("push", "--task", T)
        self.assertEqual(self.adlc("push", "--task", T)["status"], "already-pushed")
        self.approve(T, "pr", "--scope", "run")
        body = self.tmp / "b.md"
        body.write_text("x")
        self.adlc("pr", "--task", T, "--title", "t", "--body-file", str(body))
        second = self.adlc("pr", "--task", T, "--title", "t", "--body-file", str(body))
        self.assertEqual(second["status"], "existing")
        self.assertEqual(self.provider_prs()["creates"], 1)

        self.approve(T, "asana-comment", "--scope", "run")
        msg = self.tmp / "m.txt"
        msg.write_text("PR opened")
        self.adlc("asana", "comment", "--task", T, "--key", "pr-created", "--file", str(msg))
        self.assertEqual(self.adlc("asana", "comment", "--task", T, "--key", "pr-created", "--file", str(msg))["status"], "skipped")
        # Ledger entry lost (e.g. crash after the POST): the marker on the task prevents a repost.
        run_path = self._run_path(T)
        data = json.loads(run_path.read_text())
        data["asana"] = {}
        run_path.write_text(json.dumps(data))
        again = self.adlc("asana", "comment", "--task", T, "--key", "pr-created", "--file", str(msg))
        self.assertEqual(again["reason"], "already posted (found on task)")
        # Server error after the write landed: confirmed by re-reading, not reposted.
        self.asana.failures.append({"method": "POST", "contains": "/stories", "code": 500, "times": 1, "apply": True})
        res = self.adlc("asana", "comment", "--task", T, "--key", "implementation", "--file", str(msg))
        self.assertEqual(res["reason"], "confirmed after uncertain write")
        self.assertEqual(len(self.asana.comments(T, "asana-adlc:pr-created")), 1)
        self.assertEqual(len(self.asana.comments(T, "asana-adlc:implementation")), 1)

    def test_foreign_branch_is_never_reused(self):
        self.git("branch", "feature/ASANA-1001-add-greeting-helper")
        self.adlc("init", "--task", "1001", "--mode", "full")
        self.approve("1001", "plan")
        self.approve("1001", "workspace")
        self.adlc("workspace", "--task", "1001", expect=4)
        self.assertIn("not created by this workflow", self.last_stderr)

    def _run_path(self, task):
        run = self.adlc("status", "--task", task, "--json")
        return Path(run["repo"]["root"]) / ".git/asana-adlc" / task / run["run_id"] / "run.json"


class Scenario08DestructiveAndProduction(WorkflowCase):
    def test_destructive_check_requires_approval(self):
        T = "1001"
        self.start_full()
        added = self.adlc("checks", "--task", T, "add", "--name", "wipe",
                          "--command", "rm -rf build && python3 -m unittest discover -s tests")
        self.assertEqual(added["class"], "destructive")
        self.adlc("run", "--task", T, "--check", "wipe", "--phase", "final", expect=3)
        self.assertIn("destructive", self.last_stderr)

    def test_production_environment_is_blocked(self):
        T = "1001"
        self.start_full()
        self.adlc("run", "--task", T, "--check", "tests", "--phase", "final", env={"APP_ENV": "production"}, expect=3)
        self.assertIn("production", self.last_stderr)

    def test_soft_run_refuses_production_outright(self):
        path = self.tmp / "t.json"
        path.write_text(json.dumps({"name": "x", "notes": "Acceptance Criteria:\n- y"}))
        self.adlc("init", "--task", "1001", "--mode", "soft", "--task-file", str(path))
        self.approve("1001", "plan")
        self.approve("1001", "workspace")
        self.adlc("workspace", "--task", "1001")
        self.adlc("checks", "--task", "1001", "add", "--name", "m", "--command", "RAILS_ENV=production rake test")
        self.approve("1001", "production")
        self.adlc("run", "--task", "1001", "--check", "m", "--phase", "final", expect=4)

    def test_unattended_never_approves_destructive(self):
        T = "1001"
        cfg_path = self.repo / ".claude/asana-adlc.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["unattended"] = {"enabled": True, "allowed_gates": ["plan", "workspace", "destructive"], "allowed_commands": []}
        cfg_path.write_text(json.dumps(cfg))
        self.adlc("init", "--task", T, "--mode", "full")
        plan = self.tmp / "plan.md"
        plan.write_text("# plan")
        self.adlc("plan", "--task", T, "--file", str(plan))
        self.adlc("workspace", "--task", T, expect=3)  # config alone is not enough
        env = {"ASANA_ADLC_UNATTENDED": "1"}
        self.adlc("workspace", "--task", T, env=env)
        self.adlc("checks", "--task", T, "add", "--name", "wipe", "--command", "rm -rf build", env=env)
        self.adlc("run", "--task", T, "--check", "wipe", "--phase", "final", env=env, expect=3)
        decisions = self.adlc("status", "--task", T, "--json")["decisions"]
        self.assertEqual({d["by"] for d in decisions}, {"unattended-config"})
        self.assertEqual({d["gate"] for d in decisions}, {"plan", "workspace"})

    def test_unattended_plan_with_high_impact_waits_for_a_person(self):
        T = "1001"
        cfg_path = self.repo / ".claude/asana-adlc.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["unattended"] = {"enabled": True, "allowed_gates": ["plan"], "allowed_commands": []}
        cfg_path.write_text(json.dumps(cfg))
        env = {"ASANA_ADLC_UNATTENDED": "1"}
        self.adlc("init", "--task", T, "--mode", "full")
        plan = self.tmp / "plan.md"
        plan.write_text("# plan")
        self.adlc("plan", "--task", T, "--file", str(plan), "--high-impact", "drops column users.legacy", env=env)
        self.adlc("checkpoint", "--task", T, "--stage", "2", env=env)
        import subprocess, sys
        probe = ("import sys; sys.path.insert(0, %r)\n"
                 "from adlc_lib import config, ledger, gates\n"
                 "repo = config.Repo('.'); cfg = config.load(repo); run = ledger.Store(repo).get('1001')\n"
                 "try:\n    gates.require(run, cfg, 'plan'); print('auto')\n"
                 "except Exception as e:\n    print('blocked')\n") % str(Path(__file__).resolve().parent.parent / "skills/asana-adlc/scripts")
        r = subprocess.run([sys.executable, "-c", probe], cwd=self.repo, capture_output=True, text=True, env={**self.env, **env})
        self.assertEqual(r.stdout.strip(), "blocked", r.stderr)
        self.adlc("plan", "--task", T, "--file", str(plan), env=env)  # no high-impact items
        r = subprocess.run([sys.executable, "-c", probe], cwd=self.repo, capture_output=True, text=True, env={**self.env, **env})
        self.assertEqual(r.stdout.strip(), "auto", r.stderr)


class Scenario09MissingCredentials(WorkflowCase):
    def test_missing_token(self):
        pre = self.adlc("preflight", env={"ASANA_ACCESS_TOKEN": ""})
        self.assertFalse(pre["ok"])
        self.assertTrue(any("adlc auth login" in p for p in pre["problems"]))
        self.adlc("init", "--task", "1001", "--mode", "full", env={"ASANA_ACCESS_TOKEN": ""}, expect=2)
        self.assertFalse((self.repo / ".git/asana-adlc").exists())

    def test_invalid_token_and_no_leak(self):
        self.adlc("init", "--task", "1001", "--mode", "full", env={"ASANA_ACCESS_TOKEN": "2/999999999/888888888:deadbeefdeadbeefdeadbeef"}, expect=1)
        self.assertIn("401", self.last_stderr)
        self.assertNotIn("deadbeef", self.last_stderr)

    def test_unreachable_asana_and_provider(self):
        self.adlc("init", "--task", "1001", "--mode", "full", env={"ASANA_BASE_URL": "http://127.0.0.1:9"}, expect=1)
        self.assertIn("unreachable", self.last_stderr)
        pre = self.adlc("preflight", env={"STUB_PROVIDER_AUTH_FAIL": "1"})
        self.assertTrue(any("auth login" in p for p in pre["problems"]))

    def test_rate_limit_is_retried(self):
        self.asana.failures.append({"method": "GET", "contains": "/tasks/1001", "code": 429, "times": 2})
        intake = self.adlc("init", "--task", "1001", "--mode", "full")
        self.assertTrue(intake["intake"]["ok"])

    def test_provider_down_during_pr(self):
        T = "1001"
        _, wt = self.start_full()
        self.implement_greet(wt)
        self.adlc("run", "--task", T, "--check", "tests", "--phase", "final")
        sh("git add -A && git commit -qm feat", wt, self.env)
        self.approve(T, "push")
        self.adlc("push", "--task", T)
        self.approve(T, "pr")
        body = self.tmp / "b.md"
        body.write_text("x")
        self.adlc("pr", "--task", T, "--title", "t", "--body-file", str(body), env={"STUB_PROVIDER_DOWN": "1"}, expect=1)
        self.assertIsNone(self.adlc("status", "--task", T, "--json")["pr"])

    def test_secrets_withheld_and_redacted_in_logs(self):
        T = "1001"
        self.start_full()
        self.adlc("checks", "--task", T, "add", "--name", "env",
                  "--command", "echo has_asana=${ASANA_ACCESS_TOKEN:-absent} key=$MY_API_KEY")
        self.adlc("run", "--task", T, "--check", "env", "--phase", "final", env={"MY_API_KEY": "s3cr3t-value-123"})
        log = Path(self.adlc("status", "--task", T, "--json")["checks"][-1]["final"]["log"]).read_text()
        self.assertIn("has_asana=absent", log)
        self.assertNotIn("s3cr3t-value-123", log)


class Scenario10GitLab(WorkflowCase):
    provider = "gitlab"

    def test_same_flow_on_gitlab(self):
        T = "1001"
        _, wt = self.start_full()
        self.implement_greet(wt)
        self.adlc("run", "--task", T, "--check", "tests", "--phase", "final")
        sh("git add -A && git commit -qm feat", wt, self.env)
        self.approve(T, "push")
        self.adlc("push", "--task", T)
        self.approve(T, "pr", "--scope", "run")
        body = self.tmp / "b.md"
        body.write_text("x")
        pr = self.adlc("pr", "--task", T, "--title", "t", "--body-file", str(body))
        self.assertIn("merge_requests/1", pr["url"])
        self.assertEqual(self.adlc("pr", "--task", T, "--title", "t", "--body-file", str(body))["status"], "existing")


class ScenarioNoProvider(WorkflowCase):
    provider = "none"

    def test_local_only_flow_ends_at_push(self):
        T = "1001"
        self.assertTrue(self.adlc("preflight")["ok"])
        _, wt = self.start_full()
        self.implement_greet(wt)
        self.adlc("run", "--task", T, "--check", "tests", "--phase", "final")
        self.adlc("finish", "--task", T, "--status", "pushed", expect=1)  # not pushed yet
        sh("git add -A && git commit -qm feat", wt, self.env)
        self.approve(T, "push")
        self.assertEqual(self.adlc("push", "--task", T)["status"], "pushed")
        body = self.tmp / "b.md"
        body.write_text("x")
        self.adlc("pr", "--task", T, "--title", "t", "--body-file", str(body), expect=2)
        self.assertIn("finish with", self.last_stderr)
        self.adlc("finish", "--task", T, "--status", "pushed")
        self.assertEqual(self.adlc("status", "--task", T, "--json")["status"], "pushed")
        self.assertFalse(self.provider_state.exists())  # no provider CLI was ever called


if __name__ == "__main__":
    unittest.main()
