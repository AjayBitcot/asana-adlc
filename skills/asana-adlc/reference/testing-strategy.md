# Testing strategy

The repository decides how it is tested. `adlc detect` proposes checks from evidence (each names
its source); you confirm them against the project's docs and CI, then the user approves them in
the plan.

## Finding the checks

Look, in order, at: convention docs (`CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`, README "testing"
sections) → CI workflows (what CI runs is mandatory) → manifests and their scripts → tool config
files. Prefer the project's own entry point (`npm test`, `composer test`, `make test`) over calling
tools directly; the guard expands such scripts to classify what they really run.

Examples of what detection finds — examples, not requirements:

| Stack | Typical checks |
|---|---|
| PHP / Laravel | `php artisan test` or `vendor/bin/pest` / `phpunit`, `vendor/bin/pint --test`, `vendor/bin/phpstan analyse` |
| Node / Next.js / React | package scripts `test`, `lint`, `typecheck`, `build` via the lockfile's package manager |
| Python | `pytest` (via poetry/uv/pipenv when locked), `ruff check`, `mypy`, Django `manage.py test` |
| Go / Rust / JVM / .NET / Ruby | `go test ./...`, `cargo test`, `./mvnw test` / `./gradlew test`, `dotnet test`, `bundle exec rspec` |

Monorepos: detection reports sub-projects; register checks for the ones the change touches (with
`cd sub && …` commands — `cd` is allowed by the guard).

## No automated tests

Say so plainly in the plan and the PR. Propose proportionate validation that needs no new
dependency: build / compile / syntax check, the project's linter if present, a scripted smoke
check of the changed behaviour, and manual verification steps for the reviewer. Adding a test
framework is a new dependency → its own confirmation.

## Isolation

Before running checks that touch data or services, establish where they point:

- **Test config:** `phpunit.xml` / `.env.testing`, `pytest.ini` / Django test settings, Rails
  `database.yml` `test:`, `jest`/`vitest` setup files, docker-compose test services. `adlc detect`
  surfaces some hints (`test_db_hints`).
- **Acceptable:** in-memory or temp-file databases, a database dedicated to tests whose name differs
  from the app's, framework test transactions/refresh, mocks/fakes, services started inside the
  worktree for this run.
- **Not acceptable:** the developer's main database, shared staging, anything production. The
  engine blocks commands whose text or environment points at production; that does not prove the
  rest is isolated — you must check the config.
- Overrides for isolation go in `isolation.env` in the config (e.g. `DB_CONNECTION=sqlite`); they
  apply to every `adlc run`.
- Cannot establish isolation → `adlc checks skip --reason "…"` or ask the user; never guess.

## Failures

For each failing check: read the log, compare with the baseline, decide *introduced* vs
*pre-existing* with evidence, and record it (`adlc checks classify`). Explain it to the user in one
or two lines and propose the next step. A failing mandatory check is never reported as success, and
a task is never marked ready for review while validation is `failing` or `incomplete`.
