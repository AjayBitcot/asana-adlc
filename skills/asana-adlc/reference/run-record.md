# Run record

One JSON file per run: `<git-common-dir>/asana-adlc/<task>/<run_id>/run.json`, written only by the
engine (atomic replace). `adlc status --task <gid> --json` prints it.

| Field | Contents |
|---|---|
| `task`, `repo` | Asana GID/URL/name; repository root, remote, remote URL (credentials stripped) |
| `mode`, `status` | `full`/`soft`; `active`, `paused`, `blocked`, `pr_open`, `done`, `soft_run_complete`, `cancelled` |
| `stack` | detected stacks, frameworks, package managers, notes |
| `stage`, `last_completed_stage`, `checkpoints`, `next_action` | where the run is and what comes next |
| `completed_actions`, `pending_actions` | what was done / what is outstanding |
| `decisions` | every approval and rejection: gate, scope, detail, who, when, consumed |
| `workspace` | base branch/ref/sha, branch, worktree path, snapshot sha |
| `files_changed` | from `adlc changes` (+ `evidence/changes.patch`) |
| `checks` | command, guard class, mandatory, baseline/final result + log, classification |
| `validation` | last computed state and details |
| `resources` | everything created: type, ref, owning run, keep flag, status (`active`/`removed`/`kept`/`failed`), detail |
| `git`, `pr` | commits, pushed sha, push status; PR url/number/state/draft, verified time |
| `asana` | each comment/status operation: `done`, `skipped`, `failed`, `unknown` |
| `intake` | validation result, criteria, open subtasks |
| `errors`, `blocker`, `recovery` | what went wrong, what a human must do |
| `history` | audit log of every transition and external action |

The task lock is `<git-common-dir>/asana-adlc/<task>/lock/` (atomic `mkdir`), released by `pause`,
`block`, `reject`, and `finish`.

## Idempotency

Before repeating any action the engine checks both the record and the real state:

| Action | Duplicate guard |
|---|---|
| Run creation | one active run per task and mode; lock per task |
| Branch / worktree | reuses the run's own worktree; reuses a branch only if a run of this task created it; refuses any other existing branch (local or remote) |
| Push | compares remote head with local HEAD first (`already-pushed`); never forces |
| PR | queries the provider for any PR on the branch first; re-queries after a failed create |
| Asana comment | ledger entry, then searches the task's comments for the marker `[asana-adlc:<key>:<run_id>]`; after an uncertain write, re-reads before reporting |
| Asana status | reads the task's current section/field first; skips if already applied |
| Cleanup | assesses each resource on every call; removed resources are not touched again |
