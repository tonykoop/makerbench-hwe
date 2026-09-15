"""Design workbench routes (#788 W3), under ``/api/workbench/``.

Kept out of ``app.py`` and attached with one call, like the delta lane. Every
filesystem path these routes touch resolves under ``runs/workbench/<design>``
through the store's containment helper; ids follow the task-id rule; POSTs
keep the app's same-origin and TrustedHost guards and additionally require a
JSON content type (G6). Bodies are size-capped (G7) and answers go through the
app's published response so no host path reaches the wire (G15).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from makerbench.entrant_sandbox import SandboxUnavailable
from makerbench.workbench_store import (
    MAX_FEEDBACK_BYTES,
    MAX_SOURCE_BYTES,
    MAX_TEXT_BYTES,
    Conflict,
    NotFound,
    TooLarge,
    WorkbenchError,
)

from .workbench import MAX_PARAMS, QueueFull, WorkbenchService

#: Revisions listed per design page (G7).
MAX_REVISIONS_PAGE = 200
_ARTIFACT_MEDIA = {
    ".png": "image/png",
    ".stl": "model/stl",
    ".glb": "model/gltf-binary",
    ".step": "application/step",
    ".stp": "application/step",
    ".json": "application/json",
    ".log": "text/plain",
}


def _text(limit: int, what: str):
    def check(value: Optional[str]) -> Optional[str]:
        if value is not None and len(value.encode("utf-8")) > limit:
            raise ValueError(f"{what} is longer than {limit} bytes")
        return value

    return check


class OriginTrial(BaseModel):
    run_id: str = Field(min_length=1, max_length=128)
    trial_id: str = Field(min_length=1, max_length=128)


class OriginMaster(BaseModel):
    instrument_id: str = Field(min_length=1, max_length=64)
    file: str = Field(min_length=1, max_length=255)


class OriginBlank(BaseModel):
    backend: str = "openscad"
    instrument_id: str = Field(default="blank", min_length=1, max_length=64)


class CreateDesignPayload(BaseModel):
    trial: Optional[OriginTrial] = None
    master: Optional[OriginMaster] = None
    blank: Optional[OriginBlank] = None
    title: str = ""
    voter: str = "tony"

    _title = field_validator("title")(_text(MAX_TEXT_BYTES, "title"))
    _voter = field_validator("voter")(_text(MAX_TEXT_BYTES, "voter"))


class CreateDraftPayload(BaseModel):
    parent_rev_id: Optional[str] = Field(default=None, max_length=64)
    source: Optional[str] = None
    params: Optional[dict[str, Any]] = None
    revise: Optional[dict[str, Any]] = None
    voter: str = "tony"

    _voter = field_validator("voter")(_text(MAX_TEXT_BYTES, "voter"))


class SaveRevisionPayload(BaseModel):
    draft_id: str = Field(min_length=1, max_length=64)
    note: str = ""

    _note = field_validator("note")(_text(MAX_TEXT_BYTES, "note"))


class CurationPayload(BaseModel):
    rev_id: Optional[str] = Field(default=None, max_length=64)
    pick: Optional[bool] = None
    title: Optional[str] = None
    note: Optional[str] = None
    voter: str = "tony"

    _title = field_validator("title")(_text(MAX_TEXT_BYTES, "title"))
    _note = field_validator("note")(_text(MAX_TEXT_BYTES, "note"))
    _voter = field_validator("voter")(_text(MAX_TEXT_BYTES, "voter"))


async def require_json(request: Request) -> None:
    """G6: workbench POSTs carry ``Content-Type: application/json`` or get 415."""

    content_type = request.headers.get("content-type", "")
    if not content_type.split(";", 1)[0].strip().lower() == "application/json":
        raise HTTPException(status_code=415, detail="workbench POSTs require Content-Type: application/json")
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_SOURCE_BYTES + 64 * 1024:
        raise HTTPException(status_code=413, detail="request body too large")


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, QueueFull):
        return HTTPException(status_code=429, detail=str(exc))
    if isinstance(exc, Conflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, TooLarge):
        return HTTPException(status_code=413, detail=str(exc))
    if isinstance(exc, SandboxUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (WorkbenchError, ValueError)):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


def register_workbench_routes(
    app: FastAPI,
    workbench: WorkbenchService,
    resolve_run_dir: Callable[[str], Path],
    publish: Callable[[Any], Any],
) -> None:
    """Attach the workbench endpoints. ``resolve_run_dir`` is the app's
    run-id resolver (a trial origin never takes a raw path); ``publish`` is the
    app's host-path redaction, applied to every SSE line."""

    store = workbench.store

    # --- designs ---------------------------------------------------------

    @app.get("/api/workbench/designs")
    def list_designs():
        return {"designs": store.list_designs()}

    @app.post("/api/workbench/designs", status_code=202, dependencies=[Depends(require_json)])
    def create_design(payload: CreateDesignPayload):
        chosen = [k for k in ("trial", "master", "blank") if getattr(payload, k) is not None]
        if len(chosen) != 1:
            raise HTTPException(status_code=400, detail="origin must be exactly one of trial, master or blank")
        kind = chosen[0]
        run_dir = None
        if kind == "trial":
            run_dir = resolve_run_dir(payload.trial.run_id)  # type: ignore[union-attr]
        origin = {kind: getattr(payload, kind).model_dump()}
        try:
            created = workbench.create_design(origin=origin, title=payload.title, voter=payload.voter, run_dir=run_dir)
        except Exception as exc:  # noqa: BLE001 - mapped below
            raise _http(exc)
        return {"design_id": created["design"]["design_id"], "draft_id": created["draft"]["draft_id"], "design": created["design"], "draft": created["draft"]}

    @app.get("/api/workbench/designs/{design_id}")
    def get_design(design_id: str):
        try:
            payload = workbench.get_design(design_id)
        except Exception as exc:  # noqa: BLE001
            raise _http(exc)
        payload["revisions"] = payload["revisions"][-MAX_REVISIONS_PAGE:]
        return payload

    # --- revisions -------------------------------------------------------

    @app.get("/api/workbench/designs/{design_id}/revisions/{rev_id}")
    def get_revision(design_id: str, rev_id: str):
        try:
            return store.read_revision(design_id, rev_id)
        except Exception as exc:  # noqa: BLE001
            raise _http(exc)

    @app.get("/api/workbench/designs/{design_id}/revisions/{rev_id}/source")
    def get_revision_source(design_id: str, rev_id: str):
        try:
            text = store.revision_source(design_id, rev_id)
        except Exception as exc:  # noqa: BLE001
            raise _http(exc)
        return PlainTextResponse(text, headers={"Cache-Control": "no-store"})

    @app.get("/api/workbench/designs/{design_id}/revisions/{rev_id}/parameters")
    def get_revision_parameters(design_id: str, rev_id: str):
        try:
            return workbench.parameters(design_id, rev_id)
        except Exception as exc:  # noqa: BLE001
            raise _http(exc)

    @app.get("/api/workbench/designs/{design_id}/revisions/{rev_id}/artifacts/{name}")
    def get_revision_artifact(design_id: str, rev_id: str, name: str):
        return _serve_artifact(store, design_id, "revisions", rev_id, name)

    @app.get("/api/workbench/designs/{design_id}/compare")
    def compare(design_id: str, a: str = Query(...), b: str = Query(...)):
        try:
            payload = store.compare(design_id, a, b)
            design = store.read_design(design_id)
            from makerbench.cad_params import changed_values, extract_parameters

            pa = extract_parameters(store.revision_source(design_id, a), design["backend"])
            pb = extract_parameters(store.revision_source(design_id, b), design["backend"])
            payload["parameter_delta"] = changed_values(pa, pb)
        except Exception as exc:  # noqa: BLE001
            raise _http(exc)
        return payload

    @app.post("/api/workbench/designs/{design_id}/revisions", status_code=201, dependencies=[Depends(require_json)])
    def save_revision(design_id: str, payload: SaveRevisionPayload):
        try:
            return workbench.save_revision(design_id, draft_id=payload.draft_id, note=payload.note)
        except Exception as exc:  # noqa: BLE001
            raise _http(exc)

    # --- drafts and jobs -------------------------------------------------

    @app.post("/api/workbench/designs/{design_id}/drafts", status_code=202, dependencies=[Depends(require_json)])
    def create_draft(design_id: str, payload: CreateDraftPayload):
        chosen = [k for k in ("source", "params", "revise") if getattr(payload, k) is not None]
        if len(chosen) != 1:
            raise HTTPException(status_code=400, detail="a draft is exactly one of source, params or revise")
        kind = chosen[0]
        try:
            if kind == "source":
                if len(payload.source.encode("utf-8")) > MAX_SOURCE_BYTES:  # type: ignore[union-attr]
                    raise TooLarge(f"source is longer than {MAX_SOURCE_BYTES} bytes")
                draft = workbench.start_edit(design_id, parent_rev_id=payload.parent_rev_id, source=payload.source, voter=payload.voter)  # type: ignore[arg-type]
            elif kind == "params":
                if len(payload.params) > MAX_PARAMS:  # type: ignore[arg-type]
                    raise TooLarge(f"at most {MAX_PARAMS} parameters per apply")
                draft = workbench.start_params(design_id, parent_rev_id=payload.parent_rev_id, values=payload.params, voter=payload.voter)  # type: ignore[arg-type]
            else:
                feedback = str((payload.revise or {}).get("feedback") or "")
                if len(feedback.encode("utf-8")) > MAX_FEEDBACK_BYTES:
                    raise TooLarge(f"feedback is longer than {MAX_FEEDBACK_BYTES} bytes")
                store.read_design(design_id)
                raise HTTPException(status_code=501, detail="model revisions are not in this slice (W6)")
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _http(exc)
        return {"draft_id": draft["draft_id"], "draft": draft}

    @app.get("/api/workbench/designs/{design_id}/drafts/{draft_id}")
    def get_draft(design_id: str, draft_id: str):
        try:
            return workbench.refresh_draft(design_id, draft_id)
        except Exception as exc:  # noqa: BLE001
            raise _http(exc)

    @app.get("/api/workbench/designs/{design_id}/drafts/{draft_id}/source")
    def get_draft_source(design_id: str, draft_id: str):
        try:
            text = store.draft_source(design_id, draft_id)
        except Exception as exc:  # noqa: BLE001
            raise _http(exc)
        return PlainTextResponse(text, headers={"Cache-Control": "no-store"})

    @app.get("/api/workbench/designs/{design_id}/drafts/{draft_id}/log/stream")
    def stream_draft_log(design_id: str, draft_id: str, tail: int = Query(200, ge=0, le=5000), follow: bool = Query(True)):
        try:
            workbench.refresh_draft(design_id, draft_id)
        except Exception as exc:  # noqa: BLE001
            raise _http(exc)

        def events():
            for line in workbench.stream_log(design_id, draft_id, tail=tail, follow=follow):
                if line.startswith("data: "):
                    value = json.loads(line[len("data: "):].strip())
                    yield f"data: {json.dumps(publish(value))}\n\n"
                else:
                    yield line

        return StreamingResponse(
            events(), media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/workbench/designs/{design_id}/drafts/{draft_id}/artifacts/{name}")
    def get_draft_artifact(design_id: str, draft_id: str, name: str):
        return _serve_artifact(store, design_id, "drafts", draft_id, name)

    @app.post("/api/workbench/designs/{design_id}/drafts/{draft_id}/cancel", dependencies=[Depends(require_json)])
    def cancel_draft(design_id: str, draft_id: str):
        try:
            return workbench.cancel(design_id, draft_id)
        except Exception as exc:  # noqa: BLE001
            raise _http(exc)

    # --- curation and sources -------------------------------------------

    @app.post("/api/workbench/designs/{design_id}/curation", status_code=201, dependencies=[Depends(require_json)])
    def add_curation(design_id: str, payload: CurationPayload):
        try:
            row = store.add_curation(design_id, rev_id=payload.rev_id, pick=payload.pick, title=payload.title, note=payload.note, voter=payload.voter)
        except Exception as exc:  # noqa: BLE001
            raise _http(exc)
        return {"row": row, "state": store.curation_state(design_id)}

    @app.get("/api/workbench/sources/masters")
    def list_masters(instrument: str = Query(..., min_length=1, max_length=64)):
        try:
            return {"instrument_id": instrument, "files": workbench.master_files(instrument)}
        except Exception as exc:  # noqa: BLE001
            raise _http(exc)


def _serve_artifact(store, design_id: str, kind: str, item_id: str, name: str):
    try:
        path = store.artifact_path(design_id, kind, item_id, name)
    except Exception as exc:  # noqa: BLE001
        raise _http(exc)
    media = _ARTIFACT_MEDIA.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, headers={"Cache-Control": "no-store"})
