"""Detect a repository's stack and propose validation checks from its own files.

Proposals are evidence-backed candidates (each names its source), not
assumptions: the agent confirms them with the user in the plan.
"""

import json
import re
from pathlib import Path

from . import guard

MANIFESTS = ["package.json", "composer.json", "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg",
             "Pipfile", "pom.xml", "build.gradle", "build.gradle.kts", "go.mod", "Cargo.toml", "Gemfile",
             "mix.exs", "Package.swift"]
CI_GLOBS = [".github/workflows/*.yml", ".github/workflows/*.yaml", ".gitlab-ci.yml", "Jenkinsfile",
            ".circleci/config.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml", ".buildkite/*.yml"]
DOCS = ["CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md", "README.md", ".editorconfig", "docs/CONTRIBUTING.md"]


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _text(path):
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def detect(root):
    root = Path(root)
    out = {"stacks": [], "frameworks": [], "package_managers": [], "checks": [], "install": [],
           "deps_installed": {}, "ci_files": [], "convention_docs": [], "test_db_hints": [], "notes": []}

    def add_check(name, command, kind, source):
        if any(c["command"] == command for c in out["checks"]):
            return
        verdict = guard.classify(command, root)
        out["checks"].append({"name": name, "command": command, "kind": kind, "source": source,
                              "class": verdict["class"], "gate": verdict["gate"]})

    for pattern in CI_GLOBS:
        out["ci_files"].extend(str(p.relative_to(root)) for p in sorted(root.glob(pattern)))
    out["convention_docs"] = [d for d in DOCS if (root / d).exists()]
    ci_text = "\n".join(_text(root / f) for f in out["ci_files"])

    # --- Node / JS / TS -----------------------------------------------------
    pkg_path = root / "package.json"
    if pkg_path.exists():
        pkg = _read_json(pkg_path)
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        pm = ("pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists()
              else "bun" if (root / "bun.lockb").exists() or (root / "bun.lock").exists() else "npm")
        out["stacks"].append("node")
        out["package_managers"].append(pm)
        if (root / "tsconfig.json").exists() or "typescript" in deps:
            out["stacks"].append("typescript")
        for dep, fw in [("next", "nextjs"), ("react", "react"), ("vue", "vue"), ("nuxt", "nuxt"),
                        ("svelte", "svelte"), ("@angular/core", "angular"), ("express", "express"),
                        ("@nestjs/core", "nestjs"), ("astro", "astro"), ("remix", "remix")]:
            if dep in deps:
                out["frameworks"].append(fw)
        scripts = pkg.get("scripts", {}) or {}
        kinds = [("test", "test"), ("test:unit", "test"), ("lint", "lint"), ("typecheck", "typecheck"),
                 ("type-check", "typecheck"), ("check-types", "typecheck"), ("check", "lint"),
                 ("format:check", "format"), ("build", "build")]
        for name, kind in kinds:
            if name in scripts:
                cmd = f"{pm} test" if name == "test" else f"{pm} run {name}"
                add_check(name, cmd, kind, f"package.json scripts.{name}")
        if "tsconfig.json" in [p.name for p in root.iterdir()] and not any(c["kind"] == "typecheck" for c in out["checks"]):
            add_check("typecheck", "node_modules/.bin/tsc --noEmit", "typecheck", "tsconfig.json")
        if "test" not in scripts:
            out["notes"].append("package.json has no `test` script")
        out["deps_installed"]["node_modules"] = (root / "node_modules").is_dir()
        lock = {"npm": "package-lock.json", "pnpm": "pnpm-lock.yaml", "yarn": "yarn.lock"}.get(pm)
        install = {"npm": "npm ci" if lock and (root / lock).exists() else "npm install",
                   "pnpm": "pnpm install --frozen-lockfile", "yarn": "yarn install --frozen-lockfile",
                   "bun": "bun install --frozen-lockfile"}[pm]
        out["install"].append({"stack": "node", "command": install})

    # --- PHP ----------------------------------------------------------------
    comp_path = root / "composer.json"
    if comp_path.exists():
        comp = _read_json(comp_path)
        req = {**comp.get("require", {}), **comp.get("require-dev", {})}
        out["stacks"].append("php")
        out["package_managers"].append("composer")
        laravel = "laravel/framework" in req and (root / "artisan").exists()
        if laravel:
            out["frameworks"].append("laravel")
        elif "symfony/framework-bundle" in req:
            out["frameworks"].append("symfony")
        if "pestphp/pest" in req:
            add_check("tests", "vendor/bin/pest", "test", "composer.json require-dev pestphp/pest")
        elif laravel:
            add_check("tests", "php artisan test", "test", "laravel/framework + artisan")
        elif "phpunit/phpunit" in req or (root / "phpunit.xml").exists() or (root / "phpunit.xml.dist").exists():
            add_check("tests", "vendor/bin/phpunit", "test", "phpunit config / dependency")
        if "laravel/pint" in req:
            add_check("format", "vendor/bin/pint --test", "format", "composer.json laravel/pint")
        if any(k in req for k in ("phpstan/phpstan", "larastan/larastan", "nunomaduro/larastan")):
            add_check("static-analysis", "vendor/bin/phpstan analyse --no-progress", "static", "phpstan dependency")
        if "vimeo/psalm" in req:
            add_check("static-analysis", "vendor/bin/psalm --no-progress", "static", "psalm dependency")
        for name in ("test", "lint", "analyse", "analyze"):
            if name in (comp.get("scripts") or {}):
                add_check(f"composer-{name}", f"composer run {name}", "test" if name == "test" else "lint",
                          f"composer.json scripts.{name}")
        out["deps_installed"]["vendor"] = (root / "vendor").is_dir()
        out["install"].append({"stack": "php", "command": "composer install --no-interaction --no-progress"})
        for xml in ("phpunit.xml", "phpunit.xml.dist"):
            text = _text(root / xml)
            for key in ("DB_CONNECTION", "DB_DATABASE"):
                m = re.search(rf'name="{key}"\s+value="([^"]*)"', text)
                if m:
                    out["test_db_hints"].append(f"{xml}: {key}={m.group(1)}")

    # --- Python -------------------------------------------------------------
    py_files = [f for f in ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile") if (root / f).exists()]
    if py_files or (root / "manage.py").exists():
        out["stacks"].append("python")
        blob = "\n".join(_text(root / f) for f in py_files + ["requirements-dev.txt", "pytest.ini", "tox.ini"])
        runner = ("poetry run " if (root / "poetry.lock").exists() else "uv run " if (root / "uv.lock").exists()
                  else "pipenv run " if (root / "Pipfile.lock").exists() else "")
        out["package_managers"].append(runner.split()[0] if runner else "pip")
        if (root / "manage.py").exists():
            out["frameworks"].append("django")
        for fw in ("fastapi", "flask"):
            if re.search(rf"\b{fw}\b", blob, re.I):
                out["frameworks"].append(fw)
        if "pytest" in blob or (root / "pytest.ini").exists() or (root / "conftest.py").exists():
            add_check("tests", f"{runner}python -m pytest" if runner else "python3 -m pytest", "test", "pytest configuration")
        elif (root / "manage.py").exists():
            add_check("tests", "python3 manage.py test", "test", "Django manage.py")
        elif (root / "tests").is_dir() or (root / "test").is_dir():
            add_check("tests", "python3 -m unittest discover -s tests" if (root / "tests").is_dir()
                      else "python3 -m unittest discover -s test", "test", "tests/ directory (unittest)")
        if "[tool.ruff" in blob or (root / "ruff.toml").exists() or re.search(r"^ruff\b", blob, re.M):
            add_check("lint", f"{runner}ruff check ." if runner else "ruff check .", "lint", "ruff configuration")
        if "[tool.mypy" in blob or (root / "mypy.ini").exists():
            add_check("typecheck", f"{runner}mypy ." if runner else "mypy .", "typecheck", "mypy configuration")
        if (root / ".flake8").exists() or "[flake8]" in blob:
            add_check("lint", "flake8", "lint", "flake8 configuration")
        if "[tool.black" in blob:
            add_check("format", "black --check .", "format", "black configuration")
        out["deps_installed"]["virtualenv"] = any((root / d).is_dir() for d in (".venv", "venv"))
        if (root / "poetry.lock").exists():
            out["install"].append({"stack": "python", "command": "poetry install"})
        elif (root / "uv.lock").exists():
            out["install"].append({"stack": "python", "command": "uv sync"})
        elif (root / "requirements.txt").exists():
            out["install"].append({"stack": "python", "command": "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"})

    # --- JVM, Go, Rust, Ruby, .NET, Elixir, Swift ---------------------------
    if (root / "pom.xml").exists():
        out["stacks"].append("java")
        out["package_managers"].append("maven")
        add_check("tests", "./mvnw -q test" if (root / "mvnw").exists() else "mvn -q test", "test", "pom.xml")
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        out["stacks"].append("jvm-gradle")
        out["package_managers"].append("gradle")
        add_check("tests", "./gradlew test" if (root / "gradlew").exists() else "gradle test", "test", "build.gradle")
    if (root / "go.mod").exists():
        out["stacks"].append("go")
        add_check("tests", "go test ./...", "test", "go.mod")
        add_check("vet", "go vet ./...", "static", "go.mod")
    if (root / "Cargo.toml").exists():
        out["stacks"].append("rust")
        add_check("tests", "cargo test", "test", "Cargo.toml")
        add_check("lint", "cargo clippy", "lint", "Cargo.toml")
    if (root / "Gemfile").exists():
        out["stacks"].append("ruby")
        gem = _text(root / "Gemfile")
        if (root / "spec").is_dir():
            add_check("tests", "bundle exec rspec", "test", "spec/ directory")
        elif "rails" in gem and (root / "test").is_dir():
            out["frameworks"].append("rails")
            add_check("tests", "bundle exec rails test", "test", "Rails test/ directory")
        if (root / ".rubocop.yml").exists():
            add_check("lint", "bundle exec rubocop", "lint", ".rubocop.yml")
    if list(root.glob("*.sln")) or list(root.glob("*.csproj")):
        out["stacks"].append("dotnet")
        add_check("tests", "dotnet test", "test", "*.sln / *.csproj")
    if (root / "mix.exs").exists():
        out["stacks"].append("elixir")
        add_check("tests", "mix test", "test", "mix.exs")
    if (root / "Package.swift").exists():
        out["stacks"].append("swift")
        add_check("tests", "swift test", "test", "Package.swift")

    # --- Makefile targets ---------------------------------------------------
    makefile = _text(root / "Makefile")
    for target in ("test", "lint", "check", "typecheck"):
        if re.search(rf"^{target}\s*:", makefile, re.M):
            add_check(f"make-{target}", f"make {target}", "test" if target == "test" else "lint", f"Makefile target {target}")

    # --- Mandatory hints from CI -------------------------------------------
    for check in out["checks"]:
        core = check["command"].replace("python3 -m ", "").replace("./", "")
        check["in_ci"] = bool(ci_text) and (core in ci_text or check["name"] in ci_text)

    nested = [str(p.parent.relative_to(root)) for name in MANIFESTS for p in root.glob(f"*/{name}")
              if "node_modules" not in p.parts and "vendor" not in p.parts]
    if nested:
        out["notes"].append("sub-projects with their own manifests: " + ", ".join(sorted(set(nested))))
    if not any(c["kind"] == "test" for c in out["checks"]):
        out["notes"].append("no automated test runner detected")
    if not out["stacks"]:
        out["notes"].append("no recognised stack manifest at the repository root")
    for key in ("stacks", "frameworks", "package_managers"):
        out[key] = list(dict.fromkeys(out[key]))
    return out
