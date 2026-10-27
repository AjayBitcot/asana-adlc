# Unattended runs

The workflow is interactive by default. Unattended operation is for teams that explicitly choose
it, and stays narrow.

## Enabling

1. In `.claude/asana-adlc.json`:
   ```json
   "unattended": {
     "enabled": true,
     "allowed_gates": ["workspace", "asana-comment", "asana-status", "push", "pr"],
     "allowed_commands": ["npm ci"]
   }
   ```
2. In the scheduler's environment: `ASANA_ADLC_UNATTENDED=1`. Without it, config alone does nothing.

Never automatic, whatever the config says: `destructive`, `production`, `cleanup-uncertain`,
`corrective-change`, and `plan` when the plan lists high-impact items. A run that reaches one
blocks and waits for a person (`/asana-adlc continue <gid>` in an interactive session).

## Isolation

- A dedicated clone used only by the bot.
- Least-privilege credentials: an Asana bot user limited to the project; a Git token scoped to the
  repository (contents + pull requests, no admin). Protect the base branch on the provider.
- Secrets in a file readable only by the bot user (`chmod 600`), never in crontab or the repo.
- A Claude Code permission allow-list in the clone's `.claude/settings.local.json` limiting Bash to
  the engine and the project's check commands, e.g.:
  ```json
  { "permissions": {
      "allow": ["Read", "Edit", "Write", "Glob", "Grep", "Bash(<skill-dir>/scripts/adlc:*)",
                "Bash(git status:*)", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git add:*)", "Bash(git commit:*)"],
      "deny":  ["Read(./.env)", "Read(./.env.*)", "Bash(git push:*)", "Bash(gh:*)", "Bash(glab:*)",
                "Bash(curl:*)", "Bash(rm:*)", "Bash(git reset:*)", "Bash(git clean:*)", "Bash(env)", "Bash(printenv:*)"] } }
  ```
  `git push` / `gh` are denied directly because the engine performs them.

## Scheduling

```cron
*/15 * * * * cd /srv/adlc/<repo> && set -a && . ~/.config/asana-adlc.env && set +a && \
  ASANA_ADLC_UNATTENDED=1 flock -n /tmp/asana-adlc-<repo>.lock claude -p "/asana-adlc next" >> ~/logs/asana-adlc.log 2>&1
```

`flock -n` prevents overlapping runs in one clone. `next` in unattended mode picks the first
eligible task instead of asking. Review the log and `adlc status` regularly; blocked runs need a
person.
