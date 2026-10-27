"""Scenario 10: the same workflow runs different stacks with repository-specific
checks. Stacks with an available toolchain are executed in a Soft Run; the
rest are covered by detection only."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import WorkflowCase
from adlc_lib import detect

NODE = {
    "package.json": json.dumps({"name": "demo", "version": "1.0.0", "scripts": {"test": "node --test"}}),
    "package-lock.json": "{}",
    "index.js": "exports.add = (a, b) => a + b;\n",
    "test/add.test.js": "const test = require('node:test');\nconst assert = require('node:assert');\n"
                        "const { add } = require('../index.js');\ntest('add', () => assert.strictEqual(add(2, 3), 5));\n",
}
PHP = {
    "composer.json": json.dumps({"name": "acme/demo", "require": {"php": ">=8.1"}}),
    "src/Add.php": "<?php\nfunction add(int $a, int $b): int { return $a + $b; }\n",
}


class SoftRunStack(WorkflowCase):
    expected_command = None

    def run_soft(self, checks_source="from-detect"):
        path = self.tmp / "task.json"
        path.write_text(json.dumps({"name": "Stack check", "notes": "Acceptance Criteria:\n- checks pass"}))
        intake = self.adlc("init", "--task", "1001", "--mode", "soft", "--task-file", str(path))
        self.approve("1001", "plan")
        self.approve("1001", "workspace")
        self.adlc("workspace", "--task", "1001")
        checks = self.adlc("checks", "--task", "1001", checks_source)
        result = self.adlc("run", "--task", "1001", "--check", checks[0]["name"], "--phase", "final")
        self.adlc("snapshot", "--task", "1001")
        report = self.adlc("cleanup", "--task", "1001", "--offline")
        self.adlc("finish", "--task", "1001", "--status", "soft_run_complete")
        return intake, checks, result, report


@unittest.skipUnless(shutil.which("node") and shutil.which("npm"), "node/npm not installed")
class NodeStack(SoftRunStack):
    project = NODE

    def test_node_soft_run(self):
        intake, checks, result, report = self.run_soft()
        self.assertIn("node", intake["stack"]["stacks"])
        self.assertEqual(checks[0]["command"], "npm test")
        self.assertEqual(checks[0]["class"], "safe")  # expanded to `node --test`
        self.assertEqual(result["status"], "pass")
        self.assertEqual(report["failed"], [])


@unittest.skipUnless(shutil.which("php"), "php not installed")
class PhpStack(SoftRunStack):
    project = PHP

    def config(self, cfg):
        cfg["checks"] = [{"name": "syntax", "command": "php -l src/Add.php", "mandatory": True}]
        return cfg

    def test_php_soft_run_with_configured_checks(self):
        intake, checks, result, _ = self.run_soft("from-config")
        self.assertIn("php", intake["stack"]["stacks"])
        self.assertEqual(checks[0]["source"], "config")
        self.assertEqual(result["status"], "pass")


class PythonStack(SoftRunStack):
    def test_python_soft_run(self):
        intake, checks, result, _ = self.run_soft()
        self.assertEqual(checks[0]["command"], "python3 -m unittest discover -s tests")
        self.assertEqual(result["status"], "pass")


class Detection(unittest.TestCase):
    def make(self, files):
        root = Path(tempfile.mkdtemp(prefix="adlc-detect-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for rel, content in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(content)
        return detect.detect(root)

    def test_laravel(self):
        d = self.make({"composer.json": json.dumps({"require": {"laravel/framework": "^11"},
                                                    "require-dev": {"laravel/pint": "*", "larastan/larastan": "*"}}),
                       "artisan": "", "phpunit.xml": '<env name="DB_CONNECTION" value="sqlite"/>'})
        self.assertIn("laravel", d["frameworks"])
        self.assertEqual([c["command"] for c in d["checks"]],
                         ["php artisan test", "vendor/bin/pint --test", "vendor/bin/phpstan analyse --no-progress"])
        self.assertIn("phpunit.xml: DB_CONNECTION=sqlite", d["test_db_hints"])

    def test_nextjs_pnpm(self):
        d = self.make({"package.json": json.dumps({"dependencies": {"next": "14", "react": "18"},
                                                   "scripts": {"test": "vitest run", "lint": "next lint", "build": "next build",
                                                               "typecheck": "tsc --noEmit"}}),
                       "pnpm-lock.yaml": "", "tsconfig.json": "{}"})
        self.assertEqual(d["package_managers"], ["pnpm"])
        self.assertIn("nextjs", d["frameworks"])
        cmds = {c["name"]: (c["command"], c["class"]) for c in d["checks"]}
        self.assertEqual(cmds["test"], ("pnpm test", "safe"))
        self.assertEqual(cmds["build"], ("pnpm run build", "safe"))

    def test_python_poetry_pytest(self):
        d = self.make({"pyproject.toml": "[tool.poetry]\n[tool.pytest.ini_options]\n[tool.ruff]\n[tool.mypy]\n",
                       "poetry.lock": ""})
        self.assertEqual([c["command"] for c in d["checks"]],
                         ["poetry run python -m pytest", "poetry run ruff check .", "poetry run mypy ."])
        self.assertTrue(all(c["class"] == "safe" for c in d["checks"]))

    def test_go_java_rust(self):
        self.assertEqual(self.make({"go.mod": "module x"})["checks"][0]["command"], "go test ./...")
        self.assertEqual(self.make({"pom.xml": "", "mvnw": ""})["checks"][0]["command"], "./mvnw -q test")
        self.assertEqual(self.make({"Cargo.toml": ""})["checks"][0]["command"], "cargo test")

    def test_no_tests_is_reported(self):
        d = self.make({"index.html": "<h1>hi</h1>"})
        self.assertIn("no automated test runner detected", d["notes"])
        self.assertEqual(d["checks"], [])

    def test_ci_marks_mandatory_hint(self):
        d = self.make({"package.json": json.dumps({"scripts": {"test": "jest", "lint": "eslint ."}}),
                       ".github/workflows/ci.yml": "steps:\n  - run: npm test\n"})
        by_name = {c["name"]: c["in_ci"] for c in d["checks"]}
        self.assertTrue(by_name["test"])
        self.assertFalse(by_name["lint"])


if __name__ == "__main__":
    unittest.main()
