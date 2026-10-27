*[← asana-adlc](../SKILL.md) · stage 5 · prev: [Implement](4-implement.md) · next: [Commit & push](6-commit.md)*

# Stage 5 — Validate

1. Run every check: `adlc run --task <gid> --check <name> --phase final`. Logs are saved, redacted,
   in the run dir; the command prints the tail.
2. **Analyse each failure** — read the log, compare with the baseline:
   - failed at baseline with the same failures → `adlc checks classify --name <n> --as pre-existing
     --reason "<evidence>"` (the engine refuses this if the baseline passed);
   - otherwise it is introduced by the change → `--as introduced`, then fix.
3. **Fixing:** small, clearly-scoped fixes to code you wrote in stage 4 need no extra confirmation.
   Ask first (`--gate corrective-change`) before anything significant — touching files outside the
   plan, changing a test's expectations, adding a dependency — or before rerunning a check that has
   side effects. After `limits.max_fix_iterations` rounds without green, stop and present the
   analysis and options ([blocked](blocked.md) if the user wants to stop there).
4. Never skip, weaken, or delete tests to get green. A test changes only when the task deliberately
   changes the behaviour it asserts — and the PR says so.
5. `adlc validation --task <gid>` → `ready` (all mandatory pass), `draft` (pre-existing failures
   or mandatory checks not run — PR will be a draft), `failing`, or `incomplete`. Only `ready`
   or `draft` can be pushed.
6. `adlc changes --task <gid>`, then post the `implementation` comment if comments were approved.

**Done when:** validation is `ready` or `draft`, every non-passing check has a recorded
classification or skip reason, and the user has been shown the results. Then checkpoint stage 5.
If the state is `failing`, do not claim completion — go to [blocked](blocked.md) or keep fixing
with the user's agreement.
