*[← asana-adlc](../SKILL.md) · stage 6 · prev: [Validate](5-validate.md) · next: [Pull Request](7-pull-request.md)*

# Stage 6 — Review, commit & push

1. **Review the full diff** in the worktree (`git diff <base_sha>` plus untracked files). Every hunk
   traces to a criteria-map row or a confirmed deviation. Remove debug output, temporary files,
   build artefacts, unrelated formatting churn, and anything secret-shaped. Only placeholder
   example env files may change.
2. **Regression review:** list callers of changed public functions/endpoints and confirm they still
   work with the change (tests or reading). Note residual risk for the PR.
3. **Commit** in the worktree following the repository's style (`git log --oneline -20 <base_ref>`
   — conventional commits, prefixes, tense). Reference `ASANA-<gid>` and put the task URL in the body.
   Stage paths explicitly. If a check's result could change because of the review edits, rerun it.
4. **Confirm the push** ([confirmations.md](../reference/confirmations.md#6-push)): branch, remote,
   commits, validation state, and that this makes the branch visible to others. Record
   `adlc approve --gate push --detail <branch>` (or reject → [recovery](recovery.md)).
5. `adlc push --task <gid>`. The engine refuses unless validation is `ready`/`draft`, the worktree
   is clean, and the branch is not protected; it never forces, and verifies the remote head after.
   Exit 4 with "remote branch has commits" → [blocked](blocked.md).

**Done when:** `adlc push` reported `pushed` or `already-pushed` with the remote head equal to local
HEAD. Then checkpoint stage 6.
