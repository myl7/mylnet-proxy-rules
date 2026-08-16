"""Write the rule file and run Ansible, as one serialised job.

The job is the only thing in the app that touches the working tree, so it holds a
lock for its whole run. It always takes a backup first and restores it when the
playbook fails, which keeps a rejected change from leaking into the next apply.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import threading
import urllib.error
import urllib.request
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from app.config import Settings
from app.rules import Rule, dump

TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"


class JobBusyError(RuntimeError):
    """Raised when an apply is requested while another one is running."""


@dataclass
class Job:
    """One run of the write, check, apply, publish sequence."""

    id: str
    kind: str
    state: str = "running"
    started_at: str = ""
    finished_at: str | None = None
    error: str | None = None
    changed: bool = False
    log: list[str] = field(default_factory=list)

    def append(self, line: str) -> None:
        self.log.append(line)

    def snapshot(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "changed": self.changed,
            "log": list(self.log),
        }


def read_file(settings: Settings) -> tuple[str, str]:
    """Return the current rule file text and its revision."""
    text = settings.rules_file.read_text(encoding="utf-8")
    return text, revision_of(text)


def revision_of(text: str) -> str:
    """Return a short content hash used to detect concurrent edits."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def check_renders(text: str) -> str | None:
    """Simulate the template injection and report why the result is unusable.

    ``clash.yaml.j2`` drops the first line and pastes the rest into a surrounding
    ``rules:`` list. Reproducing that here catches indentation mistakes before
    they reach a client.
    """
    fragment = "\n".join(text.split("\n")[1:])
    probe = f"rules:\n  - GEOSITE,private,DIRECT\n{fragment}\n  - MATCH,default\n"
    try:
        document = yaml.safe_load(probe)
    except yaml.YAMLError as error:
        return f"the rules would not render as valid YAML: {error}"

    if not isinstance(document, dict):
        return "the rules would not render as a YAML mapping"
    entries = document.get("rules")
    if not isinstance(entries, list):
        return "the rules would not render as a YAML list"
    for entry in entries:
        if not isinstance(entry, str):
            return f"the rules would render {entry!r}, which mihomo cannot read as a rule"
    return None


