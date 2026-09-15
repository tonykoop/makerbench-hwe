"""Delta-lane routes: agreement analytics (#699 D1/D2).

Kept out of ``app.py`` and included from it with a one-line call so this
lane's route additions never collide with atlas's edits to the core routes
in the same file.
"""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from . import doe
from .service import ArenaStudioService


def _split_csv(value: Optional[str]) -> Optional[list[str]]:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


class DoeQueuePayload(BaseModel):
    run_id: str
    instruments: list[str]
    models: list[str]
    levels: Optional[list[str]] = None
    context_tiers: Optional[list[str]] = None
    seeds: Optional[list[int]] = None
    budget_usd: float = 5.0
    max_cost_usd_by_model: Optional[dict[str, float]] = None
    # An existing doe_queue.json is only replaced when the client says so (409 otherwise).
    replace: bool = False


def register_delta_routes(
    app: FastAPI,
    service: ArenaStudioService,
    resolve_run_dir: Callable[[str], object],
) -> None:
    """Attach the analytics endpoints to ``app``.

    ``resolve_run_dir`` is the same run-id-or-path resolver ``app.py`` uses
    for its own routes, passed in so both modules agree on run lookup.
    """

    @app.get("/api/runs/{run_id}/leaderboard/ci")
    def get_leaderboard_ci(
        run_id: str,
        seed: int = Query(0),
        n_resamples: int = Query(1000, le=20000, gt=0),
    ):
        run_path = resolve_run_dir(run_id)
        try:
            return service.get_run_leaderboard_with_ci(
                run_path, seed=seed, n_resamples=n_resamples
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/runs/{run_id}/agreement/detailed")
    def get_agreement_detailed(run_id: str):
        run_path = resolve_run_dir(run_id)
        try:
            return service.get_run_agreement_with_caveats(run_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/runs/{run_id}/agreement/families")
    def get_agreement_families(run_id: str):
        run_path = resolve_run_dir(run_id)
        try:
            return {"run_id": run_id, "families": service.get_run_agreement_by_family(run_path)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # Story #697 D3: DoE Matrix Builder (preview only — never executes anything)
    @app.get("/api/doe/preview")
    def preview_doe_matrix(
        instruments: str = Query(..., description="Comma-separated instrument ids"),
        models: str = Query(..., description="Comma-separated model ids"),
        levels: Optional[str] = Query(None, description="Comma-separated levels, default L1-L4"),
        context_tiers: Optional[str] = Query(None, description="Comma-separated context tiers"),
        seeds: Optional[str] = Query(None, description="Comma-separated integer seeds"),
    ):
        try:
            seed_values = [int(s) for s in _split_csv(seeds)] if seeds else None
        except ValueError:
            raise HTTPException(status_code=400, detail=f"seeds must be integers, got {seeds!r}")
        try:
            return service.preview_doe_matrix(
                _split_csv(instruments) or [],
                _split_csv(models) or [],
                levels=_split_csv(levels),
                context_tiers=_split_csv(context_tiers),
                seeds=seed_values,
            )
        except doe.DoeValidationError as e:
            # Fix-your-input errors (Tony, 2026-09-14): 400, so a client can tell them from a server failure.
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/doe/queue")
    def write_doe_queue(payload: DoeQueuePayload):
        try:
            return service.write_doe_queue(
                payload.run_id,
                payload.instruments,
                payload.models,
                levels=payload.levels,
                context_tiers=payload.context_tiers,
                seeds=payload.seeds,
                budget_usd=payload.budget_usd,
                max_cost_usd_by_model=payload.max_cost_usd_by_model,
                replace=payload.replace,
            )
        except doe.DoeQueueExistsError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except doe.DoeValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
