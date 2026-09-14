"""Delta-lane routes: agreement analytics (#699 D1/D2).

Kept out of ``app.py`` and included from it with a one-line call so this
lane's route additions never collide with atlas's edits to the core routes
in the same file.
"""

from __future__ import annotations

from typing import Callable

from fastapi import FastAPI, HTTPException, Query

from .service import ArenaStudioService


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
