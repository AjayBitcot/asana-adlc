"""Command guard: classify a shell command before the workflow runs it.

Nothing is assumed safe. A command is `safe` only when every segment matches a
known read-only/check tool (test runners, linters, type checkers, builds),
after expanding package-manager scripts (npm/yarn/pnpm/bun, composer, make)
into the commands they really run. Everything else needs an approval gate.
"""

import json
import re
from pathlib import Path

SEVERITY = ["safe", "unknown", "side-effect", "remote", "environment", "destructive", "production"]
GATE_FOR = {
    "safe": None,
    "unknown": "side-effect-tests",
    "side-effect": "side-effect-tests",
    "remote": "push",
    "environment": "environment",
    "destructive": "destructive",
    "production": "production",
}

I = re.I

RULES = [
    # production ------------------------------------------------------------
    ("production", re.compile(r"\b(APP_ENV|RAILS_ENV|RACK_ENV|FLASK_ENV|MIX_ENV|ASPNETCORE_ENVIRONMENT|DOTNET_ENVIRONMENT|SPRING_PROFILES_ACTIVE|ENVIRONMENT|STAGE)=['\"]?(prod|production|live)\b", I), "production environment variable"),
    ("production", re.compile(r"\bDJANGO_SETTINGS_MODULE=\S*prod", I), "production Django settings"),
    ("production", re.compile(r"(--env(ironment)?[= ]|\s-e\s+)['\"]?(prod|production|live)\b", I), "production environment flag"),
    ("production", re.compile(r"\b(postgres(ql)?|mysql|mariadb|mongodb(\+srv)?|redis|rediss|amqp|sqlserver)://\S*prod", I), "production connection string"),
    ("production", re.compile(r"\b((npm|pnpm|yarn)\s+publish|twine\s+upload|cargo\s+publish|gem\s+push|vercel\b|netlify\s+deploy|firebase\s+deploy|serverless\s+deploy|sls\s+deploy|fly\s+deploy|heroku\b|cap\s+production|kubectl\s+(apply|rollout|scale)|helm\s+(install|upgrade))", I), "deploys or publishes"),
    # destructive -----------------------------------------------------------
    ("destructive", re.compile(r"^sudo\b"), "privileged command"),
    ("destructive", re.compile(r"^(rm|rmdir|unlink|shred|mkfs\S*|dd)\b"), "deletes files or devices"),
    ("destructive", re.compile(r"^(chmod|chown)\b.*\s-R\b"), "recursive permission change"),
    ("destructive", re.compile(r"^git\s+reset\b"), "git reset"),
    ("destructive", re.compile(r"^git\s+(clean|stash)\b"), "discards or hides working-tree changes"),
    ("destructive", re.compile(r"^git\s+checkout\b.*(\s--(\s|$)|\s\.$)"), "discards working-tree changes"),
    ("destructive", re.compile(r"^git\s+restore\b(?!.*--staged)"), "discards working-tree changes"),
    ("destructive", re.compile(r"^git\s+push\b.*(\s--force\b|\s-f\b|\s--force-with-lease\b|\s--delete\b|\s-d\b|\s\+\S|\s:\S|\s--mirror\b|\s--prune\b)"), "force or delete push"),
    ("destructive", re.compile(r"^git\s+branch\b.*\s(-D|-d|--delete|-M|-m|--move)\b"), "deletes or renames a branch"),
    ("destructive", re.compile(r"^git\s+(rebase|filter-branch|filter-repo|update-ref|replace|worktree\s+(remove|prune)|gc\b|prune\b|reflog\s+(expire|delete))"), "rewrites or deletes git data"),
    ("destructive", re.compile(r"\b(drop\s+(table|database|schema|index|view|collection)|truncate\s+(table\s+)?\w+|delete\s+from)\b", I), "destructive SQL"),
    ("destructive", re.compile(r"(migrate:(fresh|reset|refresh|rollback)|db:(wipe|drop|reset|purge|schema:load|truncate)|schema:drop|\bflushall\b|\bflushdb\b|\bdropdb\b|dropDatabase|manage\.py\s+flush|prisma\s+migrate\s+reset|--force-reset|alembic\s+downgrade|knex\s+migrate:(rollback|down)|sequelize(-cli)?\s+db:(drop|migrate:undo))", I), "destructive database command"),
    ("destructive", re.compile(r"^docker(\s+compose|-compose)?\s+((system|volume|image|container|network|builder)\s+(prune|rm)|rm\b|rmi\b|kill\b|down\b.*(-v|--volumes))"), "removes containers or volumes"),
    ("destructive", re.compile(r"\b(kubectl\s+delete|helm\s+(uninstall|delete)|terraform\s+(apply|destroy|import|state)|pulumi\s+(up|destroy)|aws\s+\S+\s+(rm|rb|delete\S*|terminate\S*)|gsutil\s+rm|gcloud\s+.*\bdelete\b|az\s+.*\bdelete\b)", I), "infrastructure change"),
    ("destructive", re.compile(r"(^|[^2&])>\s*/(?!dev/null\b)\S"), "writes to an absolute path"),
    ("destructive", re.compile(r"^(find)\b.*\s-(delete|exec\s+rm)\b"), "deletes files"),
    # environment -----------------------------------------------------------
    ("environment", re.compile(r"^(npm|pnpm|yarn|bun)\s+(install|i|ci|add|remove|rm|uninstall|un|update|upgrade|up|link|dlx|dedupe|prune)\b"), "installs or changes dependencies"),
    ("environment", re.compile(r"^(yarn|pnpm|bun)\s*$"), "installs dependencies"),
    ("environment", re.compile(r"^(npx|pnpx|bunx)\b"), "may download and run packages"),
    ("environment", re.compile(r"^composer\s+(install|update|require|remove|upgrade|create-project|global|dump-autoload|reinstall)\b"), "installs or changes dependencies"),
    ("environment", re.compile(r"^((python3?\s+-m\s+)?pip3?|uv\s+pip)\s+(install|uninstall)\b|^(poetry\s+(install|add|update|lock|remove)|pipenv\s+(install|update|uninstall)|uv\s+(sync|add|lock|remove)|conda\s+(install|create|remove|update))\b"), "installs or changes dependencies"),
    ("environment", re.compile(r"^(python3?\s+-m\s+venv|virtualenv)\b"), "creates a virtual environment"),
    ("environment", re.compile(r"^(bundle\s+(install|update|add)|gem\s+install|go\s+(get|install|mod\s+(download|tidy|vendor))|cargo\s+(add|install|update|remove)|dotnet\s+(add|restore|tool\s+install)|mvnw?\s+.*\binstall\b|\./mvnw\s+.*\binstall\b)"), "installs or changes dependencies"),
    ("environment", re.compile(r"^(apt|apt-get|brew|yum|dnf|apk|choco|snap|pacman|port)\b"), "system package manager"),
    ("environment", re.compile(r"(artisan\s+migrate\b|db:migrate|db:setup|manage\.py\s+migrate|prisma\s+(migrate\s+(dev|deploy)|db\s+push|generate)|alembic\s+upgrade|flyway\s+migrate|knex\s+migrate:(latest|up)|sequelize(-cli)?\s+db:migrate|typeorm\s+migration:run|liquibase\s+update|rake\s+db:)", I), "runs database migrations"),
    ("environment", re.compile(r"^docker(\s+compose|-compose)?\s+(run|up|start|create|build|pull|exec)\b"), "starts or builds containers"),
    ("environment", re.compile(r"(artisan\s+(key:generate|storage:link|vendor:publish|config:cache|optimize))", I), "changes application environment"),
    # remote ----------------------------------------------------------------
    ("remote", re.compile(r"^git\s+(push|remote\s+(add|set-url|remove|rm))\b"), "changes the remote (use `adlc push`)"),
    ("remote", re.compile(r"^(gh|glab)\s+(pr|mr|issue|release|repo|api)\b"), "Git provider API (use `adlc pr`)"),
    # side effects ----------------------------------------------------------
    ("side-effect", re.compile(r"^(curl|wget|http|https|httpie|nc|ncat|telnet|ssh|scp|rsync|sftp|ftp)\b"), "network access"),
    ("side-effect", re.compile(r"(db:seed|artisan\s+(tinker|queue:work|queue:listen|schedule:run|serve|horizon)|manage\.py\s+(shell|dbshell|loaddata|runserver)|rails\s+(console|runner|server|c|s)\b|\bpsql\b|\bmysql\b|\bmongo(sh)?\b|\bredis-cli\b|\bsqlcmd\b|\bsqlite3\b)", I), "database or service access"),
    ("side-effect", re.compile(r"^(npm|pnpm|yarn|bun)\s+(start|run\s+(dev|start|serve))\b|^(next|vite|nuxt)\s+(dev|start)\b"), "starts a long-running server"),
]

