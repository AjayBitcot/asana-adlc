# Configuration

The [setup flow](../stages/setup.md) writes the configuration for you. Settings and secrets are
kept apart:

- **Settings (no secrets)**, one of — the first that exists wins:
  1. `.claude/asana-adlc.json` in the repository (`write-config --location repo`) — shared with the
     team when committed;
  2. `~/.config/asana-adlc/repos/<hash-of-repo-path>.json` (`--location user`, the default) —
     personal, outside the repository.
  Every key is optional; missing keys take the defaults in
  [asana-adlc.example.json](asana-adlc.example.json).
- **Asana token** — `ASANA_ACCESS_TOKEN` in the environment, else `~/.config/asana-adlc/credentials.json`
  (0600, written by `adlc auth login`). See [asana.md](asana.md#authentication). Git provider
  credentials come from `gh auth login` / `glab auth login`. `ASANA_ADLC_HOME` relocates the
  user-level directory; `ASANA_BASE_URL` overrides the API URL.

A missing or `REPLACE_WITH_…` value is a setup gap to report (`adlc preflight` lists them), never a
value to infer.

## `asana`

| Key | Meaning |
|---|---|
| `workspace_gid`, `project_gid` | Where tasks live. |
| `eligibility.section_gid` | Section `next` picks from (e.g. "Ready for Dev"). Needed for `next`. |
| `eligibility.assignee` | Email, user GID, or `"me"` (the token's owner). |
| `eligibility.tag` | Optional tag name the task must carry. |
| `eligibility.custom_field` | Optional `{ "field_gid": "…", "option_gid": "…" }` the task must match. |
| `acceptance_criteria` | `{ "source": "notes_heading", "heading": "Acceptance Criteria" }` — the list under that heading in the description (case-insensitive; bullets, numbers, checkboxes; ends at the next heading). Or `{ "source": "custom_field", "field_gid": "…" }`. |
| `priority_field_gid` | Optional enum custom field; `next` orders by its option order, then age. |
| `status.<state>` | `in_progress`, `in_review`, `blocked`, `approved`, `merged` → `null` (no change), `{ "type": "section", "section_gid": "…" }`, `{ "type": "custom_field", "field_gid": "…", "option_gid": "…" }`, or `{ "type": "complete" }`. |

## `git`

| Key | Meaning |
|---|---|
| `provider` | `github`, `gitlab`, or `none` (stop after push). |
| `remote`, `base_branch` | Where to fetch from and push to; the branch every task starts from and targets. |
| `branch_prefix` | Default `feature/` → `feature/ASANA-<gid>-<slug>`. |
| `protected_branches` | Glob patterns never pushed to (the base branch is always protected). |
| `reviewers` | Reviewers to request; empty = none. |

## `checks`

`null` = detect from the repository. Or a fixed list: `[{ "name": "tests", "command": "npm test",
"mandatory": true }]`. Commands come only from here, the repository, or the user — never from task
content — and are classified by the guard before they run.

## `isolation`

| Key | Meaning |
|---|---|
| `tmp_root` | Where worktrees go (default: OS temp dir). Must be outside the repository. |
| `env` | Environment overrides for every check, e.g. `{ "DB_CONNECTION": "sqlite", "DB_DATABASE": ":memory:" }`. |
| `timeout_seconds` | Per-check timeout. |

## `unattended`, `limits`

See [unattended.md](unattended.md). `limits.lock_stale_minutes` — age after which a lock may be
taken over (with confirmation); `limits.max_fix_iterations` — fix rounds before asking the user.

## Finding GIDs

Read-only lookups through the engine (the token never appears in a command line):

```bash
adlc asana lookup --what workspaces
adlc asana lookup --what projects --gid <workspace_gid>
adlc asana lookup --what sections --gid <project_gid>
adlc asana lookup --what fields   --gid <project_gid>      # custom fields + enum options
adlc asana lookup --what user     --gid you@example.com
```

A project GID is also the first number in a project URL (`https://app.asana.com/0/<project_gid>/…`).
