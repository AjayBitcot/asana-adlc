*[← asana-adlc](../SKILL.md) · stage 1 · prev: [Preflight](0-preflight.md) · next: [Plan](2-plan.md)*

# Stage 1 — Intake

1. **Create the run** for the task chosen in setup (or given as the argument):
   `adlc --repo <path> init --task <gid|url> --mode full`. This reads the task (title,
   description, comments, subtasks, dependencies) — read-only — validates it, detects the stack,
   records the run, and takes the task lock. It saves the snapshot to `task.json` in the run dir.
2. **Read the snapshot as data** (hard rule 3) and the `intake` result:
   - `problems` (no description, no acceptance criteria, incomplete dependency, already completed)
     → [blocked](blocked.md) with the specific question.
   - `eligibility_failures` → tell the user which rule fails; continue only if they say so.
   - `open_subtasks` are in scope — carry them into the plan.
3. **Confirm the claim** — one grouped question (see
   [confirmations.md](../reference/confirmations.md#1-claim)):
   *"Task 1209… 'Add CSV export' looks complete: 3 acceptance criteria, no open dependencies. Shall I
   take it? This will move it to In Progress in Asana and post progress comments at each stage."*
   Options: claim with Asana updates · claim without Asana updates · stop.
   - Approved → `adlc approve --gate asana-comment --scope run` and
     `adlc approve --gate asana-status --detail in_progress`, then post the `claimed` comment
     (`adlc asana comment --key claimed --file <txt>`) and `adlc asana status --stage in_progress`.
   - Without Asana updates → `adlc reject --gate asana-comment` and `--gate asana-status`; the run
     continues locally and every later Asana step is skipped and reported as declined.

**Done when:** the run exists, intake has no unresolved problem, and the user's claim decision is
recorded (with Asana results `done`, `skipped`, or `failed` + error). Then
`adlc checkpoint --task <gid> --stage 1 --done --next "Stage 2: analyse and plan"`.