SAFE = [re.compile(p) for p in [
    r"^(cd|echo|printf|true|false|exit|pwd|test|\[)\b",
    r"^(cat|ls|head|tail|grep|egrep|wc|sort|uniq|cut|tr|awk|sed\s+-n|tee\s+(?!/)\S|diff|cmp|xargs\s+-0?\s*(grep|cat))\b",
    r"^(export|set)\s+-?\w",
    r"^git\s+(status|diff|log|show|rev-parse|ls-files|ls-remote|blame|branch\s+--show-current|branch$|describe|merge-base)\b",
    # PHP
    r"^php\s+(-l|--syntax-check)\b",
    r"^php\s+artisan\s+(test|route:list|about)\b",
    r"^(php\s+)?(\./)?vendor/bin/(pest|phpunit|pint|phpstan|psalm|php-cs-fixer|phpcs|phpmd|paratest|rector\s+.*--dry-run)\b",
    r"^(phpunit|pest|pint|phpstan|psalm)\b",
    r"^composer\s+(validate|audit|check-platform-reqs|outdated|show|diagnose)\b",
    # JS / TS
    r"^node\s+--test\b",
    r"^(\./)?node_modules/\.bin/\S+",
    r"^(jest|vitest|mocha|ava|tap|playwright\s+test|cypress\s+run|eslint|tsc|vue-tsc|svelte-check|astro\s+check|stylelint|biome\s+(check|lint|ci)|oxlint)\b",
    r"^prettier\s+.*(--check|-c\b|--list-different|-l\b)",
    r"^(next\s+(lint|build)|vite\s+build|nuxt\s+(build|typecheck)|ng\s+(test|lint|build)|react-scripts\s+(test|build)|tsup\b|rollup\s+-c|webpack\b|turbo\s+run\s+(test|lint|build|typecheck))",
    r"^(npm|pnpm|yarn|bun)\s+(audit|ls|list|outdated|why)\b(?!.*\bfix\b)",
    # Python
    r"^(python3?\s+-m\s+)?(pytest|unittest|mypy|ruff|flake8|pyright|pylint|bandit|compileall|doctest|nose2|tox\s+-e\s+lint)\b",
    r"^(black|isort)\s+.*(--check|--diff)",
    r"^ruff\s+(check|format)\b",
    r"^(pip-audit|safety\s+check)\b",
    r"^python3?\s+manage\.py\s+(test|check)\b",
    r"^(poetry|uv|pipenv|hatch|pdm)\s+run\s+(pytest|mypy|ruff|flake8|pyright|python\s+-m\s+(pytest|unittest))\b",
    # Go / Rust / JVM / .NET / Ruby / Swift / Elixir
    r"^go\s+(test|vet|build)\b",
    r"^(gofmt\s+-l|golangci-lint\s+run|staticcheck)\b",
    r"^cargo\s+(test|clippy|build|check|fmt|doc|audit)\b",
    r"^(\./)?(mvnw|mvn)\s+((-\S+\s+)*)(test|verify|compile|package|checkstyle:check|spotless:check|pmd:check)\b",
    r"^(\./)?(gradlew|gradle)\s+((-\S+\s+)*)(test|check|build|compileJava|compileKotlin|lint|ktlintCheck|detekt|spotlessCheck)\b",
    r"^dotnet\s+(test|build|format\s+.*--verify-no-changes)\b",
    r"^(bundle\s+exec\s+)?(rspec|rubocop|standardrb|rake\s+(test|spec)|rails\s+test|brakeman)\b",
    r"^swift\s+(test|build)\b",
    r"^mix\s+(test|format\s+--check-formatted|credo|compile)\b",
]]

