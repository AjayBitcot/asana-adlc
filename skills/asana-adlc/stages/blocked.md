*[← asana-adlc](../SKILL.md) · any stage*

# Blocked

Use when the run cannot continue without a human: unclear or missing requirements, incomplete
dependencies, a branch owned by someone else, a closed PR, an unavailable credential or service,
failures the user chose not to keep fixing, or a rejected push.

1. Finish the current atomic action.
2. `adlc block --task <gid> --reason "<one specific line>" --action "<what a human must do, where>"`
   — records the blocker and recovery instruction and releases the lock. Work stays in place.
3. If Asana comments were approved, post the `blocked` comment (questions or reason). Apply the
   `blocked` status only with its approval (`--gate asana-status --detail blocked`).
4. Tell the user the reason, the required action, and how to resume: answer in Asana (or fix the
   setup), then `/asana-adlc continue <gid>`. In an interactive session, ask the questions directly.

When the blocker was about requirements, resume with `adlc resume`, then `adlc refresh --task <gid>`
(re-reads the task and comments, read-only) and redo [stage 2](2-plan.md) with the answers.

**Done when:** `adlc status` shows `blocked` with reason and required action, and the user has been told.
