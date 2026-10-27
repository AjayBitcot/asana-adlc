# The `adlc` engine

`<skill-dir>/scripts/adlc` — Python 3.8+ standard library only, so it runs beside any stack. Call it
by absolute path from anywhere inside the target repository (or pass `--repo <path>`). In these docs
`adlc` means that path. `--task` accepts a GID or an Asana task URL; `--run <id>` picks a specific
run (default: the latest active one).

## Exit codes

| Code | Meaning | What to do |
|---|---|---|
| 0 | success | — |
| 1 | error (message on stderr) | report it; fix or block |
| 2 | config or credentials missing | show setup steps ([config.md](config.md)) |
| 3 | approval missing or rejected | ask the user (or respect the rejection) |
| 4 | blocked — needs a human decision | [blocked](../stages/blocked.md) |
| 5 | task already has a run | offer [recovery](../stages/recovery.md) choices |
| 6 | `adlc run`: the check failed | analyse the log (stage 5) |
| 75 | remote write outcome unknown | the engine re-checks; report, then retry the same command |

## Commands

| Command | Purpose | Gate |
|---|---|---|
| `auth login [--token-stdin\|--from-env]` / `auth status` / `auth logout` | store (hidden prompt; user's own terminal), verify, or remove the Asana token | — |
| `setup status [--offline]` | what is configured/verified and the `next_step` | — |
| `setup projects [--workspace W] [--with-sections]` · `select-project --project ID\|URL` · `inspect` · `select-task --task ID\|URL\|name` | read-only Asana selection | — |
| `setup repo --path P` · `map --eligible … --in-progress … …` · `write-config [--location user\|repo] [--provider …]` · `forget [--all]` | repository, section mapping, config | — |
| `preflight [--offline]` | read-only readiness report | — |
| `detect [--path]` | stack, frameworks, proposed checks, install commands, CI files | — |
| `guard -- <command>` | classify a command (safe … production) | — |
| `init --task T --mode full\|soft [--task-file F] [--take-over-stale]` | read & validate the task, create the run, take the lock | — |
| `status [--task T] [--json]` | all runs, or one run's full state + recovery options | — |
| `refresh --task T` | re-read the task from Asana and re-validate | — |
| `plan --task T --file F [--high-impact …] [--open-question …]` | record the plan and its high-impact items | — |
| `approve --task T --gate G [--scope once\|run] [--detail D] [--soft-run-override]` | record the user's approval | — |
| `reject --task T --gate G [--detail D]` | record a rejection; pauses the run | — |
| `checkpoint --task T --stage N [--done] [--next "…"] [--action "…"] [--pending "…"]` | stage progress | — |
| `pause` / `resume` / `retry` / `block --reason --action` | recovery state | — |
| `workspace --task T [--offline]` | create or reuse the isolated worktree + branch | workspace |
| `checks --task T list\|add\|from-detect\|from-config\|skip\|classify` | manage the check list | — |
| `run --task T --check NAME --phase baseline\|final` | run one check in the worktree | by guard class |
| `changes --task T` | record changed files; save `evidence/changes.patch` | — |
| `snapshot --task T` | Soft Run: commit work to the temp branch for lossless cleanup | — |
| `validation --task T` | ready / draft / failing / incomplete | — |
| `push --task T` | push the task branch, never forced; verified | push |
| `pr --task T --title … --body-file … [--draft\|--ready]` | create or reuse the PR; verified | pr |
| `asana me\|candidates` · `asana lookup --what workspaces\|projects\|sections\|fields\|user [--gid]` | read-only lookups | — |
| `asana comment --task T --key K --file F` | idempotent comment | asana-comment |
| `asana status --task T --stage S` | idempotent status change | asana-status |
| `sync [--task T] [--apply]` | provider PR state → Asana | asana-sync |
| `cleanup --task T [--include-uncertain] [--offline]` | remove verified workflow-owned resources | cleanup-uncertain for uncertain ones |
| `finish --task T --status pr_open\|pushed\|done\|soft_run_complete\|cancelled` | close the run; release the lock | — |

## What the engine guarantees

- **Gates:** gated commands refuse (exit 3) without a matching, unconsumed approval; a later
  rejection overrides an approval; `--scope once` approvals are used up; `--detail` narrows an
  approval to one command / stage / branch.
- **Soft Run:** push, PR, and Asana writes refuse without a separate `--soft-run-override` approval;
  production-classified commands are refused outright.
- **Isolation:** worktrees live under `isolation.tmp_root` (default: the OS temp dir), never inside
  the repository; credentials (`ASANA_ACCESS_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`, `GITLAB_TOKEN`, …)
  are withheld from project commands; logs, comments, and PR bodies are redacted.
- **Idempotency:** see [run-record.md](run-record.md#idempotency).

## Where things live

- Run records: `<git-common-dir>/asana-adlc/<task>/<run_id>/` — `run.json`, `task.json`,
  `plan.md`, `logs/`, `evidence/`. Inside `.git`, so never committed.
- User-level (`$ASANA_ADLC_HOME`, default `~/.config/asana-adlc/`, directory 0700):
  `credentials.json` (0600), `setup.json` (setup progress), `projects/`, `tasks/` (read-only
  snapshots), `repos/<hash>.json` (per-repository config when saved with `--location user`).
