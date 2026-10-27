*[← asana-adlc](../SKILL.md) · stage 0 · next: [Intake](1-intake.md)*

# Stage 0 — Preflight

`adlc` below means `<skill-dir>/scripts/adlc` ([engine.md](../reference/engine.md)).

0. Setup must be `ready` ([setup.md](setup.md)); use `adlc --repo <path>` with the selected
   repository from here on.
1. Run `adlc --repo <path> preflight`. It reports, without changing anything:
   repository identity, config gaps, whether the Asana token is set and valid, Git provider
   auth, remote reachability, the user's checkout state (informational — it is never modified),
   whether unattended mode is active, and active runs.
2. **Problems** → tell the user each one with one concrete fix ([config.md](../reference/config.md)
   has setup steps). What still works without them:
   - no Asana token / Asana unreachable → back to [setup step 1](setup.md#1-auth--connect-asana),
     or `soft-run` with a local task file (`--task-file`, see [soft-run.md](soft-run.md));
   - no provider auth → everything up to and including push; PR creation is blocked;
   - missing config → run [setup](setup.md); never fill GIDs by guessing.
3. **Existing run for this task** (in `active_runs`, or `init` exits 5) → go to
   [recovery](recovery.md). Do not start a new run or continue the old one without the user's choice.

**Done when:** the preflight report has been shown to the user, every problem has a stated fix or
a stated limitation they accepted, and the task has no active run (or recovery was chosen).
