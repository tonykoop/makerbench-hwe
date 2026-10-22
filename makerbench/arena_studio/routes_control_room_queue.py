"""HTTP adapter for Tonight's Queue; no task-enable or launch endpoint exists."""

from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .control_room_queue import QueueError, QueueStore


class QueueEdit(BaseModel):
    action: str
    job_id: str = ""
    revision: str
    order: list[str] = Field(default_factory=list)


class QueuePreflight(BaseModel):
    job_id: str


def register_queue_routes(app: FastAPI, service, *, runs_root: Path, queue_path: Path,
                          writes: bool, instruments_root: Optional[Path] = None):
    store = QueueStore(runs_root=runs_root, queue_path=queue_path, repo_root=service.repo_root,
                       writes=writes, instruments_root=instruments_root)
    app.state.control_room_queue = store

    def view():
        result = store.view(service.get_registry_tasks())
        for job in result["jobs"]:
            job["reference_url"] = f"/api/queue/reference/{quote(job['job_id'], safe='')}" if job.pop("has_reference") else None
        return result

    @app.get("/api/queue")
    def get_queue():
        try:
            return view()
        except QueueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/queue/edit")
    def edit_queue(payload: QueueEdit):
        try:
            store.mutate(**payload.model_dump())
            return view()
        except (QueueError, OSError) as exc:
            raise HTTPException(status_code=409, detail="Queue edit refused: " +
                                (str(exc) if isinstance(exc, QueueError) else "could not write the queue")) from exc

    @app.get("/api/queue/status")
    def get_status():
        try:
            return store.readiness()
        except QueueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/queue/preflight")
    def preflight_queue(payload: QueuePreflight):
        try:
            return store.preflight(payload.job_id)
        except QueueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail="Preflight could not finish; check the server setup.") from exc

    @app.get("/api/queue/reference/{job_id}")
    def reference(job_id: str):
        try:
            path = store.reference(job_id)
        except QueueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if path is None:
            raise HTTPException(status_code=404, detail="No plan reference image for this night.")
        return FileResponse(path, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
