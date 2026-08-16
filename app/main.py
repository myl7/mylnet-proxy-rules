"""HTTP API and static frontend for editing the mihomo extend rules.

There is no authentication here on purpose. The app listens on loopback and nginx
in front of it does the auth, matching how the other services on ``sg`` are set up.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.apply import ApplyRunner, JobBusyError, check_renders, read_file
from app.config import load_settings
from app.rules import Rule, RuleFormatError, dump, parse
from app.validate import NO_RESOLVE, RULE_TYPES, validate

settings = load_settings()
runner = ApplyRunner(settings)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="mylnet proxy rules", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def revalidate(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Make browsers revalidate everything.

    The assets carry no version in their names, so a cached ``app.js`` would
    otherwise survive a redeploy. Responses still carry an ETag, so revalidation
    usually costs one 304.
    """
    response = await call_next(request)
    response.headers.setdefault("Cache-Control", "no-cache")
    return response


class RuleModel(BaseModel):
    """One rule as the frontend sends and receives it."""

    type: str
    payload: str
    target: str
    options: str = ""
    note: str = ""

    def to_rule(self) -> Rule:
        return Rule(
            type=self.type.strip(),
            payload=self.payload.strip(),
            target=self.target.strip(),
            options=self.options.strip(),
            note=self.note.strip(),
        )

    @classmethod
    def from_rule(cls, rule: Rule) -> RuleModel:
        return cls(type=rule.type, payload=rule.payload, target=rule.target, options=rule.options, note=rule.note)


class RulesResponse(BaseModel):
    rules: list[RuleModel]
    revision: str


class ApplyRequest(BaseModel):
    rules: list[RuleModel]
    base_revision: str | None = Field(default=None, description="Revision the edit started from")
    dry_run: bool = False


@app.get("/api/meta")
def get_meta() -> dict[str, Any]:
    """Describe what the frontend is allowed to build, so the two cannot drift."""
    return {
        "rule_types": [
            {
                "name": spec.name,
                "placeholder": spec.placeholder,
                "hint": spec.hint,
                "allows_no_resolve": spec.allows_no_resolve,
            }
            for spec in RULE_TYPES
        ],
        "targets": sorted(settings.targets),
        "rule_sets": sorted(settings.rule_sets),
        "no_resolve": NO_RESOLVE,
        "max_rules": settings.max_rules,
        "rules_file": str(settings.rules_file),
        "playbook": f"{settings.playbook} --tags {settings.tags} --limit {settings.limit}",
    }


@app.get("/api/rules", response_model=RulesResponse)
def get_rules() -> RulesResponse:
    try:
        text, revision = read_file(settings)
        rules = parse(text)
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"cannot read {settings.rules_file}: {error}") from error
    except RuleFormatError as error:
        raise HTTPException(status_code=500, detail=f"cannot parse {settings.rules_file}: {error}") from error
    return RulesResponse(rules=[RuleModel.from_rule(rule) for rule in rules], revision=revision)


@app.post("/api/apply")
def post_apply(request: ApplyRequest) -> dict[str, object]:
    """Validate, then start the write and apply job."""
    rules = [item.to_rule() for item in request.rules]

    errors = validate(rules, settings)
    if errors:
        raise HTTPException(
            status_code=400,
            detail={"errors": [{"index": error.index, "message": error.message} for error in errors]},
        )

    try:
        text = dump(rules)
    except RuleFormatError as error:
        raise HTTPException(status_code=400, detail={"errors": [{"index": None, "message": str(error)}]}) from error

    problem = check_renders(text)
    if problem:
        raise HTTPException(status_code=400, detail={"errors": [{"index": None, "message": problem}]})

    try:
        _, revision = read_file(settings)
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"cannot read {settings.rules_file}: {error}") from error

    if request.base_revision and request.base_revision != revision:
        raise HTTPException(
            status_code=409,
            detail="the rule file changed since it was loaded, reload before saving",
        )

    try:
        job = runner.start(rules, dry_run=request.dry_run)
    except JobBusyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return job.snapshot()


@app.get("/api/jobs")
def get_jobs() -> dict[str, object]:
    return {"jobs": [_summary(job.snapshot()) for job in runner.recent()]}


@app.get("/api/jobs/current")
def get_current_job() -> dict[str, object]:
    job = runner.current()
    return job.snapshot() if job else {"id": None, "state": "idle"}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    job = runner.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id}")
    return job.snapshot()


@app.get("/healthz")
def get_health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def get_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def _summary(snapshot: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in snapshot.items() if key != "log"}


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
