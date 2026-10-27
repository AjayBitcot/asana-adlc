# Asana ADLC — Claude Code plugin

Takes an Asana task from requirements to a reviewed-ready Pull Request on **any repository**:
Laravel, Node/Next.js/React, Python, Go, Java, Ruby, .NET and others. The repository's own files
decide its checks.

Every major step is explained and confirmed before it happens. All work runs in an isolated git
worktree, so your checkout is never touched. A run can be stopped at any point and later reviewed,
continued, retried or cleaned up.

## How it works

```
Asana task ──► Intake ──► Plan ──► Workspace ──► Implement ──► Validate ──► Push ──► PR ──► In Review
               (read,     (stack,   (worktree    (in the       (baseline    (gated, (gated, (gated
                validate)  checks,   outside      worktree)     vs final,    never   dedup)  Asana
                           confirm)  the repo)                  analysed)    forced)          update)
                                          │
                                          └─► Soft Run: same up to Validate, then snapshot → evidence →
                                              verified cleanup. No push, no PR, no Asana change.
```

- **The skill** (`skills/asana-adlc/SKILL.md` plus `stages/`) tells Claude what to do at each
  stage, when to ask, and how to talk to you.
- **The engine** (`skills/asana-adlc/scripts/adlc`) is a Python program using only the standard
  library. It holds everything safety-critical:
  - approval gates that refuse unapproved actions;
  - a command guard that expands `npm`/`composer`/`make` scripts to see what they really run;
  - isolated worktrees;
  - cleanup that removes only resources it can prove it owns;
  - resumable run records;
  - Asana and GitHub/GitLab clients that prevent duplicate PRs and comments.

## Install

```text
/plugin marketplace add AjayBitcot/asana-adlc
/plugin install asana-adlc@asana-adlc-marketplace
```

Requirements: `git` 2.31+, Python 3.8+, and `gh` (GitHub) or `glab` (GitLab) for PRs.

## Getting started

Run `/asana-adlc` in Claude Code. It walks you through:

1. **Connecting Asana.** In your own terminal, run
   `<plugin>/skills/asana-adlc/scripts/adlc auth login`. It reads your Personal Access Token with
   hidden input and checks it with a read-only request. Then it saves the token in
   `~/.config/asana-adlc/credentials.json`, readable only by you and outside every repository.
   Never paste the token into the chat. Exporting `ASANA_ACCESS_TOKEN` works too.
2. **Choosing the Asana project or board.** It lists the projects you can access, with their
   sections.
3. **Reviewing the project.** It summarises the project's purpose, its sections, and the tasks
   that are good candidates, then asks which task to implement.
4. **Choosing the local repository.** It detects the stack, checks and any uncommitted changes.
   Your checkout is never modified.
5. **Confirming section mapping.** You decide which sections mean ready, in progress, in review
   and blocked, and where to save the settings. By default they go outside the repository.
6. **Approving the plan.** It shows a plan for the task, and implementation starts only after you
   approve it.

The whole setup only reads from Asana and can be resumed at any step. It needs `gh auth login`
(GitHub) or `glab auth login` (GitLab) only if you want PRs. Choose provider `none` for
local-only work.

## Use

| Command | What it does |
|---|---|
| `/asana-adlc` | Start: setup as needed, then project → task → plan → workflow |
| `/asana-adlc setup` | Change the connection, project, task, repository or mapping |
| `/asana-adlc <task-id or URL>` | Full workflow, with a confirmation at each major step |
| `/asana-adlc soft-run <task>` | Isolated trial with verified cleanup; nothing pushed or posted |
| `/asana-adlc next` | Pick from eligible tasks |
| `/asana-adlc review <task>` | Inspect earlier work before deciding |
| `/asana-adlc continue <task>` | Resume from the last checkpoint |
| `/asana-adlc retry <task>` | Re-run the current stage |
| `/asana-adlc stop <task>` | Pause and remove only workflow-owned temporary resources |
| `/asana-adlc status [task]` | Show recorded runs |
| `/asana-adlc sync` | Update Asana from the real PR state (approved / merged / closed) |

Run records live in `.git/asana-adlc/`, which is never committed.

Unattended scheduling is opt-in and limited. See
[unattended.md](skills/asana-adlc/reference/unattended.md).

## Tests

```bash
cd tests && python3 -m unittest -v
```

The suite uses throwaway git repositories with a local bare "remote", a fake Asana HTTP server, and
stub `gh`/`glab` executables. It needs no credentials and touches no real service. The Node and
PHP stack tests run when `node`/`npm` and `php` are installed and are skipped otherwise.

| Scenario | Tests |
|---|---|
| Task → PR → merged, end to end | `Scenario01FullSuccess` |
| Rejected confirmation | `Scenario02Rejection` |
| Stop between stages, then resume | `Scenario03PauseResume` |
| Failed tests, introduced vs pre-existing | `Scenario04FailedTests` |
| Soft Run removes only its own resources; uncertain ones need approval | `Scenario05SoftRunCleanup` |
| User's checkout untouched | `Scenario06UserChangesUntouched` |
| No duplicate PRs or Asana comments | `Scenario07Duplicates` |
| Destructive and production actions blocked; unattended limits | `Scenario08DestructiveAndProduction` |
| Missing or invalid credentials, unreachable services, rate limits, secret redaction | `Scenario09MissingCredentials` |
| GitLab provider | `Scenario10GitLab` |
| Multiple stacks (Python, Node, PHP executed; Laravel, Next.js, Poetry, Go, Java and Rust detected) | `test_stacks.py` |
| Setup: missing config, authentication, projects, tasks, repositories, mapping, read-only behaviour, redaction, resuming, confirmation before implementation | `test_setup.py` |
| Guard, redaction, gates, parsing, Asana client | `test_units.py` |

## Status

Version 0.3.1. All 81 automated tests passed on 2026-10-27 (Python 3.12, git 2.43, Node 22, PHP 8.3).

On 2026-10-27 a live Claude Code session took one task from a real Asana workspace (free plan) to a
committed, validated branch on a local-only Laravel repository. That run led to the 0.3.1 fixes:
Asana plans without dependencies, `--assignee none`, and repositories without a remote.

Not yet verified:
- against real GitHub or GitLab (push and PR), since tests use stub CLIs.

Known gaps: a local-only run has no final status after its commit (it stays paused), and an
approval is used up by a `workspace` attempt that fails before creating anything.

Try push and PR creation on a throwaway repository before relying on them.
