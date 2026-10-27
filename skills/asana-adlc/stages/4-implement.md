*[← asana-adlc](../SKILL.md) · stage 4 · prev: [Workspace & baseline](3-workspace.md) · next: [Validate](5-validate.md)*

# Stage 4 — Implement

Work only inside the worktree, one criteria-map row at a time.

- **Read before editing**, and match the file's style, naming, structure, and comment density.
- **Extend the architecture in use** (noted in stage 2): put validation, authorization, business
  logic, data access, and presentation where this project already puts them. Add a layer only when
  the plan justified it.
- **Security as you go:** validate input, authorize new entry points, parameterise queries, keep
  secrets out of code (new settings via the project's config mechanism, placeholder values only).
- **Data changes** only as planned, reversible where the stack supports it; destructive ones only
  with their recorded approval.
- **Tests:** write the tests named in the criteria map with the project's own runner, fixtures, and
  conventions, including failure cases.
- **Docs:** update docs the change makes stale.
- **Scope:** note unrelated problems for the PR's follow-ups instead of fixing them.
- **Deviations:** small ones (a helper file, a renamed function) — mention them in the stage summary.
  A deviation that adds a high-impact item, a dependency, or changes scope → stop and confirm it
  with the user before making it (`--gate plan` again, or the specific gate).
- Run `adlc changes --task <gid>` to record the changed files and save a patch as evidence.

**Done when:** every criteria-map row has its change and its test, and every path `adlc changes`
reports is in the plan or was confirmed as a deviation. Then checkpoint stage 4.
