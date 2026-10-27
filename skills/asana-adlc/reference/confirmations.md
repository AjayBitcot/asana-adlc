# Confirmations

Before each major action: say **what** will happen, **what it affects**, and the **expected
outcome**; then ask. Use the AskUserQuestion tool when available (options + "Other"), otherwise ask
in plain text and wait. Record the answer immediately:

```
adlc approve --task <gid> --gate <gate> [--scope once|run] [--detail "<exact command|stage|branch>"]
adlc reject  --task <gid> --gate <gate> [--detail …] --note "<user's reason>"
```

Record only what the user actually said — the engine treats the record as the user's decision. A
rejection pauses the run: say what was not done, and offer the [recovery](../stages/recovery.md)
choices.

## Confirmation points

Related low-risk actions are grouped into one question; high-impact and irreversible actions are
always asked about individually.

Setup choices (project, task, repository, section mapping, where to save config) are always the
user's: asked as questions in [setup](../stages/setup.md), never defaulted. They are read-only
towards Asana and need no gate.

| # | When | Gates recorded | Grouped with |
|---|---|---|---|
| <a id="1-claim"></a>1 | Intake | `asana-comment` (scope run), `asana-status` detail `in_progress` | claim + Asana progress updates |
| <a id="2-plan"></a>2 | Plan ready | `plan`; `workspace`; `environment` / `side-effect-tests` per exact command | plan, branch/worktree, dependency installs, checks needing approval |
| 3 | High-impact item | `destructive` / `environment` / `production` per item | **never grouped** |
| 4 | Significant deviation or corrective change | `plan` again or `corrective-change` | — |
| 5 | A check with side effects not covered at #2 | `side-effect-tests` detail = command | — |
| <a id="6-push"></a>6 | Before push | `push` detail = branch | — |
| <a id="7-pull-request"></a>7 | Before PR | `pr` | — |
| <a id="8-asana-status"></a>8 | Status changes after intake (In Review, Blocked) | `asana-status` detail = stage | — |
| 9 | Uncertain cleanup | `cleanup-uncertain` detail = resource id | **never grouped** |
| — | Sync | `asana-sync` (scope run) per task | all updates in one sync |

No confirmation is needed for read-only work (reading files, `detect`, `status`, `preflight`,
`git diff`), edits inside the run's own worktree that follow the approved plan, checks the guard
classifies `safe`, or cleanup the engine proves is safe.

## Wording

```
Next: <action in one line>
Affects: <repo/branch/remote/Asana task/files/services>
Expected: <outcome>
Not affected: <user's checkout, other branches, …>   (when relevant)
Proceed?  [Yes] [No] [Other…]
```

## Gate reference

| Gate | Covers |
|---|---|
| `plan` | starting implementation from the approved plan |
| `workspace` | creating the branch + worktree (affects the repo's refs and worktree list) |
| `environment` | dependency installs, migrations, container start — exact command |
| `side-effect-tests` | commands that touch data/services or can't be verified side-effect free |
| `destructive` | deleting, resetting, force operations, destructive data changes |
| `production` | anything aimed at a production environment (refused in Soft Run) |
| `push`, `pr` | remote writes |
| `asana-comment`, `asana-status`, `asana-sync` | Asana writes |
| `cleanup-uncertain` | removing a resource whose ownership/contents are not provably the workflow's |
| `corrective-change` | significant fixes after failures |

## Unattended mode

Only when **both** `unattended.enabled: true` in the config **and** the process has
`ASANA_ADLC_UNATTENDED=1`. Then gates listed in `unattended.allowed_gates` pass automatically
(recorded as `by: unattended-config`), and `environment`/`side-effect-tests` pass only for commands
listed verbatim in `unattended.allowed_commands`. `destructive`, `production`, `cleanup-uncertain`,
and `corrective-change` are never automatic, and `plan` is never automatic when the plan has
high-impact items — those runs stop and wait. See [unattended.md](unattended.md).
