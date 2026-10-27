*[← asana-adlc](../SKILL.md) · stop, pause, resume*

# Recovery: stop, pause, resume

## When the user stops (or a run is interrupted)

1. Finish the current atomic action only (no half-written file; let a running check end).
2. `adlc pause --task <gid> --reason "<what the user said>"` — records status `paused`, keeps every
   checkpoint, approval, check result, changed file, and resource, and releases the lock. It prints
   the status.
3. Tell the user what is done and what is not, then offer the choices below. Do nothing further
   until they choose.

A stop never discards changes, deletes files, resets or deletes branches, closes PRs, or reverts
Asana. Those happen only as separately confirmed actions, if ever.

## When a task already has a run

Triggered by `continue|review|retry|stop <task>`, by `init` exiting 5, or by starting a task that
has an active run. Show `adlc status --task <gid>` and offer:

**A. Review previous work** — read the status, `git -C <worktree> diff <base_sha>` and `git log`,
the check logs, the plan, and the resources list; verify against reality (does the worktree exist,
is the branch on the remote, does the PR exist — `gh pr view` / `glab mr view`). Present the
findings, then ask whether to continue, retry, or stop.

**B. Continue from the last completed stage** — `adlc resume --task <gid>`. It re-takes the lock and
prints the stage and `next_action`. Re-verify only what time could have changed (base branch moved,
PR state, credentials), summarise the next action, and ask for confirmation where that stage
requires it. Completed actions are not repeated; the engine's idempotency checks make re-running a
stage's engine commands safe.

**C. Retry the current stage** — `adlc retry --task <gid>`. It prints that stage's previous actions
and errors. Review them, explain why a retry is now safe (or what changes first), confirm, then
redo the stage. Checks with side effects need their approval again.

**D. Stop and clean up** — `adlc cleanup --task <gid>` to remove verified workflow-owned temporary
resources; report `needs_approval` and `kept` items with reasons. The task branch, commits, any PR,
and the user's checkout are untouched. Then `adlc finish --task <gid> --status cancelled` if the
user is abandoning the run (or leave it `paused`).

## Locks

A lock held by a live run means another session is working on the task — do not interfere. A
`stale` lock (older than `limits.lock_stale_minutes`) can be taken over with `--take-over-stale`
only after the user confirms no other session is running.
