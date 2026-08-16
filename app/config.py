"""Settings, all read from the environment.

Defaults describe the deployed layout on ``sg``: the two Ansible repositories are
bind mounted at the same paths inside and outside the container, so the relative
``playbooks/secrets`` symlink keeps resolving.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# The proxy groups declared in playbooks/templates/proxy/clash.yaml.j2, plus the
# built-in mihomo targets. Parsing the template would be more fragile than
# keeping this list in step by hand, because the groups are built inside a loop.
DEFAULT_TARGETS = ("DIRECT", "REJECT", "REJECT-DROP", "PASS", "default", "proxy", "cn", "ai")

# The rule-providers declared in the same template.
DEFAULT_RULE_SETS = ("anti-ad",)

DEFAULT_ROOT = Path("/srv/mylnet")


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the app needs to know about its host."""

    ansible_dir: Path
    secrets_dir: Path
    rules_file: Path
    backup_dir: Path
    playbook: str
    tags: str
    limit: str
    git_commit: bool
    git_push: bool
    git_author_name: str
    git_author_email: str
    verify_url: str
    targets: frozenset[str]
    rule_sets: frozenset[str]
    max_rules: int
    apply_timeout: int
    keep_backups: int
    keep_jobs: int

    @property
    def playbook_path(self) -> Path:
        return self.ansible_dir / self.playbook


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    return Path(value) if value else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_set(name: str, default: tuple[str, ...]) -> frozenset[str]:
    value = os.environ.get(name, "").strip()
    if not value:
        return frozenset(default)
    return frozenset(item.strip() for item in value.split(",") if item.strip())


def load_settings() -> Settings:
    """Build settings from the environment."""
    secrets_dir = _env_path("MYLNET_SECRETS_DIR", DEFAULT_ROOT / "mylnet-ansible-secrets")
    return Settings(
        ansible_dir=_env_path("MYLNET_ANSIBLE_DIR", DEFAULT_ROOT / "mylnet-ansible"),
        secrets_dir=secrets_dir,
        rules_file=_env_path("MYLNET_RULES_FILE", secrets_dir / "clash_rules_extend.yaml"),
        backup_dir=_env_path("MYLNET_BACKUP_DIR", Path("/var/lib/mylnet-proxy-rules/backups")),
        playbook=_env("MYLNET_PLAYBOOK", "playbooks/proxy.yaml"),
        tags=_env("MYLNET_TAGS", "proxy-sub"),
        limit=_env("MYLNET_LIMIT", "sg"),
        git_commit=_env_bool("MYLNET_GIT_COMMIT", True),
        git_push=_env_bool("MYLNET_GIT_PUSH", True),
        git_author_name=_env("MYLNET_GIT_AUTHOR_NAME", "mylnet-proxy-rules"),
        git_author_email=_env("MYLNET_GIT_AUTHOR_EMAIL", "mylnet-proxy-rules@localhost"),
        verify_url=_env("MYLNET_VERIFY_URL", ""),
        targets=_env_set("MYLNET_TARGETS", DEFAULT_TARGETS),
        rule_sets=_env_set("MYLNET_RULE_SETS", DEFAULT_RULE_SETS),
        max_rules=_env_int("MYLNET_MAX_RULES", 2000),
        apply_timeout=_env_int("MYLNET_APPLY_TIMEOUT", 600),
        keep_backups=_env_int("MYLNET_KEEP_BACKUPS", 50),
        keep_jobs=_env_int("MYLNET_KEEP_JOBS", 20),
    )
