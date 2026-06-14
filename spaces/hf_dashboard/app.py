#!/usr/bin/env python3
"""app.py — MakerBench HF Space (Docker) dual-league dashboard server (#98).

A thin FastAPI shell over the stdlib data layer (``dashboard_data.py``). It serves:

  * the two-tab static UI (``static/index.html``);
  * Tab A data  — ``GET /api/leagues``    (dual_league.json, baked at build time);
  * Tab B index — ``GET /api/inspect``     (inspect_index.json);
  * Tab B detail — ``GET /api/run/{id}``   (run_details.json, or live if RUNS_ROOT set);
  * run artifacts for the viewer — ``GET /artifacts/{id}/{name}`` (glb/stl/step),
    with STEP transparently tessellated to glTF via ``geometry_convert`` when the
    headless geometry stack is present;
  * ``GET /healthz``.

Config via env (Docker-friendly):
  ``MB_DATA_DIR``   baked JSON payloads (default ``./data``)
  ``MB_RUNS_ROOT``  optional dir of run dirs; enables live detail + artifact serving
  ``MB_STATIC_DIR`` static assets (default ``./static``)
  ``PORT``          listen port (HF Spaces inject 7860)

The pure helpers (``load_payload``, ``resolve_artifact``, ``run_detail``) are
import-safe without FastAPI so they unit-test in bare Python; ``create_app()``
imports FastAPI lazily and raises a clear error if it is missing.

Security: serves only files under ``MB_RUNS_ROOT`` (path-escape guarded) and the
baked public payloads. Never reads ``private/oracles/`` or hidden seeds.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent

ARTIFACT_SUFFIXES = (".glb", ".gltf", ".stl", ".step", ".stp")


def _data_dir() -> Path:
    return Path(os.environ.get("MB_DATA_DIR", HERE / "data"))


def _static_dir() -> Path:
    return Path(os.environ.get("MB_STATIC_DIR", HERE / "static"))


def _runs_root() -> Path | None:
    value = os.environ.get("MB_RUNS_ROOT")
    return Path(value) if value else None


def load_payload(name: str) -> dict:
    """Read a baked payload (``leagues`` -> dual_league.json, etc.). {} if absent."""
    filename = {
        "leagues": "dual_league.json",
        "inspect": "inspect_index.json",
        "details": "run_details.json",
    }.get(name, name)
    path = _data_dir() / filename
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def run_detail(run_id: str) -> dict | None:
    """Detail for one run: baked first, else live from MB_RUNS_ROOT, else None."""
    baked = load_payload("details").get(run_id)
    if baked is not None:
        return baked
    runs_root = _runs_root()
    if runs_root is not None:
        run_dir = runs_root / run_id
        if (run_dir / "run.json").exists():
            from dashboard_data import build_run_detail  # local import: stdlib spine
            return build_run_detail(run_dir)
    return None


def resolve_artifact(run_id: str, name: str) -> Path | None:
    """Safely resolve ``runs_root/run_id/name`` for a 3D artifact, or None.

    Guards against path escape (``..``) and non-artifact suffixes, and confirms
    the resolved path stays inside the run dir.
    """
    runs_root = _runs_root()
    if runs_root is None:
        return None
    if Path(name).suffix.lower() not in ARTIFACT_SUFFIXES:
        return None
    run_dir = (runs_root / run_id).resolve()
    candidate = (run_dir / name).resolve()
    if run_dir not in candidate.parents and candidate != run_dir:
        return None
    return candidate if candidate.is_file() else None


def create_app():
    """Build the FastAPI app. Raises RuntimeError with guidance if FastAPI absent."""
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import FileResponse, JSONResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:  # pragma: no cover - exercised only in a bare env
        raise RuntimeError(
            "FastAPI is required to serve the Space. Install it "
            "(`pip install -r requirements.txt`) or run dashboard_data.py directly "
            "to bake the JSON payloads."
        ) from exc

    app = FastAPI(title="MakerBench HWE — dual-league dashboard", docs_url="/api/docs")

    @app.get("/healthz")
    def healthz():
        leagues = load_payload("leagues")
        return {"status": "ok", "total_runs": leagues.get("total_runs", 0)}

    @app.get("/api/leagues")
    def api_leagues():
        return JSONResponse(load_payload("leagues"))

    @app.get("/api/inspect")
    def api_inspect():
        return JSONResponse(load_payload("inspect"))

    @app.get("/api/run/{run_id}")
    def api_run(run_id: str):
        detail = run_detail(run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
        return JSONResponse(detail)

    @app.get("/artifacts/{run_id}/{name}")
    def api_artifact(run_id: str, name: str):
        path = resolve_artifact(run_id, name)
        if path is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        if path.suffix.lower() in (".step", ".stp"):
            from geometry_convert import step_to_glb
            glb = path.with_suffix(".converted.glb")
            if not glb.exists() and not step_to_glb(path, glb):
                # No headless geometry stack: hand back the raw B-Rep to download.
                return FileResponse(path, media_type="application/step")
            return FileResponse(glb, media_type="model/gltf-binary")
        return FileResponse(path)

    static = _static_dir()
    if static.is_dir():
        app.mount("/", StaticFiles(directory=str(static), html=True), name="static")
    return app


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "7860")))
