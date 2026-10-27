"""Unit tests for the safety-critical pure logic: guard, redaction, gates,
criteria parsing, branch naming, Asana client retry rules."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import TOKEN, FakeAsana
from adlc_lib import GateError, OutcomeUnknown, asana, gates, guard, ledger
from adlc_lib.config import DEFAULTS
from adlc_lib.util import redact
from adlc_lib.workspace import branch_name


class Guard(unittest.TestCase):
    CASES = {
        "safe": ["php artisan test", "vendor/bin/pest --parallel", "pytest -q", "python3 -m unittest discover",
                 "go test ./...", "cargo clippy", "./gradlew test", "ruff check .", "tsc --noEmit",
                 "bundle exec rspec", "dotnet test", "git diff --stat", "prettier --check ."],
        "destructive": ["git push --force origin x", "git push origin +main", "git reset --hard HEAD~1", "git clean -fdx",
                        "rm -rf build", "php artisan migrate:fresh", "php artisan db:wipe", "psql -c 'DROP TABLE users'",
                        "docker volume rm data", "terraform apply", "sudo make install", "git stash",
                        "git checkout -- .", "git branch -D main", "echo hi > /etc/hosts", "npx prisma migrate reset"],
        "environment": ["npm install", "composer install", "pip install -r requirements.txt", "php artisan migrate",
                        "python manage.py migrate", "docker compose up -d", "go get x", "yarn"],
        "production": ["APP_ENV=production php artisan test", "php artisan test --env=production",
                       "DATABASE_URL=postgres://u@prod-db/app pytest", "npm publish", "vercel --prod",
                       "RAILS_ENV=production rake test"],
        "side-effect": ["curl https://api.example.com", "php artisan db:seed", "ssh host ls", "npm start"],
        "unknown": ["./scripts/test.sh", "make test", "bash -c 'pytest'", "pytest $(cat args)", "npm run missing"],
        "remote": ["git push origin feature/x", "gh pr create --fill"],
    }

    def test_classification_table(self):
        for expected, commands in self.CASES.items():
            for command in commands:
                with self.subTest(command=command):
                    self.assertEqual(guard.classify(command, "/nonexistent")["class"], expected,
                                     guard.classify(command, "/nonexistent"))

    def test_script_expansion_sees_through_aliases(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root)
        (root / "package.json").write_text(json.dumps({"scripts": {
            "test": "jest", "lint": "eslint . && npm run clean", "clean": "rm -rf dist", "ci": "npm test && npm run lint"}}))
        (root / "composer.json").write_text(json.dumps({"scripts": {"test": ["@php artisan test"], "reset": "@php artisan migrate:fresh"}}))
        (root / "Makefile").write_text("test:\n\tpytest -q\n\nnuke:\n\trm -rf /tmp/x\n")
        self.assertEqual(guard.classify("npm test", root)["class"], "safe")
        self.assertEqual(guard.classify("npm run lint", root)["class"], "destructive")
        self.assertEqual(guard.classify("npm run ci", root)["class"], "destructive")
        self.assertEqual(guard.classify("composer test", root)["class"], "safe")
        self.assertEqual(guard.classify("composer run reset", root)["class"], "destructive")
        self.assertEqual(guard.classify("make test", root)["class"], "safe")
        self.assertEqual(guard.classify("make nuke", root)["class"], "destructive")

    def test_production_env_detection(self):
        self.assertEqual(guard.production_env({"APP_ENV": "production", "NODE_ENV": "production"}), ["APP_ENV"])
        self.assertEqual(guard.production_env({"DATABASE_URL": "postgres://prod.example/app"}), ["DATABASE_URL"])
        self.assertEqual(guard.production_env({"APP_ENV": "testing"}), [])


class Redaction(unittest.TestCase):
    def test_known_shapes(self):
        text = (f"asana {TOKEN} gh ghp_{'a' * 36} gl glpat-{'b' * 20} aws AKIAABCDEFGHIJKLMNOP "
                "url https://user:pa55word@host/x Authorization: Bearer abc.def.ghi password=hunter22")
        out = redact(text, env={})
        for secret in (TOKEN, "ghp_", "glpat-", "AKIAABCDEFGHIJKLMNOP", "pa55word", "abc.def.ghi", "hunter22"):
            self.assertNotIn(secret, out)

    def test_env_values(self):
        self.assertEqual(redact("x=topsecretvalue", env={"STRIPE_SECRET": "topsecretvalue"}), "x=[REDACTED:STRIPE_SECRET]")
        self.assertEqual(redact("plain text", env={"HOME": "/home/x"}), "plain text")


class FakeRun:
    def __init__(self, mode="full"):
        self.data = {"decisions": [], "history": [], "stage": 1, "status": "active", "next_action": ""}
        self.mode = mode

    def log(self, *a, **k):
        pass


class Gates(unittest.TestCase):
    def cfg(self, **unattended):
        return {**DEFAULTS, "unattended": {"enabled": False, "allowed_gates": [], "allowed_commands": [], **unattended}}

    def test_once_is_consumed_and_run_scope_persists(self):
        run = FakeRun()
        gates.record(run, "push", "approved")
        gates.require(run, self.cfg(), "push")
        with self.assertRaises(GateError):
            gates.require(run, self.cfg(), "push")
        gates.record(run, "asana-comment", "approved", scope="run")
        for _ in range(3):
            gates.require(run, self.cfg(), "asana-comment", detail="k")

    def test_detail_scoping(self):
        run = FakeRun()
        gates.record(run, "environment", "approved", detail="npm ci")
        with self.assertRaises(GateError):
            gates.require(run, self.cfg(), "environment", detail="npm install left-pad")
        gates.require(run, self.cfg(), "environment", detail="npm ci")

    def test_rejection_wins_until_reapproved(self):
        run = FakeRun()
        gates.record(run, "pr", "approved", scope="run")
        gates.record(run, "pr", "rejected")
        with self.assertRaisesRegex(GateError, "rejected"):
            gates.require(run, self.cfg(), "pr")
        gates.record(run, "pr", "approved")
        gates.require(run, self.cfg(), "pr")

    def test_unattended_requires_config_and_env(self):
        run = FakeRun()
        cfg = self.cfg(enabled=True, allowed_gates=["push", "destructive"])
        with self.assertRaises(GateError):
            gates.require(run, cfg, "push")
        os.environ["ASANA_ADLC_UNATTENDED"] = "1"
        self.addCleanup(os.environ.pop, "ASANA_ADLC_UNATTENDED")
        gates.require(run, cfg, "push")
        with self.assertRaises(GateError):
            gates.require(run, cfg, "destructive")

    def test_soft_run_remote_needs_override(self):
        run = FakeRun("soft")
        gates.record(run, "pr", "approved", scope="run")
        with self.assertRaisesRegex(GateError, "Soft Run"):
            gates.require(run, self.cfg(), "pr")
        gates.record(run, "pr", "approved", soft_run_override=True)
        gates.require(run, self.cfg(), "pr")


class Parsing(unittest.TestCase):
    def test_task_refs(self):
        self.assertEqual(ledger.parse_task_ref("1209876543210"), "1209876543210")
        self.assertEqual(ledger.parse_task_ref("https://app.asana.com/0/111/222"), "222")
        self.assertEqual(ledger.parse_task_ref("https://app.asana.com/1/9/project/111/task/333?focus=true"), "333")
        with self.assertRaises(Exception):
            ledger.parse_task_ref("1; rm -rf /")

    def test_branch_name_is_sanitised(self):
        self.assertEqual(branch_name("42", "Fix `rm -rf` & $(whoami) bug!!"), "feature/ASANA-42-fix-rm-rf-whoami-bug")
        self.assertEqual(branch_name("42", "Ünïcode only ✨"), "feature/ASANA-42-n-code-only")
        self.assertEqual(branch_name("42", ""), "feature/ASANA-42-task")

    def test_acceptance_criteria(self):
        cfg = DEFAULTS
        notes = ("Intro\n\n## Acceptance Criteria\n- [ ] first\n2. second\n* third\n\n## Notes\n- not a criterion\n")
        self.assertEqual(asana.extract_criteria({"notes": notes}, cfg), ["first", "second", "third"])
        self.assertEqual(asana.extract_criteria({"notes": "acceptance criteria:\n- a\nOut of scope:\n- b"}, cfg), ["a"])
        self.assertEqual(asana.extract_criteria({"notes": "no heading here"}, cfg), [])


class AsanaClient(unittest.TestCase):
    def setUp(self):
        self.fake = FakeAsana()
        self.fake.add_task("1", "t", "n", "s")
        self.addCleanup(self.fake.stop)
        self.client = asana.Client(token=TOKEN, base_url=self.fake.url, sleep=lambda s: None)

    def test_get_retries_5xx_and_429(self):
        self.fake.failures += [{"method": "GET", "contains": "/tasks/1", "code": 503, "times": 1},
                               {"method": "GET", "contains": "/tasks/1", "code": 429, "times": 1}]
        self.assertEqual(self.client.get("/tasks/1")["name"], "t")

    def test_write_is_not_blindly_retried(self):
        self.fake.failures.append({"method": "POST", "contains": "/stories", "code": 502, "times": 1})
        with self.assertRaises(OutcomeUnknown):
            self.client.request("POST", "/tasks/1/stories", {"data": {"text": "x"}})
        self.assertEqual(self.fake.stories["1"], [])

    def test_pagination(self):
        self.fake.stories["1"] = [{"gid": str(i), "resource_subtype": "comment_added", "text": str(i)} for i in range(5)]
        self.assertEqual(len(self.client.get_all("/tasks/1/stories", limit=2)), 5)

    def test_path_injection_rejected(self):
        with self.assertRaises(Exception):
            self.client.get("/tasks/1 HTTP/1.1\r\nX: y")

    def test_token_not_in_repr(self):
        self.assertNotIn(TOKEN, repr(self.client))


if __name__ == "__main__":
    unittest.main()
