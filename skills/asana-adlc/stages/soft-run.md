*[← asana-adlc](../SKILL.md) · `soft-run` mode*

# Soft Run

A Soft Run tries the task end to end in a disposable environment and then removes what it created,
leaving the evidence. It is for answering "does this work, and what would it change?" before
committing to the real workflow.

| | Local validation (stages 3–5) | **Soft Run** | Full workflow |
|---|---|---|---|
| Where | task worktree, kept | temporary worktree + `adlc-soft/…` branch | task worktree + `feature/ASANA-…` branch |
| Remote writes | — | **none** (each needs its own separate approval) | push, PR, Asana, each confirmed |
| Ends with | results in the run | verified cleanup + evidence | PR + cleanup of temp resources |

## Steps

1. **Intake (read-only):** `adlc init --task <gid> --mode soft`, or offline with a local task file:
   `adlc init --task <gid> --mode soft --task-file task.json` (`{"name": …, "notes": …}` with an
   Acceptance Criteria section). No Asana writes happen in a Soft Run.
2. **Plan** as in [stage 2](2-plan.md), and confirm once: plan, the temporary worktree and branch,
   dependency installs, and any checks needing approval — stating that everything will be removed
   afterwards and nothing will be pushed or posted.
3. **Workspace:** `adlc workspace --task <gid>` → temporary worktree on `adlc-soft/<gid>-<run_id>`
   under the isolation temp root. Isolated test data only: in-memory/temporary databases, mocks, the
   project's test config ([testing-strategy.md](../reference/testing-strategy.md#isolation)). If
   isolation cannot be established for a check, skip it with the reason or ask — never fall back to
   shared services. The engine refuses production-classified commands outright in a Soft Run.
4. **Baseline, implement, validate** as in stages 3–5, using `adlc run`.
5. **Snapshot:** `adlc snapshot --task <gid>` commits the work to the temporary branch and saves
   `evidence/changes.patch`, so cleanup loses nothing.
6. **Clean up:** `adlc cleanup --task <gid>`. It removes only resources this run registered and
   can prove it owns, verifying each removal:
   - worktree — only if clean and at the snapshot commit;
   - temporary branch — only if at the recorded tip, not checked out, and not on the remote;
   - temp directory — only if it holds nothing but its ownership marker.
   Anything else is reported under `needs_approval` (possible user data: e.g. edits made in the
   sandbox after the snapshot) or `kept` (another run's or unknown content). Show these to the
   user; remove an uncertain one only with `--gate cleanup-uncertain` approved, and the engine
   saves its contents as a patch first. Never remove `kept` items.
7. `adlc finish --task <gid> --status soft_run_complete` (refused until cleanup has run), then
   show the Soft Run report ([templates.md](../reference/templates.md#soft-run-report)): checks,
   changed files, evidence paths, cleanup results, anything left behind and why.

## Remote actions from a Soft Run

Push, PR, and Asana writes are refused in a Soft Run even with an ordinary approval. If the user
explicitly wants one, confirm it as its own step and record it with `--soft-run-override`. The
usual path is instead to start the full workflow (`/asana-adlc start <task>`); the Soft Run's
`changes.patch` can be applied in the new worktree with the user's agreement.

**Done when:** status is `soft_run_complete`, every created resource is `removed` or reported with
a reason, and the report is shown.
