# Asana integration

All Asana access goes through the engine.

## Authentication

- **Method:** Personal Access Token (Asana → Settings → Apps → Developer apps). OAuth apps are not
  supported yet.
- **Storing it:** the user runs `adlc auth login` in their own terminal. The token is read with
  hidden input (or `--token-stdin` from a password manager pipe, or `--from-env`), verified with
  `GET /users/me`, and only then written to `~/.config/asana-adlc/credentials.json` — file mode
  0600, directory 0700, outside every repository. A failed check stores nothing. The engine
  refuses to read the file if its permissions allow other users.
- **Precedence:** `ASANA_ACCESS_TOKEN` in the environment, then the file.
- **Never:** pasted into chat, printed (status shows only the source and the Asana user), put on a
  command line, written to repositories, logs, run records, comments, or PR bodies, or passed to
  project commands. Logs and comments are additionally redacted for token-shaped strings.
- **Removing it:** `adlc auth logout` (and revoke it in Asana if it may have been exposed).

## Calls

| Engine command | Asana API calls |
|---|---|
| `auth login`, `auth status`, `setup status`, `preflight`, `asana me` | `GET /users/me` |
| `setup projects` | `GET /users/me` (workspaces), `GET /projects?workspace=…`, `GET /projects/{gid}/sections` |
| `setup select-project`, `setup inspect` | `GET /projects/{gid}`, `/sections`, `/projects/{gid}/tasks`, `GET /tasks/{gid}/dependencies` |
| `setup select-task` | same reads as `init` |
| `init`, `refresh` | `GET /tasks/{gid}`, `/stories` (comments), `/subtasks`, `/dependencies` |
| `asana candidates` | `GET /sections/{section}/tasks?completed_since=now`, `GET /custom_fields/{gid}` (priority order) |
| `asana comment` | `GET /tasks/{gid}/stories` (marker check), `POST /tasks/{gid}/stories` |
| `asana status` | `GET /tasks/{gid}` (current state), then `POST /sections/{s}/addTask`, or `PUT /tasks/{gid}` (custom field / completed) |

Behaviour: reads retry on 429 (honouring `Retry-After`), 5xx, and network errors; writes retry only
on 429 — any other failure is reported as outcome-unknown (exit 75) after a re-read. Paths are
validated before use.

| Error | Meaning | Action |
|---|---|---|
| HTTP 401 | token invalid/expired | new personal access token |
| HTTP 403 | token's user lacks access | share the project with that user |
| HTTP 404 | wrong GID or invisible | confirm the GID with the user |
| unreachable | network | retry later; Soft Run with `--task-file` still works |

Workflow states are configured per repository (`asana.status` in [config.md](config.md)): a
section, an enum custom field option, or task completion — or nothing. Unmapped states are skipped
and reported, never invented.

Writing comments: write the text to a file in the run dir with the Write tool, then
`adlc asana comment --key <template-key> --file <path>`. The engine redacts it and appends the
marker.
