*[← asana-adlc](../SKILL.md) · stage 2 · prev: [Intake](1-intake.md) · next: [Workspace & baseline](3-workspace.md)*

# Stage 2 — Analyse & plan

No code changes in this stage.

1. **Learn the project from the project.** Read the convention docs and CI files `adlc detect`
   lists, the manifests (dependencies, scripts, versions), lint/format/type config, and the existing
   tests. Note the architecture actually in use so the plan extends it rather than introducing a
   new pattern. [testing-strategy.md](../reference/testing-strategy.md) covers what to look for.
2. **Trace the change.** Find and read the code the task touches: entry points (routes, handlers,
   components, CLI commands), domain logic, data models and migrations, and the tests around them.
3. **Write the plan** to `plan.md` in the run dir (`run_dir` in `adlc status --task <gid> --json`):
   - **Criteria map** — one row per acceptance criterion and in-scope subtask:
     criterion → files to change → test(s) that prove it.
   - Files to create or modify; data/schema changes and how they roll back; new config keys (names only).
   - Security: input validation, authorization, injection, secrets, data exposure.
   - Compatibility: public APIs, stored data, in-flight jobs, clients.
   - **High-impact items** — destructive data changes, new dependencies, auth changes, CI/deploy
     config, anything touching secrets — each listed, or "none".
   - **Open questions** — anything hard rule 6 applies to.
   Record it: `adlc plan --task <gid> --file <run_dir>/plan.md --high-impact "<item>" …
   --open-question "<question>" …` (one flag per item).
4. **Choose the checks.** `adlc checks --task <gid> from-config` if the config lists them, else
   `from-detect` (optionally `--names a,b`), plus `add` for anything the project documents that
   detection missed. Each check shows its guard `class`; mark ones CI does not run as `--optional`.
   No test runner at all → say so plainly and propose validation (see testing-strategy.md) rather
   than adding a framework.
5. **Open questions** → [blocked](blocked.md). Otherwise **confirm the plan** — one grouped
   question ([confirmations.md](../reference/confirmations.md#2-plan)) that shows:
   - the Asana project and task (name, ID, link) and the repository path;
   - the detected stack;
   - the files/modules to change or create;
   - the working-copy strategy: branch `feature/ASANA-<gid>-<slug>` in a separate worktree outside
     the repository (your checkout and uncommitted changes are untouched) — or a Soft Run;
   - the checks to run, which need approval, and any dependency installs;
   - open questions or missing information (must be none to proceed).
   High-impact items are asked about separately. Nothing is implemented before this approval —
   the engine refuses to create the worktree without a recorded `plan` approval.
   Record each answer: `--gate plan`, `--gate workspace`, and per-command
   `--gate environment|side-effect-tests --detail "<exact command>"`.
6. Post the `analysis` comment if Asana comments were approved.

**Done when:** every criterion and in-scope subtask has a criteria-map row with at least one file
and one test, open questions are empty, and the plan approval (or rejection → [recovery](recovery.md))
is recorded. Then checkpoint stage 2.
