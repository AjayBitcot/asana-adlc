"""Asana REST client and the workflow's Asana operations.

The token comes only from ASANA_ACCESS_TOKEN and is never printed. Reads retry
on rate limits and transient errors; writes retry only on 429 (not processed)
and otherwise raise OutcomeUnknown so callers re-read before trying again.
Every comment carries a marker and is checked for before posting.
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import AdlcError, ConfigError, OutcomeUnknown
from . import credentials, gates
from .util import now, redact

PATH_RE = re.compile(r"^/[A-Za-z0-9/_?=&.,:%+@-]*$")
TASK_FIELDS = ("name,notes,completed,permalink_url,due_on,created_at,modified_at,assignee.gid,assignee.email,"
               "assignee.name,tags.name,memberships.project.gid,memberships.section.gid,memberships.section.name,"
               "custom_fields.gid,custom_fields.name,custom_fields.display_value,custom_fields.text_value,"
               "custom_fields.enum_value.gid,custom_fields.enum_value.name,num_subtasks")


class AsanaHTTPError(AdlcError):
    HINTS = {401: "token invalid or expired — create a new personal access token",
             403: "the token's user cannot access this resource — grant access to the project",
             404: "not found, or the token's user cannot see it — check the GID",
             402: "this feature needs a paid Asana plan"}

    def __init__(self, status, message):
        self.status = status
        super().__init__(f"Asana HTTP {status}: {message}. {self.HINTS.get(status, '')}".strip())


class Client:
    def __init__(self, token=None, base_url=None, sleep=time.sleep, max_attempts=4, timeout=30):
        if not token:
            token, _ = credentials.load()
        self._token = token
        if not self._token:
            raise ConfigError("no Asana token: run `adlc auth login` in your own terminal "
                              "(or export ASANA_ACCESS_TOKEN)")
        self.base = (base_url or os.environ.get("ASANA_BASE_URL") or "https://app.asana.com/api/1.0").rstrip("/")
        self.sleep, self.max_attempts, self.timeout = sleep, max_attempts, timeout

    def __repr__(self):
        return f"<asana.Client {self.base}>"  # never expose the token

    def request(self, method, path, body=None):
        if method not in {"GET", "POST", "PUT"}:
            raise AdlcError(f"unsupported method {method}")
        if not PATH_RE.match(path):
            raise AdlcError(f"rejected Asana path {path!r}")
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(1, self.max_attempts + 1):
            req = urllib.request.Request(self.base + path, data=data, method=method, headers={
                "Authorization": f"Bearer {self._token}", "Accept": "application/json",
                **({"Content-Type": "application/json"} if data else {})})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    if attempt == self.max_attempts:
                        raise AdlcError(f"Asana rate limit persisted after {attempt} attempts")
                    self.sleep(int(e.headers.get("Retry-After") or 30))
                    continue
                if e.code >= 500:
                    if method != "GET":
                        raise OutcomeUnknown(f"{method} {path}: Asana HTTP {e.code}; outcome unknown")
                    if attempt == self.max_attempts:
                        raise AsanaHTTPError(e.code, "server error persisted")
                    self.sleep(2 ** attempt)
                    continue
                raise AsanaHTTPError(e.code, _error_message(e))
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if method != "GET":
                    raise OutcomeUnknown(f"{method} {path}: network error ({_reason(e)}); outcome unknown")
                if attempt == self.max_attempts:
                    raise AdlcError(f"Asana unreachable: {_reason(e)}")
                self.sleep(2 ** attempt)
        raise AdlcError("unreachable")

    def get(self, path):
        return self.request("GET", path).get("data")

    def get_all(self, path, limit=100):
        sep = "&" if "?" in path else "?"
        items, offset = [], None
        while True:
            page = self.request("GET", f"{path}{sep}limit={limit}" + (f"&offset={urllib.parse.quote(offset)}" if offset else ""))
            items.extend(page.get("data") or [])
            offset = (page.get("next_page") or {}).get("offset")
            if not offset:
                return items


def _error_message(e):
    try:
        return "; ".join(err.get("message", "") for err in json.loads(e.read()).get("errors", []))[:300]
    except Exception:
        return e.reason or "error"


def _reason(e):
    return str(getattr(e, "reason", e))[:200]


# --- Reading -----------------------------------------------------------------

def fetch_dependencies(client, gid, fields="name,completed,permalink_url"):
    """A task's dependencies, or None when the workspace's plan does not offer them (HTTP 402)."""
    try:
        return client.get_all(f"/tasks/{gid}/dependencies?opt_fields={fields}")
    except AsanaHTTPError as e:
        if e.status == 402:
            return None
        raise


def fetch_task(client, gid):
    task = client.get(f"/tasks/{gid}?opt_fields={TASK_FIELDS}")
    stories = client.get_all(f"/tasks/{gid}/stories?opt_fields=resource_subtype,text,created_at,created_by.gid,created_by.name")
    deps = fetch_dependencies(client, gid)
    return {
        "fetched_at": now(),
        "task": task,
        "comments": [s for s in stories if s.get("resource_subtype") == "comment_added"],
        "subtasks": client.get_all(f"/tasks/{gid}/subtasks?opt_fields=name,notes,completed,assignee.email"),
        "dependencies": deps or [],
        "dependencies_available": deps is not None,
    }


def extract_criteria(task, cfg):
    spec = cfg["asana"]["acceptance_criteria"]
    if spec.get("source") == "custom_field":
        for field in task.get("custom_fields") or []:
            if field.get("gid") == spec.get("field_gid"):
                text = field.get("text_value") or field.get("display_value") or ""
                return [ln.strip(" -*•\t") for ln in text.splitlines() if ln.strip(" -*•\t")]
        return []
    heading = (spec.get("heading") or "Acceptance Criteria").lower()
    lines, out, inside = (task.get("notes") or "").splitlines(), [], False
    for line in lines:
        clean = line.strip().strip("#*_ ").rstrip(":").strip().lower()
        if clean == heading:
            inside = True
            continue
        if inside:
            if re.match(r"^\s*(#{1,6}\s|\*\*[^*]+\*\*\s*:?\s*$|[A-Z][\w /&-]{2,40}:\s*$)", line):
                break
            item = re.sub(r"^\s*(?:[-*•]|\d+[.)])?\s*(?:\[[ xX]\])?\s*", "", line).strip()
            if item:
                out.append(item)
    return out


def check_eligibility(task, cfg, me=None):
    """Names of the configured eligibility rules this task fails."""
    elig, failures = cfg["asana"]["eligibility"], []
    assignee = task.get("assignee") or {}
    want = (elig.get("assignee") or "").lower()
    if want:
        ok = (want == "me" and me and assignee.get("gid") == me.get("gid")) or \
             want == (assignee.get("email") or "").lower() or want == assignee.get("gid")
        if not ok:
            failures.append(f"not assigned to {elig.get('assignee')}")
    if elig.get("section_gid"):
        sections = {(m.get("section") or {}).get("gid") for m in task.get("memberships") or []}
        if elig["section_gid"] not in sections:
            failures.append("not in the eligible section")
    if elig.get("tag") and elig["tag"] not in {t.get("name") for t in task.get("tags") or []}:
        failures.append(f"missing tag {elig['tag']!r}")
    cf = elig.get("custom_field")
    if cf:
        values = {f.get("gid"): (f.get("enum_value") or {}).get("gid") for f in task.get("custom_fields") or []}
        if values.get(cf.get("field_gid")) != cf.get("option_gid"):
            failures.append("custom field does not match the eligibility rule")
    return failures


def validate(snapshot, cfg, me=None):
    task, problems, eligibility = snapshot["task"], [], []
    criteria = extract_criteria(task, cfg)
    if task.get("completed"):
        problems.append("task is already completed")
    if not (task.get("name") or "").strip():
        problems.append("task has no title")
    if not (task.get("notes") or "").strip():
        problems.append("task has no description")
    if not criteria:
        problems.append("no acceptance criteria found "
                        f"({cfg['asana']['acceptance_criteria'].get('source')}: "
                        f"{cfg['asana']['acceptance_criteria'].get('heading') or cfg['asana']['acceptance_criteria'].get('field_gid')})")
    for dep in snapshot.get("dependencies") or []:
        if not dep.get("completed"):
            problems.append(f"dependency not completed: {dep.get('name')} ({dep.get('gid')})")
    warnings = []
    if snapshot.get("dependencies_available") is False:
        warnings.append("dependencies could not be checked: this Asana plan does not offer them — "
                        "confirm by hand that nothing this task waits on is still open")
    eligibility = check_eligibility(task, cfg, me)
    open_subtasks = [s.get("name") for s in snapshot.get("subtasks") or [] if not s.get("completed")]
    return {"ok": not problems, "problems": problems, "warnings": warnings, "eligibility_failures": eligibility,
            "criteria": criteria, "open_subtasks": open_subtasks}


def candidates(client, cfg, me=None, exclude=()):
    section = cfg["asana"]["eligibility"].get("section_gid")
    if not section:
        raise ConfigError("asana.eligibility.section_gid is not configured")
    fields = ("name,created_at,completed,assignee.gid,assignee.email,tags.name,memberships.section.gid,"
              "custom_fields.gid,custom_fields.enum_value.gid")
    tasks = client.get_all(f"/sections/{section}/tasks?completed_since=now&opt_fields={fields}")
    eligible = [t for t in tasks if t.get("gid") not in exclude and not t.get("completed")
                and not check_eligibility(t, cfg, me)]
    order = {}
    prio = cfg["asana"].get("priority_field_gid")
    if prio:
        options = (client.get(f"/custom_fields/{prio}?opt_fields=enum_options.gid") or {}).get("enum_options") or []
        order = {o["gid"]: i for i, o in enumerate(options)}

    def key(task):
        rank = len(order)
        for field in task.get("custom_fields") or []:
            if field.get("gid") == prio and field.get("enum_value"):
                rank = order.get(field["enum_value"]["gid"], rank)
        return (rank, task.get("created_at") or "")
    return sorted(eligible, key=key)


# --- Writing (gated, idempotent) --------------------------------------------

def marker(run, key):
    return f"[asana-adlc:{key}:{run.run_id}]"


def _has_marker(client, gid, mark):
    stories = client.get_all(f"/tasks/{gid}/stories?opt_fields=resource_subtype,text")
    return any(mark in (s.get("text") or "") for s in stories)


def post_comment(client, run, cfg, key, text, gate="asana-comment"):
    ops = run.data["asana"]
    if ops.get(f"comment:{key}", {}).get("status") == "done":
        return {"status": "skipped", "reason": "already posted (ledger)"}
    mark = marker(run, key)
    if _has_marker(client, run.gid, mark):
        ops[f"comment:{key}"] = {"status": "done", "at": now(), "detail": "found existing comment"}
        return {"status": "skipped", "reason": "already posted (found on task)"}
    gates.require(run, cfg, gate, detail=key)
    body = {"data": {"text": f"{redact(text).rstrip()}\n\n{mark}"}}
    try:
        client.request("POST", f"/tasks/{run.gid}/stories", body)
    except OutcomeUnknown:
        if _has_marker(client, run.gid, mark):
            ops[f"comment:{key}"] = {"status": "done", "at": now(), "detail": "confirmed after uncertain write"}
            return {"status": "done", "reason": "confirmed after uncertain write"}
        ops[f"comment:{key}"] = {"status": "unknown", "at": now()}
        raise
    except AdlcError as e:
        ops[f"comment:{key}"] = {"status": "failed", "at": now(), "detail": str(e)}
        raise
    ops[f"comment:{key}"] = {"status": "done", "at": now()}
    run.log("asana comment posted", key=key)
    return {"status": "done"}


def _status_applied(client, gid, mapping):
    task = client.get(f"/tasks/{gid}?opt_fields=completed,memberships.section.gid,custom_fields.gid,custom_fields.enum_value.gid")
    if mapping["type"] == "section":
        return mapping["section_gid"] in {m.get("section", {}).get("gid") for m in task.get("memberships") or []}
    if mapping["type"] == "custom_field":
        return any(f.get("gid") == mapping["field_gid"] and (f.get("enum_value") or {}).get("gid") == mapping["option_gid"]
                   for f in task.get("custom_fields") or [])
    if mapping["type"] == "complete":
        return bool(task.get("completed"))
    raise AdlcError(f"unknown status mapping type {mapping['type']!r}")


def apply_status(client, run, cfg, stage, gate="asana-status"):
    ops = run.data["asana"]
    mapping = cfg["asana"]["status"].get(stage)
    if not mapping:
        ops[f"status:{stage}"] = {"status": "skipped", "at": now(), "detail": "no mapping configured"}
        return {"status": "skipped", "reason": f"no Asana mapping for `{stage}`"}
    if _status_applied(client, run.gid, mapping):
        ops[f"status:{stage}"] = {"status": "done", "at": now(), "detail": "already in that state"}
        return {"status": "skipped", "reason": "already in that state"}
    gates.require(run, cfg, gate, detail=stage)
    if mapping["type"] == "section":
        path, method, body = f"/sections/{mapping['section_gid']}/addTask", "POST", {"data": {"task": run.gid}}
    elif mapping["type"] == "custom_field":
        path, method, body = f"/tasks/{run.gid}", "PUT", {"data": {"custom_fields": {mapping["field_gid"]: mapping["option_gid"]}}}
    else:
        path, method, body = f"/tasks/{run.gid}", "PUT", {"data": {"completed": True}}
    try:
        client.request(method, path, body)
    except OutcomeUnknown:
        if not _status_applied(client, run.gid, mapping):
            ops[f"status:{stage}"] = {"status": "unknown", "at": now()}
            raise
    except AdlcError as e:
        ops[f"status:{stage}"] = {"status": "failed", "at": now(), "detail": str(e)}
        raise
    ops[f"status:{stage}"] = {"status": "done", "at": now()}
    run.log("asana status applied", status=stage)
    return {"status": "done"}
