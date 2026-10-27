*[← asana-adlc](../SKILL.md) · `sync` mode*

# Sync PR state to Asana

Reflects what the Git provider reports for PRs this workflow opened. Read-only on git.

1. `adlc sync` (dry run) — for every full run in `pr_open` it queries the provider and lists the
   planned Asana update:

   | Provider state | Planned Asana update | Run becomes |
   |---|---|---|
   | merged | `merged` comment + `merged` status mapping | `done` |
   | open, approved | `approved` comment + `approved` mapping | unchanged |
   | open, changes requested | `changes-requested` comment | unchanged |
   | closed, not merged | `pr-closed` comment + `blocked` mapping | `blocked` |
   | open, no decision | nothing | unchanged |

2. Show the list and ask for confirmation. For each approved task:
   `adlc approve --task <gid> --gate asana-sync --scope run`.
3. `adlc sync --apply` — performs the approved updates. Comments are deduplicated per PR state, so
   repeated syncs post once per change. Tasks without approval are reported, not updated.

Addressing review feedback is a new, user-initiated session: `/asana-adlc continue <gid>`.

**Done when:** every `pr_open` run was queried and each applied or skipped update is reported.
