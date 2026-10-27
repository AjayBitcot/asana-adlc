"""Small shared helpers: time, subprocess, git, URL sanitising, secret redaction."""

import datetime
import hashlib
import os
import re
import subprocess
from pathlib import Path

from . import AdlcError


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(args, cwd=None, check=True, env=None, timeout=None):
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env, timeout=timeout)
    except FileNotFoundError:
        raise AdlcError(f"command not found: {args[0]}")
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:800]
        raise AdlcError(f"`{' '.join(args[:4])}` failed: {redact(detail)}")
    return result


def git(cwd, *args):
    """Run git and return stripped stdout; raise on failure."""
    return run(["git", *args], cwd=cwd).stdout.strip()


def git_try(cwd, *args):
    """Run git and return (returncode, stripped stdout)."""
    result = run(["git", *args], cwd=cwd, check=False)
    return result.returncode, result.stdout.strip()


def sanitize_url(url):
    """Drop any userinfo (tokens) from a remote URL."""
    return re.sub(r"(?<=://)[^/@\s]+@", "", url or "")


def short_hash(text):
    return hashlib.sha1(text.encode()).hexdigest()[:10]


def is_within(path, parent):
    try:
        Path(path).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


# --- Redaction -------------------------------------------------------------

SECRET_ENV_NAME = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|PASS$|API_?KEY|PRIVATE_KEY|ACCESS_KEY|CREDENTIAL|AUTH|DSN|DATABASE_URL)",
    re.I,
)

SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[REDACTED PRIVATE KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "[REDACTED]"),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"), "[REDACTED]"),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), "[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "[REDACTED]"),
    (re.compile(r"\b\d/\d{6,}(?:/\d{6,})?:[0-9a-f]{16,}\b"), "[REDACTED]"),  # Asana PAT
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [REDACTED]"),
    (re.compile(r"(?<=://)[^/@\s:]+:[^/@\s]+@"), "[REDACTED]@"),
    (re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)(\s*[:=]\s*)(['\"]?)[^\s'\"]{4,}\3"), r"\1\2[REDACTED]"),
]


def redact(text, env=None):
    """Remove secret values (from the environment and known token shapes) from text."""
    if not text:
        return text
    env = os.environ if env is None else env
    for name, value in env.items():
        if value and len(value) >= 6 and SECRET_ENV_NAME.search(name):
            text = text.replace(value, f"[REDACTED:{name}]")
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
