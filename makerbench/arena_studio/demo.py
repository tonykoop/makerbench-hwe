"""A separate read-only application: no workspace discovery or writer services."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from makerbench import __version__
from makerbench.code_cad_arena_runner import RETIRED_CHECKS, recorded_objective_rate

PACKAGE = Path(__file__).resolve().parent


def _without_retired(node: dict) -> dict:
    """A showcase row or trial as the demo shows it: retired checks dropped, rate rescored.

    The packaged showcase data predates the removal of wall thickness (2026-10-16) and is
    kept as recorded; the demo never shows or scores that check.
    """
    node = dict(node)
    gates = node.get("gates")
    if isinstance(gates, dict):
        node["gates"] = {k: v for k, v in gates.items() if k not in RETIRED_CHECKS}
        rate = recorded_objective_rate({"sub_scores": node["gates"], "objective_pass_rate": node.get("objective_pass_rate")})
        if rate is not None:
            node["objective_pass_rate"] = rate
    if isinstance(node.get("failed_checks"), list):
        node["failed_checks"] = [f for f in node["failed_checks"] if f.get("check") not in RETIRED_CHECKS]
    if isinstance(node.get("trials"), list):
        node["trials"] = [_without_retired(t) for t in node["trials"]]
    return node


def _case_without_retired(case: dict) -> dict:
    case = dict(case)
    if isinstance(case.get("rows"), list):
        case["rows"] = [_without_retired(r) for r in case["rows"]]
    return case


def create_demo_app(allowed_hosts=("127.0.0.1", "localhost")) -> FastAPI:
    content = json.loads((PACKAGE / "data/showcase.json").read_text())
    cases = {item["id"]: _case_without_retired(item) for item in content["cases"]}
    app = FastAPI(title="Arena Studio read-only demo", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.demo = True
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))

    @app.middleware("http")
    async def refuse_writes(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            return PlainTextResponse("This demo is read-only", status_code=403)
        return await call_next(request)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "demo": True}

    @app.get("/api/runs")
    def runs():
        return {"demo": True, "hero": content["hero"], "runs": [{"run_id": key, "title": case["title"]}
                                        for key, case in cases.items()]}

    @app.get("/api/runs/{run_id}/summary")
    def summary(run_id: str):
        if run_id not in cases:
            raise HTTPException(404, "Showcase not found")
        return cases[run_id]

    @app.get("/api/demo/assets/{asset_id}")
    def asset(asset_id: str):
        relative = content["assets"].get(asset_id)
        if relative is None:
            raise HTTPException(404, "Showcase image not found")
        path = PACKAGE / "demo_assets" / asset_id
        if path.is_file() and not path.resolve().is_relative_to(PACKAGE / "demo_assets"):
            raise HTTPException(404, "Showcase image not found")
        if not path.is_file():
            root = PACKAGE.parents[1]
            path = root / relative
            if not relative.startswith("docs/showcase/") or not path.resolve().is_relative_to(root / "docs/showcase"):
                raise HTTPException(404, "Showcase image not found")
        if path.is_symlink() or not path.is_file():
            raise HTTPException(404, "Showcase image not found")
        media_type = "image/jpeg" if path.suffix == ".jpg" else "image/png"
        return FileResponse(path, media_type=media_type, headers={"X-Content-Type-Options": "nosniff"})

    @app.get("/")
    def index():
        html = (PACKAGE / "static/index.html").read_text()
        return HTMLResponse(html.replace("<body>", '<body data-demo="true">'))

    app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")

    @app.get("/{path:path}")
    def unavailable(path: str):
        # Includes GET endpoints that prepare blind assets or preference reports.
        raise HTTPException(403, "This demo is read-only; endpoint unavailable")

    return app
