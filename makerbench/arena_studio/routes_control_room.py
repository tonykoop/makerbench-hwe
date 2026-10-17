"""Control Room routes: Morning Review (#1105).

Included from ``app.py`` with a one-line call, like the delta and workbench lanes.
Nights are discovered under ``<repo>/runs`` only and addressed by folder name,
never by a filesystem path.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from . import control_room as cr
from .service import MATCHUP_MESH_MAX_BYTES, ArenaStudioService, _matchup_glb

NO_STORE = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}


class ReviewPayload(BaseModel):
    ranking: list[str]
    notes: dict[str, str] = Field(default_factory=dict)
    revise: list[str] = Field(default_factory=list)
    extra_turns: int = cr.DEFAULT_EXTRA_TURNS
    reviewer: str = "tony"


def register_control_room_routes(
    app: FastAPI,
    service: ArenaStudioService,
    *,
    queue_writes: bool = False,
    opener: Optional[cr.Opener] = None,
) -> None:
    runs_root = service.repo_root / "runs"
    open_file: cr.Opener = opener or cr.windows_opener
    revision_queue = runs_root / cr.REVISION_QUEUE

    def night_dir(run_id: str) -> Path:
        for path in cr.discover_nights(runs_root):
            if path.name == run_id:
                return path
        raise HTTPException(status_code=404, detail=f"No reviewable night called '{run_id}'")

    @app.get("/api/review/nights")
    def list_nights():
        return {"nights": [cr.night_entry(path) for path in cr.discover_nights(runs_root)],
                "queue_writes": queue_writes}

    @app.get("/api/review/{run_id}")
    def get_review(run_id: str):
        return {**cr.review_view(night_dir(run_id), service.repo_root), "queue_writes": queue_writes}

    @app.post("/api/review/{run_id}")
    def post_review(run_id: str, payload: ReviewPayload):
        run_dir = night_dir(run_id)
        view = cr.review_view(run_dir, service.repo_root)
        try:
            review = cr.build_review(view, payload.model_dump(), reviewer=payload.reviewer.strip()[:40] or "tony")
        except cr.ReviewError as error:
            raise HTTPException(status_code=400, detail=str(error))
        jobs = cr.next_night_jobs(review, run_dir)
        review["next_night_jobs"] = jobs
        if jobs and queue_writes:
            cr.append_to_queue(revision_queue, jobs)
            review["queued"] = True
        cr.save_review(run_dir, review)
        return {"saved": cr.REVIEW_FILE, "review": review}

    @app.get("/api/review/{run_id}/mesh/{trial_id}")
    def get_mesh(run_id: str, trial_id: str):
        path = cr.trial_artifact(night_dir(run_id), service.repo_root, trial_id, "stl_path", ".stl")
        if path is None or path.stat().st_size > MATCHUP_MESH_MAX_BYTES:
            raise HTTPException(status_code=404, detail="Mesh not found")
        stat = path.stat()
        data = _matchup_glb(str(path), stat.st_mtime_ns, stat.st_size)
        if data is None:
            raise HTTPException(status_code=404, detail="Mesh could not be converted")
        return Response(content=data, media_type="model/gltf-binary", headers=NO_STORE)

    @app.get("/api/review/{run_id}/render/{trial_id}")
    def get_render(run_id: str, trial_id: str):
        path = cr.trial_artifact(night_dir(run_id), service.repo_root, trial_id, "png_path", ".png")
        if path is None:
            raise HTTPException(status_code=404, detail="Render not found")
        with path.open("rb") as handle:
            if handle.read(8) != b"\x89PNG\r\n\x1a\n":
                raise HTTPException(status_code=404, detail="Render not found")
        return FileResponse(str(path), media_type="image/png", headers=NO_STORE)

    @app.get("/api/review/{run_id}/reference")
    def get_reference(run_id: str):
        path = cr.reference_path(night_dir(run_id))
        if path is None:
            raise HTTPException(status_code=404, detail="No reference photo for this night")
        return FileResponse(str(path), headers=NO_STORE)

    @app.get("/api/review/{run_id}/native/{trial_id}")
    def download_native(run_id: str, trial_id: str):
        path = cr.native_path(night_dir(run_id), service.repo_root, trial_id)
        if path is None:
            raise HTTPException(status_code=404, detail="No native file recorded for this design")
        return FileResponse(str(path), filename=path.name, media_type="application/octet-stream", headers=NO_STORE)

    @app.post("/api/review/{run_id}/open/{trial_id}")
    def open_native(run_id: str, trial_id: str):
        path = cr.native_path(night_dir(run_id), service.repo_root, trial_id)
        if path is None:
            raise HTTPException(status_code=404, detail="No native file recorded for this design")
        try:
            open_file(path)
        except Exception as error:  # noqa: BLE001 - shown to the reviewer, never fatal
            raise HTTPException(status_code=409, detail=f"Could not open {path.name}: {error}")
        return {"opened": path.name, "app": cr.NATIVE_APPS.get(path.suffix.lower(), "its app")}

    # No-JS fallback: plain server-rendered lists with links.
    def page(title: str, body: str) -> HTMLResponse:
        return HTMLResponse(
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="color-scheme" content="light dark"><title>{escape(title)}</title>'
            '<link rel="stylesheet" href="/static/studio.css"></head>'
            f'<body><main class="screen review-fallback">{body}</main></body></html>'
        )

    @app.get("/review", include_in_schema=False)
    def review_index_page():
        items = "".join(
            f'<li><a href="/review/{quote(n["run_id"])}">{escape(str(n["title"]))}</a> '
            f'<span class="hint">{escape(str(n["night"] or n["run_id"]))} · {n["designs"]} designs</span></li>'
            for n in (cr.night_entry(path) for path in cr.discover_nights(runs_root))
        )
        return page("Morning Review", f"<h1>Morning Review</h1><ol>{items or '<li>No nights to review yet.</li>'}</ol>"
                    '<p><a href="/#/review">Open the full review (needs JavaScript)</a></p>')

    @app.get("/review/{run_id}", include_in_schema=False)
    def review_night_page(run_id: str):
        view = cr.review_view(night_dir(run_id), service.repo_root)
        by_id = {d["trial_id"]: d for d in view["designs"]}
        saved = view.get("saved_review") or {}
        order = saved.get("ranking") if sorted(saved.get("ranking") or []) == sorted(by_id) else view["grader_order"]
        base = f"/api/review/{quote(run_id)}"
        rows = []
        for trial_id in order:
            d = by_id[trial_id]
            tid = quote(trial_id, safe="")
            links = []
            if d["has_mesh"]:
                links.append(f'<a href="{base}/mesh/{tid}">3D mesh (GLB)</a>')
            if d["has_render"]:
                links.append(f'<a href="{base}/render/{tid}">Render</a>')
            if d["native"]:
                links.append(f'<a href="{base}/native/{tid}">{escape(d["native"]["file"])}</a>')
            fails = "".join(f"<li>{escape(text)}</li>" for text in d["failures"])
            rows.append(
                f'<li><strong>{escape(d["name"])}</strong> <span class="hint">{escape(d["route"])}</span>'
                f' · {"passes every check" if d["all_pass"] else "fails a check"}'
                f'{f"<ul>{fails}</ul>" if fails else ""}<p>{" · ".join(links) or "No files recorded."}</p></li>'
            )
        return page(f"Morning Review: {view['title']}",
                    f'<p><a href="/review">All nights</a></p><h1>{escape(str(view["title"]))}</h1>'
                    f'<ol>{"".join(rows)}</ol>'
                    f'<p><a href="/#/review/{quote(run_id)}">Rank, add notes and save (needs JavaScript)</a></p>')

