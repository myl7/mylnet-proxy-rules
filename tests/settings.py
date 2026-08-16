"""A settings builder shared by the tests."""

from pathlib import Path
from typing import Any

from app.config import DEFAULT_RULE_SETS, DEFAULT_TARGETS, Settings


def build_settings(**overrides: Any) -> Settings:
    """Return settings that touch nothing real unless the test points them somewhere."""
    values: dict[str, Any] = {
        "ansible_dir": Path("/nonexistent/ansible"),
        "secrets_dir": Path("/nonexistent/secrets"),
        "rules_file": Path("/nonexistent/secrets/clash_rules_extend.yaml"),
        "backup_dir": Path("/nonexistent/backups"),
        "playbook": "playbooks/proxy.yaml",
        "tags": "proxy-sub",
        "limit": "sg",
        "git_commit": True,
        "git_push": False,
        "git_author_name": "test",
        "git_author_email": "test@localhost",
        "verify_url": "",
        "targets": frozenset(DEFAULT_TARGETS),
        "rule_sets": frozenset(DEFAULT_RULE_SETS),
        "max_rules": 2000,
        "apply_timeout": 30,
        "keep_backups": 50,
        "keep_jobs": 20,
    }
    values.update(overrides)
    return Settings(**values)
