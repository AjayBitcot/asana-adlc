*[← asana-adlc](../SKILL.md) · stage 7 · prev: [Commit & push](6-commit.md) · next: [Close out](8-close-out.md)*

# Stage 7 — Pull Request

With `git.provider: "none"` (local-only setups) skip this stage: tell the user the branch is pushed
to the configured remote and no PR is created, then go to [stage 8](8-close-out.md) and finish
with `--status pushed`.

1. **Write the body** from the PR template in [templates.md](../reference/templates.md) to a file
   in the run dir. Fill every section from the run record (`adlc status --task <gid> --json`);
   "None" is valid, an omitted section is not. Include checks that could not run and why.
2. **Title:** `[ASANA-<gid>] <task name>` unless recent PR titles in the repo follow another
   convention.
3. **Confirm** ([confirmations.md](../reference/confirmations.md#7-pull-request)): title, base ←
   head, draft or ready (draft whenever validation is `draft`), reviewers (only those in config).
   Record `--gate pr`.
4. `adlc pr --task <gid> --title "<title>" --body-file <file>` (add `--draft` to force draft).
   The engine first looks for an existing PR for the branch: an open one is reused, a closed or
   merged one stops for the user's decision. It verifies the created PR with a view call. If
   creation fails it re-checks before reporting, so a retry never duplicates.

**Done when:** `adlc pr` returned `created` or `existing` with a URL. Then checkpoint stage 7.
