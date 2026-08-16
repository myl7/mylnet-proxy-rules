"""Tests for the apply job, against a stub ``ansible-playbook``.

These cover the part that can damage something: writing the file, deciding
whether to commit, and restoring the previous content when the playbook fails.
"""

import os
import subprocess
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from app.apply import ApplyRunner, Job, JobBusyError
from app.rules import Rule, dump
from tests.settings import build_settings

STUB = """#!/bin/sh
echo "stub ansible-playbook: $@"
case " $* " in
  *" --check "*) mode=check ;;
  *) mode=apply ;;
esac
if [ -f "$STUB_STATE/fail-$mode" ]; then
  echo "stub failing the $mode run"
  exit 2
fi
exit 0
"""

ORIGINAL = [
    Rule(type="DOMAIN-SUFFIX", payload="one.example.net", target="proxy"),
    Rule(type="DOMAIN-SUFFIX", payload="two.example.net", target="proxy"),
]
UPDATED = [*ORIGINAL, Rule(type="DOMAIN-SUFFIX", payload="example.com", target="proxy", note="new")]


def git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def wait_for(job: Job, timeout: float = 20.0) -> Job:
    deadline = time.monotonic() + timeout
    while job.state == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    return job


class ApplyRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

        self.secrets = root / "secrets"
        self.secrets.mkdir()
        self.rules_file = self.secrets / "clash_rules_extend.yaml"
        self.rules_file.write_text(dump(ORIGINAL), encoding="utf-8")

        git(self.secrets, "init", "--quiet", "--initial-branch=main")
        git(self.secrets, "add", "clash_rules_extend.yaml")
        git(
            self.secrets,
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@localhost",
            "commit",
            "--quiet",
            "-m",
            "init",
        )

        self.ansible = root / "ansible"
        self.ansible.mkdir()
        self.state = root / "state"
        self.state.mkdir()

        binaries = root / "bin"
        binaries.mkdir()
        stub = binaries / "ansible-playbook"
        stub.write_text(STUB, encoding="utf-8")
        stub.chmod(0o755)

        self.previous_path = os.environ["PATH"]
        os.environ["PATH"] = f"{binaries}{os.pathsep}{self.previous_path}"
        os.environ["STUB_STATE"] = str(self.state)
        self.addCleanup(self._restore_environment)

        self.settings = build_settings(
            ansible_dir=self.ansible,
            secrets_dir=self.secrets,
            rules_file=self.rules_file,
            backup_dir=root / "backups",
        )
        self.runner = ApplyRunner(self.settings)

    def _restore_environment(self) -> None:
        os.environ["PATH"] = self.previous_path
        os.environ.pop("STUB_STATE", None)

    def read_rules(self) -> str:
        return self.rules_file.read_text(encoding="utf-8")

    def commit_count(self) -> int:
        return int(git(self.secrets, "rev-list", "--count", "HEAD"))

    def test_applies_and_commits(self) -> None:
        job = wait_for(self.runner.start(UPDATED, dry_run=False))
        self.assertEqual(job.state, "succeeded", "\n".join(job.log))
        self.assertEqual(self.read_rules(), dump(UPDATED))
        self.assertTrue(job.changed)
        self.assertEqual(self.commit_count(), 2)
        self.assertIn("3 entries", git(self.secrets, "log", "-1", "--pretty=%s"))

    def test_dry_run_leaves_the_file_alone(self) -> None:
        job = wait_for(self.runner.start(UPDATED, dry_run=True))
        self.assertEqual(job.state, "succeeded", "\n".join(job.log))
        self.assertEqual(self.read_rules(), dump(ORIGINAL))
        self.assertFalse(job.changed)
        self.assertEqual(self.commit_count(), 1)

    def test_a_failed_check_restores_the_file_without_committing(self) -> None:
        (self.state / "fail-check").touch()
        job = wait_for(self.runner.start(UPDATED, dry_run=False))
        self.assertEqual(job.state, "failed")
        self.assertIn("check run failed", job.error or "")
        self.assertEqual(self.read_rules(), dump(ORIGINAL))
        self.assertEqual(self.commit_count(), 1)

    def test_a_failed_apply_restores_the_file_and_reverts_the_commit(self) -> None:
        (self.state / "fail-apply").touch()
        job = wait_for(self.runner.start(UPDATED, dry_run=False))
        self.assertEqual(job.state, "failed")
        self.assertIn("playbook failed", job.error or "")
        self.assertEqual(self.read_rules(), dump(ORIGINAL))
        self.assertFalse(job.changed)
        # One commit for the change, one restoring it, on top of the initial commit.
        self.assertEqual(self.commit_count(), 3)
        self.assertEqual(git(self.secrets, "show", "HEAD:clash_rules_extend.yaml"), dump(ORIGINAL).strip())

    def test_backs_up_the_previous_content(self) -> None:
        wait_for(self.runner.start(UPDATED, dry_run=False))
        backups = sorted(self.settings.backup_dir.glob("*.yaml"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), dump(ORIGINAL))

    def test_prunes_old_backups(self) -> None:
        runner = ApplyRunner(replace(self.settings, keep_backups=2))
        for index in range(4):
            rules = [*ORIGINAL, Rule(type="DOMAIN", payload=f"host{index}.example.com", target="proxy")]
            wait_for(runner.start(rules, dry_run=False))
        self.assertEqual(len(list(self.settings.backup_dir.glob("*.yaml"))), 2)

    def test_refuses_a_second_job_while_one_runs(self) -> None:
        job = self.runner.start(UPDATED, dry_run=False)
        with self.assertRaises(JobBusyError):
            self.runner.start(UPDATED, dry_run=False)
        wait_for(job)

    def test_skips_the_commit_when_nothing_changed(self) -> None:
        job = wait_for(self.runner.start(ORIGINAL, dry_run=False))
        self.assertEqual(job.state, "succeeded", "\n".join(job.log))
        self.assertEqual(self.commit_count(), 1)
        self.assertIn("no git change to commit", job.log)


if __name__ == "__main__":
    unittest.main()