class ApplyRunner:
    """Owns the single apply slot and the recent job history."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._slot = threading.Lock()
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._jobs_lock = threading.Lock()
        self._current_id: str | None = None

    def get(self, job_id: str) -> Job | None:
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def current(self) -> Job | None:
        with self._jobs_lock:
            return self._jobs.get(self._current_id) if self._current_id else None

    def recent(self) -> list[Job]:
        with self._jobs_lock:
            return list(reversed(self._jobs.values()))

    def start(self, rules: list[Rule], dry_run: bool) -> Job:
        """Start a job, raising :class:`JobBusyError` when one is already running."""
        if not self._slot.acquire(blocking=False):
            raise JobBusyError("another apply is already running")

        job = Job(
            id=uuid.uuid4().hex[:12],
            kind="check" if dry_run else "apply",
            started_at=_now_iso(),
        )
        with self._jobs_lock:
            self._jobs[job.id] = job
            self._current_id = job.id
            while len(self._jobs) > self._settings.keep_jobs:
                self._jobs.popitem(last=False)

        thread = threading.Thread(target=self._run_guarded, args=(job, rules, dry_run), daemon=True)
        thread.start()
        return job

    def _run_guarded(self, job: Job, rules: list[Rule], dry_run: bool) -> None:
        try:
            self._run(job, rules, dry_run)
        except Exception as error:  # noqa: BLE001 - the job report is the only place to surface this
            job.append(f"unexpected failure: {error!r}")
            _finish(job, "failed", str(error))
        finally:
            with self._jobs_lock:
                if self._current_id == job.id:
                    self._current_id = None
            self._slot.release()

    def _run(self, job: Job, rules: list[Rule], dry_run: bool) -> None:
        settings = self._settings
        text = dump(rules)
        previous = settings.rules_file.read_text(encoding="utf-8")

        if text == previous and not dry_run:
            job.append("the rule file is unchanged, running the playbook anyway to converge the subscription")

        backup = self._write_backup(job, previous)
        self._write_rules(job, text)
        job.append(f"wrote {len(rules)} rules to {settings.rules_file}")

        code = self._ansible(job, check=True)
        if code != 0:
            self._restore(job, previous)
            _finish(job, "failed", f"the check run failed with exit code {code}, the rule file was restored")
            return

        if dry_run:
            self._restore(job, previous)
            job.append(f"dry run complete, the rule file is unchanged (backup at {backup})")
            _finish(job, "succeeded", None)
            return

        committed = self._commit(job, len(rules))

        code = self._ansible(job, check=False)
        if code != 0:
            self._restore(job, previous)
            if committed:
                self._commit(job, len(rules), revert=True)
            _finish(job, "failed", f"the playbook failed with exit code {code}, the rule file was restored")
            return

        job.changed = True
        self._push(job)

        problem = self._verify(job, rules)
        if problem:
            _finish(job, "failed", problem)
            return

        _finish(job, "succeeded", None)

    def _write_backup(self, job: Job, text: str) -> Path:
        settings = self._settings
        settings.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime(TIMESTAMP_FORMAT)
        path = settings.backup_dir / f"{settings.rules_file.stem}.{stamp}.{job.id}.yaml"
        path.write_text(text, encoding="utf-8")
        job.append(f"backed up the current rules to {path}")
        self._prune_backups()
        return path

    def _prune_backups(self) -> None:
        backups = sorted(self._settings.backup_dir.glob("*.yaml"))
        for path in backups[: max(0, len(backups) - self._settings.keep_backups)]:
            path.unlink(missing_ok=True)

    def _write_rules(self, job: Job, text: str) -> None:
        path = self._settings.rules_file
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o7777)
        os.replace(temporary, path)

    def _restore(self, job: Job, text: str) -> None:
        self._write_rules(job, text)
        job.append("restored the previous rules")

    def _ansible(self, job: Job, check: bool) -> int:
        settings = self._settings
        command = [
            "ansible-playbook",
            settings.playbook,
            "--tags",
            settings.tags,
            "--limit",
            settings.limit,
        ]
        if check:
            command += ["--check", "--diff"]

        environment = dict(os.environ)
        environment.update(ANSIBLE_FORCE_COLOR="0", ANSIBLE_NOCOLOR="1", PYTHONUNBUFFERED="1")
        job.append(f"running the {'check' if check else 'apply'} playbook")
        return self._stream(job, command, settings.ansible_dir, environment)

    def _commit(self, job: Job, count: int, revert: bool = False) -> bool:
        settings = self._settings
        if not settings.git_commit:
            return False

        relative = os.path.relpath(settings.rules_file, settings.secrets_dir)
        code, output = self._capture(["git", "-C", str(settings.secrets_dir), "add", "--", relative])
        if code != 0:
            job.append(f"warning: could not stage the rule file, skipping the commit: {output.strip()}")
            return False

        code, _ = self._capture(["git", "-C", str(settings.secrets_dir), "diff", "--cached", "--quiet", "--", relative])
        if code == 0:
            job.append("no git change to commit")
            return False

        message = (
            "chore(rules): restore clash extend rules after a failed apply"
            if revert
            else f"chore(rules): update clash extend rules to {count} entries"
        )
        command = [
            "git",
            "-C",
            str(settings.secrets_dir),
            "-c",
            f"user.name={settings.git_author_name}",
            "-c",
            f"user.email={settings.git_author_email}",
            "commit",
            "-m",
            message,
            "--",
            relative,
        ]
        return self._stream(job, command, settings.secrets_dir) == 0

    def _push(self, job: Job) -> None:
        settings = self._settings
        if not settings.git_push:
            return
        # Rebase onto the remote first, so a concurrent edit elsewhere never
        # turns the push into a rejected non-fast-forward. On any failure the
        # local commit stays and the push is skipped; the rules are already
        # live, so this is worth reporting but not worth aborting the apply.
        if self._stream(job, ["git", "-C", str(settings.secrets_dir), "pull", "--rebase"], settings.secrets_dir) != 0:
            self._capture(["git", "-C", str(settings.secrets_dir), "rebase", "--abort"])
            job.append("warning: could not rebase onto the remote, skipping the push")
            return
        if self._stream(job, ["git", "-C", str(settings.secrets_dir), "push"], settings.secrets_dir) != 0:
            job.append("warning: the push failed, the rules are applied but the commit is only local")

    def _verify(self, job: Job, rules: list[Rule]) -> str | None:
        url = self._settings.verify_url
        if not url:
            return None

        job.append(f"verifying the published subscription at {url}")
        # Cloudflare returns 403 to the default urllib user agent, so the
        # request presents itself as a browser.
        request = urllib.request.Request(  # noqa: S310 - the URL comes from our own config
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - the URL comes from our own config
                body = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            job.append(f"warning: could not fetch the subscription: {error}")
            return None

        try:
            document = yaml.safe_load(body)
        except yaml.YAMLError as error:
            return f"the published subscription is not valid YAML: {error}"

        published = document.get("rules") if isinstance(document, dict) else None
        if not isinstance(published, list):
            return "the published subscription has no rules list"

        missing = [rule.text for rule in rules if rule.text not in published]
        if missing:
            return f"the published subscription is missing {len(missing)} rules, starting with {missing[0]!r}"

        job.append(f"the published subscription carries all {len(rules)} rules")
        return None

    def _stream(self, job: Job, command: list[str], cwd: Path, environment: dict[str, str] | None = None) -> int:
        job.append("$ " + " ".join(shlex.quote(part) for part in command))
        with subprocess.Popen(  # noqa: S603 - the command is built from our own config
            command,
            cwd=str(cwd),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        ) as process:
            timer = threading.Timer(self._settings.apply_timeout, process.kill)
            timer.start()
            try:
                if process.stdout is not None:
                    for line in process.stdout:
                        job.append(line.rstrip("\n"))
                return process.wait()
            finally:
                timer.cancel()

    def _capture(self, command: list[str]) -> tuple[int, str]:
        result = subprocess.run(  # noqa: S603 - the command is built from our own config
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode, result.stdout + result.stderr


def _finish(job: Job, state: str, error: str | None) -> None:
    job.state = state
    job.error = error
    job.finished_at = _now_iso()
    if error:
        job.append(f"failed: {error}")
    else:
        job.append("done")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
