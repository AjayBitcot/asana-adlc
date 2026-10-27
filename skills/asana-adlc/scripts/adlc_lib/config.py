"""Repository config (`.claude/asana-adlc.json`) and repository identity."""

import copy
import json
from pathlib import Path

from . import ConfigError
from .util import git, git_try, sanitize_url

CONFIG_PATH = ".claude/asana-adlc.json"

DEFAULTS = {
    "asana": {
        "workspace_gid": None,
        "project_gid": None,
        "eligibility": {"section_gid": None, "assignee": None, "tag": None, "custom_field": None},
        "acceptance_criteria": {"source": "notes_heading", "heading": "Acceptance Criteria"},
        "priority_field_gid": None,
        "status": {"in_progress": None, "in_review": None, "blocked": None, "approved": None, "merged": None},
    },
    "git": {
        "provider": "github",
        "remote": "origin",
        "base_branch": "main",
        "branch_prefix": "feature/",
        "protected_branches": ["main", "master", "develop", "development", "production", "staging", "release/*"],
        "reviewers": [],
    },
    "checks": None,
    "isolation": {"tmp_root": None, "env": {}, "timeout_seconds": 1800},
    "unattended": {"enabled": False, "allowed_gates": [], "allowed_commands": []},
    "limits": {"lock_stale_minutes": 120, "max_fix_iterations": 3},
}

PROVIDERS = {"github", "gitlab", "none"}


def _merge(base, override):
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


class Repo:
    """Identity of the target repository, resolved from any of its worktrees."""

    def __init__(self, path="."):
        code, top = git_try(path, "rev-parse", "--show-toplevel")
        if code != 0:
            raise ConfigError(f"not inside a git repository: {Path(path).resolve()}")
        self.common_dir = Path(git(path, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        # The main worktree owns the config and the user's working files.
        self.root = self.common_dir.parent if self.common_dir.name == ".git" else Path(top)
        self.cwd_top = Path(top)

    def remote_url(self, remote):
        code, url = git_try(self.root, "remote", "get-url", remote)
        return sanitize_url(url) if code == 0 else None

    def identity(self, remote):
        return {"root": str(self.root), "remote": remote, "remote_url": self.remote_url(remote)}


def user_config_path(repo_root):
    from .credentials import home
    from .util import short_hash
    return home() / "repos" / f"{short_hash(str(Path(repo_root).resolve()))}.json"


def locate(repo):
    """The config in effect: the repo's shared file wins over the user-level one."""
    shared = repo.root / CONFIG_PATH
    if shared.exists():
        return shared, "repo"
    personal = user_config_path(repo.root)
    if personal.exists():
        return personal, "user"
    return None, None


def load(repo):
    path, location = locate(repo)
    if path is None:
        cfg = copy.deepcopy(DEFAULTS)
        cfg["_missing"] = True
        return cfg
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ConfigError(f"{CONFIG_PATH} is not valid JSON: {e}")
    cfg = _merge(DEFAULTS, raw)
    if cfg["git"]["provider"] not in PROVIDERS:
        raise ConfigError(f"git.provider must be one of {sorted(PROVIDERS)}")
    if cfg["checks"] is not None and not isinstance(cfg["checks"], list):
        raise ConfigError("checks must be null or a list")
    cfg["_missing"] = False
    cfg["_path"], cfg["_location"] = str(path), location
    return cfg


def setup_gaps(cfg):
    """Config values a human still has to supply (never guessed)."""
    gaps = []
    if cfg.get("_missing"):
        return ["no configuration for this repository (run the setup flow)"]

    def walk(node, prefix):
        for key, value in node.items():
            name = f"{prefix}{key}"
            if isinstance(value, dict):
                walk(value, name + ".")
            elif key.startswith("_"):
                continue
            elif isinstance(value, str) and value.startswith("REPLACE_WITH"):
                gaps.append(name)

    walk({k: v for k, v in cfg.items() if not k.startswith("_")}, "")
    if not cfg["asana"]["eligibility"]["section_gid"]:
        gaps.append("asana.eligibility.section_gid (needed for `next`)")
    return gaps
