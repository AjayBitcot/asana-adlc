# Git providers

Configured per repository with `git.provider`:

| Provider | CLI | Auth |
|---|---|---|
| `github` | `gh` | `gh auth login` (or `GH_TOKEN` in the environment) |
| `gitlab` | `glab` | `glab auth login` (or `GITLAB_TOKEN`) |
| `none` | — | PRs are not created; the workflow stops after push and reports the branch |

What the engine does with it:

- **Find:** every PR/MR for the task branch, any state (`gh pr list --head … --state all`,
  `glab mr list --source-branch … --all`).
- **Create:** only when none exists; draft when validation is `draft`; reviewers only from
  `git.reviewers`. Verified with a view call.
- **View/sync:** state (`open`/`merged`/`closed`), draft, review decision, merge commit — the only
  source for approved/merged in Asana.

Pushing uses plain `git push -u <remote> <branch>` from the worktree with the user's existing git
credentials. The engine refuses protected branches (`git.protected_branches` plus the base branch),
and refuses to push when the remote branch has commits the local branch lacks.

Known limitation: GitLab approval state depends on what `glab mr view --output json` exposes in the
installed version; if it is missing, `sync` reports no review decision for GitLab rather than
guessing. The GitLab path is covered by tests against a stub `glab`, not a live GitLab.
