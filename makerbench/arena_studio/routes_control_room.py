"""Control Room routes: Morning Review (#1105) and New Run (#1114).

Included from ``app.py`` with a one-line call, like the delta and workbench lanes.
Nights are discovered under ``<repo>/runs`` only and addressed by folder name,
never by a filesystem path.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from . import control_room as cr
from . import control_room_newrun as nr
from . import instrument_repo as ir
from . import library_manifest as lm
from . import promote as pm
from .service import MATCHUP_MESH_MAX_BYTES, ArenaStudioService, _matchup_glb

NO_STORE = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}


class NewRunPayload(BaseModel):
    instrument: str = ""
    variants: list[str] = Field(default_factory=list)
    deliverable: str = "single"
    entrants: list[str] = Field(default_factory=list)
    brief: str = ""
    references: list[dict[str, Any]] = Field(default_factory=list)
    turns: int = 5
    cap_usd: float = 2.0
    stop_early: bool = True


class FillGapsPayload(BaseModel):
    instruments: list[str] = Field(default_factory=list)
    start_date: Optional[str] = None


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
    library_fetch: Optional[Callable[[], str]] = None,
    library_manifest: Optional[Path] = None,
    promote_publish: bool = False,
    promote_pr_opener: Optional[Callable[[Path, str, str, str], str]] = None,
) -> None:
    runs_root = service.repo_root / "runs"
    open_file: cr.Opener = opener or cr.windows_opener

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
        if queue_writes:
            # Reconcile even with no requests, so a design no longer sent back is withdrawn.
            try:
                review["queue"] = cr.append_to_queue(cr.confined_queue_path(runs_root), jobs, run_id=run_id)
            except cr.QueueError as error:
                raise HTTPException(status_code=409, detail=str(error))
            review["queued"] = bool(jobs)
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

    @app.post("/api/review/{run_id}/promote/{trial_id}")
    def promote_design(run_id: str, trial_id: str):
        """#1111: commit the chosen design to its instrument repo on a new branch."""
        run_dir = night_dir(run_id)
        view = cr.review_view(run_dir, service.repo_root)
        design = next((d for d in view["designs"] if d["trial_id"] == trial_id), None)
        if design is None:
            raise HTTPException(status_code=404, detail="Unknown design")
        instrument_id = str(view.get("instrument_id") or "")
        registry = service.get_registry_tasks()
        repo = ir.find_repo(service.instruments_root, instrument_id, ir.registry_repo_path(registry, instrument_id))
        if repo is None:
            raise HTTPException(status_code=409, detail="No local instrument repo for this night (start the Studio with --instruments-root).")
        stl = cr.trial_artifact(run_dir, service.repo_root, trial_id, "stl_path", ".stl")
        glb = None
        if stl is not None and stl.stat().st_size <= MATCHUP_MESH_MAX_BYTES:
            stat = stl.stat()
            glb = _matchup_glb(str(stl), stat.st_mtime_ns, stat.st_size)
        chosen = pm.Design(
            run_id=run_id, trial_id=trial_id, entrant=design["entrant"], name=design["name"], route=design["route"],
            instrument_id=instrument_id, checks=design["checks"], native=cr.native_path(run_dir, service.repo_root, trial_id),
            mesh_glb=glb, render=cr.trial_artifact(run_dir, service.repo_root, trial_id, "png_path", ".png"),
        )
        try:
            worktrees = nr.confined(runs_root, *cr.CONTROL_ROOM_PROMOTE_DIR.parts, "x").parent
        except nr.UnsafePath as error:
            raise HTTPException(status_code=409, detail=str(error))
        try:
            result = pm.promote(repo, chosen, worktrees=worktrees, publish=promote_publish,
                                pr_opener=promote_pr_opener or pm.gh_pr_opener)
        except pm.PromoteError as error:
            raise HTTPException(status_code=409, detail=str(error))
        return {**result, "repo": repo.name, "publish": promote_publish}

    # New Run (#1114). Nothing here launches a model except run-now, which needs --allow-live.
    nightly_queue = runs_root / "nightly-cad-queue.json"
    telemetry_store = str(service.repo_root / "data" / "sessions.jsonl")


    def cards() -> tuple[list[dict], Optional[dict]]:
        registry = service.get_registry_tasks()
        snapshot = lm.load_snapshot(runs_root)
        if snapshot is None:
            return nr.registry_instruments(registry), None
        return nr.library_instruments(snapshot["entries"], registry), snapshot

    def instruments() -> dict[str, dict]:
        return {card["id"]: card for card in cards()[0]}

    @app.get("/api/newrun/catalog")
    def newrun_catalog():
        items, snapshot = cards()
        return {
            **nr.catalog(items),
            "library": None if snapshot is None else {
                "source": snapshot.get("source"), "fetched_utc": snapshot.get("fetched_utc"), "count": len(items),
                "filters": {key: sorted(set(lm.KNOWN_FILTERS[key]) | set(values))
                            for key, values in lm.filter_options(snapshot["entries"]).items()},
            },
            "gaps": nr.gap_summary(items),
            "lights": nr.ready_lights(queue_writes=queue_writes, queue_path=nightly_queue),
            "queue_writes": queue_writes,
            "allow_live": service.allow_live,
        }

    @app.post("/api/newrun/library/refresh")
    def newrun_library_refresh():
        try:
            snapshot = lm.refresh(runs_root, manifest_path=library_manifest, fetch=library_fetch or lm.fetch_page)
        except (lm.LibraryError, OSError, ValueError) as error:
            raise HTTPException(status_code=502, detail=f"Could not refresh the library: {error}")
        return {"count": len(snapshot["entries"]), "fetched_utc": snapshot["fetched_utc"], "source": snapshot["source"]}

    def instrument_repo(instrument_id: str) -> Optional[Path]:
        card = instruments().get(instrument_id)
        if card is None:
            raise HTTPException(status_code=404, detail="Unknown instrument")
        repo_path = ir.registry_repo_path(service.get_registry_tasks(), card.get("arena_task"))
        return ir.find_repo(service.instruments_root, str(card.get("slug") or instrument_id), repo_path)

    @app.get("/api/newrun/instrument/{instrument_id}/variants")
    def newrun_variants(instrument_id: str):
        repo = instrument_repo(instrument_id)
        card = instruments()[instrument_id]
        return {**ir.variants(repo, flute=ir.is_flute(f"{card.get('slug') or instrument_id} {card.get('name')}")),
                "repo_found": repo is not None, "instruments_root": service.instruments_root is not None}

    @app.get("/api/newrun/instrument/{instrument_id}/brief")
    def newrun_brief(instrument_id: str, variant: Optional[str] = None):
        repo = instrument_repo(instrument_id)
        card = instruments()[instrument_id]
        return {**ir.brief(repo, name=str(card.get("name") or instrument_id), slug=str(card.get("slug") or instrument_id),
                           variant=variant, registry_brief=str(card.get("brief") or "")),
                "repo_found": repo is not None, "instruments_root": service.instruments_root is not None}

    @app.get("/api/newrun/instrument/{instrument_id}/image/{name}")
    def newrun_repo_image(instrument_id: str, name: str):
        repo = instrument_repo(instrument_id)
        path = ir.repo_file(repo, f"images/{name}") if repo is not None and name in ir.repo_images(repo) else None
        if path is None:
            raise HTTPException(status_code=404, detail="Image not found")
        return FileResponse(str(path), headers=NO_STORE)

    @app.post("/api/newrun/estimate")
    def newrun_estimate(payload: NewRunPayload):
        form = payload.model_dump()
        cards = instruments()
        try:
            nr.validate_form(form, cards, for_estimate=True)
            result = nr.estimate(form, telemetry_store=telemetry_store)
        except (nr.NewRunError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error))
        card = cards.get(form["instrument"])
        return {**result, "runner_gaps": nr.runner_gaps(form, card), "run_now_gaps": nr.run_now_gaps(form, card)}

    @app.post("/api/newrun/tonight")
    def newrun_tonight(payload: NewRunPayload):
        form = payload.model_dump()
        try:
            repo = None
            if any(ref.get("repo_image") for ref in form["references"]) and form["instrument"] in instruments():
                repo = instrument_repo(form["instrument"])
            built = nr.build_jobs(form, instruments=instruments(), runs_root=runs_root,
                                  task_reference=service.reference_image_path,
                                  repo_image=lambda name: ir.repo_file(repo, f"images/{name}")
                                  if repo is not None and name in ir.repo_images(repo) else None)
            plans = nr.write_plans(runs_root, built)
            gaps = built[0]["plan"]["runner_cannot_apply"]
            jobs = [item["job"] for item in built]
            if queue_writes and not gaps:
                return {"queued": True, "added": nr.append_jobs(runs_root, built), "plans": plans, "jobs": jobs}
            # Queue writes off, or the runner would drop settings: staged, never silently queued.
            draft = nr.write_draft(runs_root, built, staged_because=gaps if queue_writes else ())
            return {"queued": False, "staged_because": gaps if queue_writes else [], "draft": draft,
                    "plans": plans, "jobs": jobs}
        except nr.UnsafePath as error:
            raise HTTPException(status_code=409, detail=str(error))
        except (nr.NewRunError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error))

    @app.post("/api/newrun/fill-gaps")
    def newrun_fill_gaps(payload: FillGapsPayload):
        """#1110: one instrument per night, each with the default overnight matchup."""
        from datetime import date

        if lm.load_snapshot(runs_root) is None:
            raise HTTPException(status_code=400, detail="Refresh from the library first: CAD status comes from it.")
        today = date.today()
        try:
            start = date.fromisoformat(payload.start_date) if payload.start_date else today
        except ValueError:
            raise HTTPException(status_code=400, detail="start_date must be YYYY-MM-DD")
        if start < today:
            raise HTTPException(status_code=400, detail="The first night is in the past.")
        start_gap = nr.future_start_gap(start, today)
        cards = instruments()
        registry = service.get_registry_tasks()
        repos: dict[str, Optional[Path]] = {}

        def repo_for(card) -> Optional[Path]:
            if card["id"] not in repos:
                repos[card["id"]] = ir.find_repo(service.instruments_root, str(card.get("slug") or card["id"]),
                                                 ir.registry_repo_path(registry, card.get("arena_task")))
            return repos[card["id"]]

        def brief_for(card) -> str:
            # A linked task keeps its registry brief: that is what the nightly runner sends.
            if str(card.get("brief") or "").strip():
                return str(card["brief"])
            return ir.brief(repo_for(card), name=str(card.get("name")), slug=str(card.get("slug") or card["id"]))["text"]

        def hero_for(card) -> Optional[str]:
            if card.get("arena_task") and service.reference_image_path(card["id"]) is not None:
                return None  # the task's own reference image is used
            repo = repo_for(card)
            return "hero-render.png" if repo is not None and "hero-render.png" in ir.repo_images(repo) else None

        try:
            forms, skipped = nr.fill_gap_forms(payload.instruments, cards, brief_for=brief_for, hero_for=hero_for)
        except nr.NewRunError as error:
            raise HTTPException(status_code=400, detail=str(error))
        built = []
        for form in forms:
            repo = repo_for(cards[form["instrument"]])
            try:
                items = nr.build_jobs(form, instruments=cards, runs_root=runs_root,
                                      task_reference=service.reference_image_path,
                                      repo_image=lambda name, repo=repo: ir.repo_file(repo, f"images/{name}") if repo else None)
            except nr.UnsafePath as error:
                raise HTTPException(status_code=409, detail=str(error))
            except (nr.NewRunError, ValueError) as error:
                skipped.append({"instrument": form["instrument"], "reason": str(error)})
                continue
            for item in items:
                # The runner keeps queue order but has no dates: a later first night is a gap (staged).
                item["plan"].update({"preset": "fill-gaps", "order": len(built) + 1, "first_night": start.isoformat()})
                if start_gap:
                    item["plan"]["runner_cannot_apply"] = [*item["plan"]["runner_cannot_apply"], start_gap]
                built.append(item)
        if not built:
            raise HTTPException(status_code=400, detail="None of these instruments can be queued: "
                                + "; ".join(f"{s['instrument']}: {s['reason']}" for s in skipped))
        try:
            plans = nr.write_plans(runs_root, built)
            response = {"plans": plans, "skipped": skipped, "first_night": start.isoformat(),
                        "order": [item["plan"]["instrument_id"] for item in built], "jobs": [item["job"] for item in built]}
            # A night the runner would run differently from its plan is staged, never queued.
            runnable = [item for item in built if not item["plan"]["runner_cannot_apply"]]
            staged = [item for item in built if item["plan"]["runner_cannot_apply"]]
            staged_because = sorted({gap for item in staged for gap in item["plan"]["runner_cannot_apply"]})
            if queue_writes and runnable:
                response["ahead"] = nr.queued_ahead(nightly_queue)
                added = nr.append_jobs(runs_root, runnable)
                draft = nr.write_draft(runs_root, staged, staged_because=staged_because) if staged else None
                return {**response, "queued": True, "added": added, "draft": draft,
                        "staged": [item["plan"]["instrument_id"] for item in staged], "staged_because": staged_because}
            return {**response, "queued": False, "staged_because": staged_because if queue_writes else [],
                    "draft": nr.write_draft(runs_root, built, staged_because=staged_because if queue_writes else ())}
        except nr.UnsafePath as error:
            raise HTTPException(status_code=409, detail=str(error))

    @app.post("/api/newrun/run-now")
    def newrun_run_now(payload: NewRunPayload):
        if not service.allow_live:
            raise HTTPException(status_code=403, detail="Run now needs a Studio started with --allow-live. Add it to tonight instead.")
        form = payload.model_dump()
        cards = instruments()
        try:
            checked = nr.validate_form(form, cards)
        except nr.NewRunError as error:
            raise HTTPException(status_code=400, detail=str(error))
        gaps = nr.run_now_gaps(form, cards.get(checked["instrument_id"]))
        if gaps:  # never launch while silently dropping what Tony set
            raise HTTPException(status_code=400, detail=f"{nr.RUN_NOW_REFUSAL} It cannot carry: {'; '.join(gaps)}.")
        picks = checked["picks"]
        backends = {nr.entrant_for(tool, model).backend for tool, model in picks}
        if len(backends) != 1 or any(tool.kind == "cadam" for tool, _ in picks):  # CADAM is already a gap above
            raise HTTPException(status_code=400, detail="Run now takes entrants on one arena backend (no CADAM); mixed nights go through Add to tonight.")
        return service.launch_competition({
            "instruments": [checked["instrument_id"]],
            "models": [nr.entrant_for(tool, model).model_id for tool, model in picks],
            "backend": backends.pop(),
            "max_turns": checked["turns"],
            "live": True,
        })

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