OPAQUE = re.compile(r"\$\(|`|\beval\b|\b(bash|sh|zsh)\s+-c\b|\bsource\b|^\.\s")
SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=(\"[^\"]*\"|'[^']*'|\S*)\s+")
SCRIPT_RUNNER = re.compile(r"^(npm|pnpm|yarn|bun)\s+(?:(?:run|run-script)\s+)?([\w:.\-]+)(\s|$)")
COMPOSER_SCRIPT = re.compile(r"^composer\s+(?:(?:run|run-script)\s+)?([\w:.\-]+)(\s|$)")
MAKE_TARGET = re.compile(r"^make\s+(?:-\S+\s+)*([\w.\-]+)\s*$")
NPM_BUILTINS = {"install", "i", "ci", "add", "remove", "rm", "uninstall", "update", "upgrade", "up", "link",
                "audit", "ls", "list", "outdated", "why", "publish", "init", "exec", "dlx", "pack", "config"}


def _max(a, b):
    return a if SEVERITY.index(a) >= SEVERITY.index(b) else b


def _load_scripts(cwd):
    scripts = {}
    pkg = Path(cwd) / "package.json"
    if pkg.exists():
        try:
            scripts["npm"] = json.loads(pkg.read_text()).get("scripts", {}) or {}
        except json.JSONDecodeError:
            scripts["npm"] = {}
    comp = Path(cwd) / "composer.json"
    if comp.exists():
        try:
            scripts["composer"] = json.loads(comp.read_text()).get("scripts", {}) or {}
        except json.JSONDecodeError:
            scripts["composer"] = {}
    make = Path(cwd) / "Makefile"
    if make.exists():
        targets, current = {}, None
        for line in make.read_text(errors="replace").splitlines():
            m = re.match(r"^([\w.\-]+)\s*:(?!=)", line)
            if m:
                current = m.group(1)
                targets[current] = []
            elif line.startswith("\t") and current:
                targets[current].append(line.strip().lstrip("@-"))
            elif line.strip() and not line.startswith("\t"):
                current = None
        scripts["make"] = {k: " && ".join(v) for k, v in targets.items()}
    return scripts


