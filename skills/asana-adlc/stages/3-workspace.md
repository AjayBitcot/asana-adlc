*[← asana-adlc](../SKILL.md) · stage 3 · prev: [Plan](2-plan.md) · next: [Implement](4-implement.md)*

# Stage 3 — Workspace & baseline

1. **Create the workspace:** `adlc workspace --task <gid>`. With the recorded `workspace` approval
   it fetches the base branch, creates `feature/ASANA-<gid>-<slug>` at the fetched base revision in
   a new worktree under the isolation temp root, registers every resource it created, and verifies
   HEAD. It reuses a branch only when an earlier run of this task created it; a branch that exists
   for any other reason is someone's work → exit 4 → [blocked](blocked.md).
   All further file edits and commands happen **in the worktree path it returns**.
2. **Dependencies.** A fresh worktree has none installed. If checks need them, run the install
   command `adlc detect` proposed (or the project's documented one) — only with its
   `environment` approval for that exact command — inside the worktree, so it stays isolated and is
   removed with it. Register nothing extra: the worktree owns it.
3. **Test isolation.** Before any check that touches a database or service, confirm from the
   project's test config that it targets a disposable test database or mocks
   ([testing-strategy.md](../reference/testing-strategy.md#isolation)). If isolation cannot be
   established, say so and ask — never point tests at shared or production data.
4. **Baseline:** for each check, `adlc run --task <gid> --check <name> --phase baseline` (exit 6 =
   check failed, which is a result, not an error). Checks whose guard class needs a gate run only
   with that command's approval. A check that cannot run → `adlc checks skip --phase baseline
   --reason "<exact reason>"`.

**Done when:** `adlc status` shows the worktree present at the recorded base, and every check has a
baseline result (pass, fail, or not run with a reason). Then checkpoint stage 3.
