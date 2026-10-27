*[← asana-adlc](../SKILL.md) · stage 8 · prev: [Pull Request](7-pull-request.md)*

# Stage 8 — Close out

1. **Asana** (only if Asana updates were approved at intake): post the `pr-created` comment, then
   confirm moving the task to In Review ([confirmations.md](../reference/confirmations.md#8-asana-status)) —
   for a ready PR only; a draft PR stays In Progress with a comment saying which checks keep it a
   draft. `adlc asana status --task <gid> --stage in_review`.
2. `adlc finish --task <gid> --status pr_open` — releases the lock. With `git.provider: "none"`:
   `--status pushed` (needs a verified push; no In Review move unless the user asks for it).
3. `adlc cleanup --task <gid>` — removes the worktree and temp directory (the branch is kept: it is
   the deliverable). Anything reported under `needs_approval` or `kept` is shown to the user with
   its reason; remove uncertain items only with their explicit approval
   (`--gate cleanup-uncertain`, then `cleanup --include-uncertain`).
4. **Report** with the execution-report template: task, branch, commits, checks (baseline → final),
   PR, Asana updates, cleanup results, follow-ups — every line taken from engine output.

The task is now *in review*. Approval and merge are reflected later by [sync](sync.md).

**Done when:** the run status is `pr_open`, cleanup has been run and its remaining items reported,
and the report has been shown.