def _expand(segment, scripts):
    """Return the body a script-runner segment executes, or None."""
    m = SCRIPT_RUNNER.match(segment)
    explicit_run = re.match(r"^\S+\s+(run|run-script)\s", segment)
    if m and (explicit_run or m.group(2) not in NPM_BUILTINS):
        tool = m.group(1)
        name = {"t": "test", "tst": "test"}.get(m.group(2), m.group(2))
        if tool == "npm" and not explicit_run and name not in {"test", "start", "stop", "restart"}:
            return None  # `npm <x>` is a builtin, not a script
        return f"{tool} script `{name}`", scripts.get("npm", {}).get(name)
    m = COMPOSER_SCRIPT.match(segment)
    if m and m.group(1) in scripts.get("composer", {}):
        body = scripts["composer"][m.group(1)]
        body = body if isinstance(body, list) else [body]
        parts = []
        for item in body:
            item = str(item)
            if item.startswith("@php "):
                item = "php " + item[5:]
            elif item.startswith("@composer "):
                item = "composer " + item[10:]
            elif item.startswith("@putenv "):
                continue
            elif item.startswith("@"):
                item = "composer run " + item[1:]
            parts.append(item)
        return f"composer script `{m.group(1)}`", " && ".join(parts)
    m = MAKE_TARGET.match(segment)
    if m:
        body = scripts.get("make", {}).get(m.group(1))
        return f"make target `{m.group(1)}`", body
    return None


def classify(command, cwd=".", _depth=0, _scripts=None):
    """Return {"class", "gate", "reasons": [...], "segments": [...]}."""
    scripts = _scripts if _scripts is not None else _load_scripts(cwd)
    worst, reasons, segments = "safe", [], []

    for rule_class, pattern, reason in RULES:
        if rule_class == "production" and pattern.search(command):
            worst = _max(worst, "production")
            reasons.append(reason)

    if OPAQUE.search(command):
        worst = _max(worst, "unknown")
        reasons.append("contains command substitution or a nested shell; cannot be inspected statically")

    for raw in SPLIT.split(command.strip()):
        segment = raw.strip()
        while ENV_ASSIGN.match(segment):
            segment = ENV_ASSIGN.sub("", segment, count=1)
        if not segment:
            continue
        seg_class, seg_reasons = "safe", []
        for rule_class, pattern, reason in RULES:
            if rule_class != "production" and pattern.search(segment):
                seg_class = _max(seg_class, rule_class)
                seg_reasons.append(reason)
        if seg_class == "safe":
            expanded = _expand(segment, scripts) if _depth < 4 else None
            if expanded:
                label, body = expanded
                if body is None:
                    seg_class, seg_reasons = "unknown", [f"{label} is not defined in the repository"]
                else:
                    inner = classify(body, cwd, _depth + 1, scripts)
                    seg_class = inner["class"]
                    seg_reasons = [f"{label} runs `{body}`"] + inner["reasons"]
            elif not any(p.search(segment) for p in SAFE):
                seg_class, seg_reasons = "unknown", ["not a recognised check command"]
        segments.append({"command": segment, "class": seg_class, "reasons": seg_reasons})
        worst = _max(worst, seg_class)
        reasons.extend(seg_reasons)

    gate = GATE_FOR[worst]
    if worst == "remote" and any(re.match(r"^(gh|glab)\b", s["command"]) for s in segments):
        gate = "pr"
    return {"command": command, "class": worst, "gate": gate, "reasons": _dedupe(reasons), "segments": segments}


PROD_ENV_NAMES = {"APP_ENV", "RAILS_ENV", "RACK_ENV", "FLASK_ENV", "MIX_ENV", "ASPNETCORE_ENVIRONMENT",
                  "DOTNET_ENVIRONMENT", "SPRING_PROFILES_ACTIVE", "ENVIRONMENT", "STAGE"}


def production_env(env):
    """Names of environment variables that point the process at production."""
    hits = []
    for name, value in env.items():
        val = (value or "").strip().lower()
        if name in PROD_ENV_NAMES and val in {"prod", "production", "live"}:
            hits.append(name)
        elif name == "DJANGO_SETTINGS_MODULE" and "prod" in val:
            hits.append(name)
        elif re.search(r"(DATABASE|DB|REDIS|MONGO)\w*_URL$", name) and re.search(r"prod", val):
            hits.append(name)
    return hits


def _dedupe(items):
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
