"""Asana credential storage.

Resolution order: ASANA_ACCESS_TOKEN in the environment, then the user-level
credentials file `<home>/credentials.json` (directory 0700, file 0600), where
<home> is $ASANA_ADLC_HOME or ~/.config/asana-adlc. The file is outside every
repository, so it can never be committed. The token is never printed.
"""

import json
import os
import stat
from pathlib import Path

from . import ConfigError
from .util import now


def home():
    return Path(os.environ.get("ASANA_ADLC_HOME") or Path.home() / ".config" / "asana-adlc")


def path():
    return home() / "credentials.json"


def load():
    """Return (token, source) or (None, None)."""
    token = os.environ.get("ASANA_ACCESS_TOKEN")
    if token:
        return token, "environment (ASANA_ACCESS_TOKEN)"
    p = path()
    if not p.exists():
        return None, None
    mode = stat.S_IMODE(p.stat().st_mode)
    if mode & 0o077:
        raise ConfigError(f"{p} is readable by other users (mode {oct(mode)}); run `chmod 600 {p}`")
    try:
        token = json.loads(p.read_text()).get("token")
    except json.JSONDecodeError:
        raise ConfigError(f"{p} is corrupt; run `adlc auth login` again")
    return (token, f"credentials file ({p})") if token else (None, None)


def save(token, user):
    directory = home()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    p = path()
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"token": token, "user": {k: user.get(k) for k in ("gid", "email", "name")}, "saved_at": now()}, fh)
    os.replace(tmp, p)
    os.chmod(p, 0o600)
    return p


def delete():
    p = path()
    if p.exists():
        p.unlink()
        return True
    return False
