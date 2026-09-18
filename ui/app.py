"""FastAPI app driving the existing pipeline scripts through a local,
single-page web UI. Two independent modes, both ending in the same
shared approve/apply flow:

- "dockerfile": scripts/generate_dockerfile.py -> decide.py -> apply.py
  (the original scope of this UI).
- "diagnose": scripts/diagnose.py -> decide.py -> apply.py - the general,
  natural-language DevOps request path. Added 2026-09-18 because it was
  already a real, working, validated capability of this project that the
  UI simply never exposed - not new functionality, a new front door onto
  existing functionality. See ui/README.md "Diagnose mode".

decide.py and apply.py are shared, unchanged, by both modes - apply.py in
particular has no idea which script produced the ApprovalRequest it's
executing (see docs/ARCHITECTURE.md "Apply step — scope").

See ui/README.md "Deviations from the spec" for the handful of places
this had to adapt to the pipeline's real shape rather than an assumed one.

Local tool only: binds to 127.0.0.1, no auth, in-memory state (no
database), one process. Never restart-safe across a server restart - a
run's state lives only in the RUNS dict below.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator

from . import pipeline

REPO_ROOT = pipeline.REPO_ROOT
STATIC_DIR = Path(__file__).resolve().parent / "static"
RUNS_SCRATCH_DIR = Path(__file__).resolve().parent / ".runs"

app = FastAPI(title="devops-agent codegen UI")

# run_id -> mutable run record (dict). No database, per spec - this is the
# entire persistence layer, and it does not survive a server restart.
RUNS: dict[str, dict] = {}

# generate/lint/verify all come out of ONE call to scripts/generate_dockerfile.py;
# diagnose/verify all come out of ONE call to scripts/diagnose.py (see
# pipeline.py's module docstring) - within a mode, none of these stages
# can be independently requested. Any one of a mode's group in `stages`
# runs the whole group.
FIRST_PHASE_GROUP = {
    "dockerfile": {"generate", "lint", "verify"},
    "diagnose": {"diagnose", "verify"},
}


class RunRequest(BaseModel):
    mode: Literal["dockerfile", "diagnose"] = "dockerfile"
    repo_path: str | None = None  # dockerfile mode: a GitHub `owner/repo` identifier - see ui/README.md "Deviations from the spec"
    app_type: str | None = None  # dockerfile mode only
    task_description: str | None = None  # diagnose mode only: free-text natural-language request
    stages: list[str] | None = None  # defaults per-mode below if omitted

    @model_validator(mode="after")
    def _default_stages(self) -> "RunRequest":
        if self.stages is None:
            self.stages = (
                ["generate", "lint", "verify", "approve", "apply"]
                if self.mode == "dockerfile"
                else ["diagnose", "verify", "approve", "apply"]
            )
        return self


class ApproveRequest(BaseModel):
    decided_by: str = "web-ui"


def _new_run_record(run_id: str, req: RunRequest) -> dict:
    run_dir = RUNS_SCRATCH_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return {
        "run_id": run_id,
        "mode": req.mode,
        "repo_path": req.repo_path,
        "app_type": req.app_type,
        "task_description": req.task_description,
        "stages_requested": list(req.stages),
        "approvals_path": run_dir / "approvals.jsonl",
        "changes_path": run_dir / "changes.jsonl",
        "approval_id": None,
        "pr_url": None,
        "cost_usd": None,
        "results": {},  # stage name -> StageResult.to_dict()
        "status": "running",  # running | awaiting_approval | ok | failed
        "failed_stage": None,
        "events": [],  # ordered SSE event log, replayed to late/reconnecting subscribers
    }


def _emit(run: dict, event: dict) -> None:
    run["events"].append(event)


def _emit_stage(run: dict, stage_result) -> None:
    _emit(run, stage_result.to_dict())


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/app-types")
def app_types() -> dict:
    """Reads codegen.BASE_IMAGES directly - never hardcoded, so a new app
    type added to services/codegen shows up here with no UI change."""
    from codegen import BASE_IMAGES

    return {
        "app_types": [
            {
                "name": name,
                "image": policy.image,
                "needs_install_step": policy.needs_install_step,
                "has_port": policy.port is not None,
            }
            for name, policy in sorted(BASE_IMAGES.items())
        ]
    }


@app.post("/api/run")
async def start_run(req: RunRequest) -> dict:
    if req.mode == "dockerfile":
        if not req.repo_path or not req.app_type:
            raise HTTPException(status_code=400, detail="dockerfile mode requires repo_path and app_type")
        try:
            pipeline.validate_target_repo(req.repo_path)
        except pipeline.RepoTargetError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

        from codegen import BASE_IMAGES

        if req.app_type not in BASE_IMAGES:
            raise HTTPException(status_code=400, detail=f"unknown app_type {req.app_type!r}; configured: {sorted(BASE_IMAGES)}")
    else:
        if not req.task_description or not req.task_description.strip():
            raise HTTPException(status_code=400, detail="diagnose mode requires a non-empty task_description")

    run_id = str(uuid.uuid4())
    run = _new_run_record(run_id, req)
    RUNS[run_id] = run
    asyncio.create_task(_execute_first_phase(run_id))
    return {"run_id": run_id}


async def _execute_first_phase(run_id: str) -> None:
    """Runs the mode's first-phase stage group (dockerfile:
    generate/lint/verify, diagnose: diagnose/verify) if requested at all,
    in a background task, then stops - approve/apply only ever run in
    response to an explicit POST to /api/runs/{run_id}/approve, never
    automatically."""
    run = RUNS[run_id]
    mode = run["mode"]
    try:
        if FIRST_PHASE_GROUP[mode] & set(run["stages_requested"]):
            if mode == "dockerfile":
                for s in ("generate", "lint", "verify"):
                    _emit(run, {"stage": s, "status": "running", "stdout": "", "stderr": "", "artifact": None})
                outcome = await asyncio.to_thread(
                    pipeline.run_generate,
                    approvals_path=run["approvals_path"],
                    target_repo=run["repo_path"],
                    app_type=run["app_type"],
                )
                _emit_stage(run, outcome.generate)
                _emit_stage(run, outcome.lint)
                _emit_stage(run, outcome.verify)
                run["results"]["generate"] = outcome.generate.to_dict()
                run["results"]["lint"] = outcome.lint.to_dict()
                run["results"]["verify"] = outcome.verify.to_dict()
                run["approval_id"] = outcome.approval_id
                run["cost_usd"] = outcome.cost_usd

                if not (outcome.generate.ok and outcome.lint.ok):
                    failed = "generate" if not outcome.generate.ok else "lint"
                    run["status"] = "failed"
                    run["failed_stage"] = failed
                    _finish(run, ok=False)
                    return
            else:
                for s in ("diagnose", "verify"):
                    _emit(run, {"stage": s, "status": "running", "stdout": "", "stderr": "", "artifact": None})
                outcome = await asyncio.to_thread(
                    pipeline.run_diagnose,
                    approvals_path=run["approvals_path"],
                    task_description=run["task_description"],
                )
                _emit_stage(run, outcome.diagnose)
                _emit_stage(run, outcome.verify)
                run["results"]["diagnose"] = outcome.diagnose.to_dict()
                run["results"]["verify"] = outcome.verify.to_dict()
                run["approval_id"] = outcome.approval_id
                run["cost_usd"] = outcome.cost_usd

                if not outcome.diagnose.ok:
                    run["status"] = "failed"
                    run["failed_stage"] = "diagnose"
                    _finish(run, ok=False)
                    return

        needs_approval = "approve" in run["stages_requested"] or "apply" in run["stages_requested"]
        if not needs_approval:
            run["status"] = "ok"
            _finish(run, ok=True)
            return

        if run["approval_id"] is None:
            run["status"] = "failed"
            run["failed_stage"] = "approve"
            first_stage = "generate" if mode == "dockerfile" else "diagnose"
            _emit(run, {"stage": "approve", "status": "failed", "stdout": "", "stderr": f"no approval id - {first_stage} must succeed first", "artifact": None})
            _finish(run, ok=False)
            return

        run["status"] = "awaiting_approval"
        _emit(run, {"stage": "approve", "status": "awaiting_approval", "stdout": "waiting for the Approve button", "stderr": "", "artifact": None})
    except Exception as exc:  # noqa: BLE001 - background task: never let an exception crash the server, always surface as a failed event
        run["status"] = "failed"
        run["failed_stage"] = run["failed_stage"] or ("generate" if mode == "dockerfile" else "diagnose")
        _emit(run, {"stage": run["failed_stage"], "status": "failed", "stdout": "", "stderr": pipeline.redact_secrets(str(exc)), "artifact": None})
        _finish(run, ok=False)


def _finish(run: dict, *, ok: bool) -> None:
    _emit(run, {"stage": "done", "status": "ok" if ok else "failed", "pr_url": run["pr_url"]})


@app.post("/api/runs/{run_id}/approve")
async def approve_run(run_id: str, req: ApproveRequest) -> dict:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run_id")
    if run["status"] != "awaiting_approval":
        raise HTTPException(status_code=409, detail=f"run is {run['status']!r}, not awaiting approval")
    asyncio.create_task(_execute_approve_and_apply(run_id, req.decided_by))
    return {"ok": True}


async def _execute_approve_and_apply(run_id: str, decided_by: str) -> None:
    run = RUNS[run_id]
    run["status"] = "running"
    try:
        _emit(run, {"stage": "approve", "status": "running", "stdout": "", "stderr": "", "artifact": None})
        approve_result = await asyncio.to_thread(
            pipeline.run_approve, approvals_path=run["approvals_path"], approval_id=run["approval_id"], decided_by=decided_by
        )
        _emit_stage(run, approve_result)
        run["results"]["approve"] = approve_result.to_dict()
        if not approve_result.ok:
            run["status"] = "failed"
            run["failed_stage"] = "approve"
            _finish(run, ok=False)
            return

        if "apply" not in run["stages_requested"]:
            run["status"] = "ok"
            _finish(run, ok=True)
            return

        _emit(run, {"stage": "apply", "status": "running", "stdout": "", "stderr": "", "artifact": None})
        apply_outcome = await asyncio.to_thread(
            pipeline.run_apply, approvals_path=run["approvals_path"], approval_id=run["approval_id"], changes_path=run["changes_path"]
        )
        _emit_stage(run, apply_outcome.apply)
        run["results"]["apply"] = apply_outcome.apply.to_dict()
        run["pr_url"] = apply_outcome.pr_url
        run["status"] = "ok" if apply_outcome.apply.ok else "failed"
        run["failed_stage"] = None if apply_outcome.apply.ok else "apply"
        _finish(run, ok=apply_outcome.apply.ok)
    except Exception as exc:  # noqa: BLE001
        run["status"] = "failed"
        run["failed_stage"] = run["failed_stage"] or "apply"
        _emit(run, {"stage": run["failed_stage"], "status": "failed", "stdout": "", "stderr": pipeline.redact_secrets(str(exc)), "artifact": None})
        _finish(run, ok=False)


@app.post("/api/runs/{run_id}/retry")
async def retry_run(run_id: str) -> dict:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run_id")
    if run["status"] != "failed":
        raise HTTPException(status_code=409, detail=f"run is {run['status']!r}, not failed")
    failed_stage = run["failed_stage"]
    run["failed_stage"] = None
    if failed_stage in FIRST_PHASE_GROUP[run["mode"]] or failed_stage is None:
        run["status"] = "running"
        asyncio.create_task(_execute_first_phase(run_id))
    elif failed_stage == "approve":
        run["status"] = "awaiting_approval"
    elif failed_stage == "apply":
        run["status"] = "running"
        asyncio.create_task(_retry_apply_only(run_id))
    return {"ok": True}


async def _retry_apply_only(run_id: str) -> None:
    run = RUNS[run_id]
    try:
        _emit(run, {"stage": "apply", "status": "running", "stdout": "", "stderr": "", "artifact": None})
        apply_outcome = await asyncio.to_thread(
            pipeline.run_apply, approvals_path=run["approvals_path"], approval_id=run["approval_id"], changes_path=run["changes_path"]
        )
        _emit_stage(run, apply_outcome.apply)
        run["results"]["apply"] = apply_outcome.apply.to_dict()
        run["pr_url"] = apply_outcome.pr_url
        run["status"] = "ok" if apply_outcome.apply.ok else "failed"
        run["failed_stage"] = None if apply_outcome.apply.ok else "apply"
        _finish(run, ok=apply_outcome.apply.ok)
    except Exception as exc:  # noqa: BLE001
        run["status"] = "failed"
        run["failed_stage"] = "apply"
        _emit(run, {"stage": "apply", "status": "failed", "stdout": "", "stderr": pipeline.redact_secrets(str(exc)), "artifact": None})
        _finish(run, ok=False)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run_id")
    return {
        "run_id": run["run_id"],
        "mode": run["mode"],
        "repo_path": run["repo_path"],
        "app_type": run["app_type"],
        "task_description": run["task_description"],
        "stages_requested": run["stages_requested"],
        "status": run["status"],
        "failed_stage": run["failed_stage"],
        "approval_id": run["approval_id"],
        "pr_url": run["pr_url"],
        "cost_usd": run["cost_usd"],
        "results": run["results"],
        "events": run["events"],
    }


@app.get("/api/stream/{run_id}")
async def stream_run(run_id: str) -> StreamingResponse:
    if run_id not in RUNS:
        raise HTTPException(status_code=404, detail="unknown run_id")

    async def event_source():
        sent = 0
        while True:
            run = RUNS.get(run_id)
            if run is None:
                return
            events = run["events"]
            while sent < len(events):
                yield f"data: {json.dumps(events[sent])}\n\n"
                sent += 1
            if events and events[-1].get("stage") == "done":
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(event_source(), media_type="text/event-stream")


# Mounted last so it never shadows the /api/* and / routes declared above.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
