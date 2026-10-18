"""Side-by-side CADAM route smoke (#1103, epic #1099): one prompt + photo through three routes.

(a) CADAM · OpenRouter · Fable 5.1   (b) CADAM via CLI · claude · <best subscription model>
(c) CADAM via CLI · codex · GPT-6.1 Sol

This module only builds the one-job queue, and the 3-column comparison page from a finished run. It never
starts a run by itself: ``scripts/cadam_routes_smoke.py`` does, and only with an explicit go flag, because
the run spends OpenRouter budget and subscription quota.
"""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .arena_studio import control_room as cr

SCHEMA = "makerbench-cadam-routes-smoke-v1"
INSTRUMENT_ID = "sambuca"
DEFAULT_REFERENCE = "/mnt/c/Users/Tony/Documents/GitHub/instruments/strings/sambuca/images/SambucaPhotobyDonHitchcock_BritishMuseum.jpg"
DEFAULT_OUT = "/mnt/c/Users/Tony/Documents/StudioPipeline-Pilot/arena-nightly/cadam-routes-smoke"
FABLE = "anthropic/claude-fable-5.1"
CLAUDE_BEST = "anthropic/claude-fable-5.1"
CODEX_BEST = "openai/gpt-6.1-sol"
OPENROUTER_CAP_USD = 3.0

NOT_APPLES = (
    "These are three separate entrants and are never merged into one score. Each CLI route also carries its own "
    "built-in instructions as part of the request, so the comparison is not apples-to-apples."
)


def smoke_entrants(*, fable: str = FABLE, claude_model: str = CLAUDE_BEST, codex_model: str = CODEX_BEST) -> list[dict[str, Any]]:
    base = {"kind": "cadam", "backend": "openscad", "context_tier": "image", "timeout_s": 1200}
    return [
        {**base, "entrant_id": "cadam-openrouter-fable-5-1", "model_id": fable, "route": "openrouter", "max_cost_usd": OPENROUTER_CAP_USD},
        {**base, "entrant_id": "cadam-cli-claude", "model_id": claude_model, "route": "cli-bridge", "runtime": "claude"},
        {**base, "entrant_id": "cadam-cli-codex", "model_id": codex_model, "route": "cli-bridge", "runtime": "codex"},
    ]


def smoke_job(job_id: str, reference_image: str, entrants: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "job_id": job_id, "instrument_id": INSTRUMENT_ID, "reference_image": reference_image, "seed": 0,
        "budget_usd": OPENROUTER_CAP_USD + 1.0, "status": "queued", "entrants": [dict(e) for e in entrants],
    }


def smoke_queue(job: Mapping[str, Any]) -> dict[str, Any]:
    return {"schema": "makerbench-nightly-cad-queue-v1", "note": "CADAM routes side-by-side smoke (#1103)", "jobs": [dict(job)]}


def plan_lines(entrants: Sequence[Mapping[str, Any]], reference: str, out_dir: str) -> list[str]:
    lines = ["CADAM routes smoke plan (nothing is run in plan mode)", f"  reference photo: {reference}", f"  output page: {out_dir}"]
    for entrant in entrants:
        if entrant["route"] == "openrouter":
            cost = f"OpenRouter spend, capped at ${entrant['max_cost_usd']:.2f}"
        else:
            cost = f"{entrant['runtime']} subscription quota (no OpenRouter spend)"
        lines.append(f"  - {entrant['entrant_id']}: {entrant['model_id']} via {entrant['route']} -> {cost}")
    return lines


# ---- the comparison page ------------------------------------------------------------------------


def comparison(run_dir: Path, repo_root: Path, order: Optional[Sequence[str]] = None,
               labels: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    """One column per entrant (in smoke order), joined from Morning Review's view of the finished run."""
    run_dir = Path(run_dir).resolve()
    view = cr.review_view(run_dir, repo_root)
    designs = {d["entrant"]: d for d in view["designs"]}
    ids = list(order) if order else sorted(designs)
    labels = labels or {}
    columns = []
    for entrant_id in ids:
        design = designs.get(entrant_id)
        if design is None:
            columns.append({"entrant": entrant_id, "name": labels.get(entrant_id, entrant_id), "status": "missing", "failures": ["This entrant produced no design row."],
                            "checks": [], "rate": None, "cost": {"kind": "unknown"}, "time_s": None, "route": None, "files": {}})
            continue
        files = {}
        png = cr.trial_artifact(run_dir, repo_root, design["trial_id"], "png_path", ".png")
        scad = cr.native_path(run_dir, repo_root, design["trial_id"])
        if png is not None:
            files["preview"] = png
        if scad is not None:
            files["scad"] = scad
        column = {k: design[k] for k in ("entrant", "name", "status", "failures", "checks", "rate", "cost", "time_s", "route", "all_pass")}
        if design["name"] == entrant_id and entrant_id in labels:  # a failed turn leaves no label meta: use the configured one
            column["name"] = labels[entrant_id]
        columns.append({**column, "files": files})
    return {"schema": SCHEMA, "run_id": run_dir.name, "instrument_id": view.get("instrument_id"), "columns": columns,
            "reference": cr.reference_path(run_dir), "note": NOT_APPLES}


def _cost(cost: Mapping[str, Any]) -> str:
    if cost.get("kind") == "usd":
        return f"${cost['usd']:.4f} (OpenRouter-billed)"
    return {"subscription": "subscription quota", "unknown": "not recorded"}.get(str(cost.get("kind")), "not recorded")


def render_page(data: Mapping[str, Any], rel: Mapping[str, Mapping[str, str]]) -> str:
    """Self-contained HTML. ``rel`` maps entrant -> {"preview": relative path, "scad": relative path}."""
    esc = html.escape
    cells = []
    for col in data["columns"]:
        links = rel.get(col["entrant"], {})
        checks = "".join(
            f'<li class="{esc(c["state"])}">{esc(c["name"].replace("_", " "))}: {esc(c["state"])}</li>' for c in col["checks"]
        ) or "<li>no checks recorded</li>"
        failures = "".join(f"<li>{esc(str(f))}</li>" for f in col["failures"]) or "<li>none</li>"
        preview = f'<img src="{esc(links["preview"])}" alt="render of {esc(col["name"])}">' if "preview" in links else '<p class="hint">no render</p>'
        scad = f'<a href="{esc(links["scad"])}">source (.scad)</a>' if "scad" in links else ""
        rate = "not graded" if col["rate"] is None else f'{col["rate"]:.0%}'
        time_s = "not recorded" if col["time_s"] is None else f'{col["time_s"]:.0f} s'
        cells.append(
            f'<section><h2>{esc(col["name"])}</h2><p class="route">{esc(str(col["route"] or "route not recorded"))} · status {esc(col["status"])}</p>'
            f"{preview}<p><strong>Objective pass rate {esc(rate)}</strong> · cost {esc(_cost(col['cost']))} · {esc(time_s)}</p>"
            f"<h3>Checks</h3><ul>{checks}</ul><h3>Why not</h3><ul>{failures}</ul><p>{scad}</p></section>"
        )
    reference = '<p><img class="ref" src="reference.jpg" alt="reference photo"></p>' if data.get("reference") else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>CADAM routes smoke</title><style>"
        "body{font:15px system-ui;margin:1rem auto;max-width:1500px;padding:0 16px;color:#1b1b1b;background:#fafafa}"
        ".grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}@media(max-width:900px){.grid{grid-template-columns:1fr}}"
        "section{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px}img{max-width:100%}.ref{max-height:220px}"
        ".route,.hint{color:#555}.pass{color:#1a7f37}.fail{color:#b42318}.unknown{color:#8a6d00}li{margin:.15rem 0}"
        f"</style></head><body><h1>CADAM routes smoke · {esc(str(data.get('instrument_id') or ''))}</h1>"
        f"<p>{esc(data['note'])}</p>{reference}<div class=\"grid\">{''.join(cells)}</div>"
        f"<p class=\"hint\">Run {esc(data['run_id'])}. Generated by makerbench (#1103).</p></body></html>"
    )


def publish(run_dir: Path, out_dir: Path, repo_root: Path, order: Optional[Sequence[str]] = None,
            labels: Optional[Mapping[str, str]] = None) -> Path:
    """Write ``<out_dir>/<run_id>/index.html`` (+ copied renders/sources) and point ``<out_dir>/index.html`` at it."""
    data = comparison(run_dir, repo_root, order, labels)
    target = Path(out_dir) / data["run_id"]
    target.mkdir(parents=True, exist_ok=True)
    rel: dict[str, dict[str, str]] = {}
    for col in data["columns"]:
        folder = target / col["entrant"]
        for kind, src in col["files"].items():
            folder.mkdir(exist_ok=True)
            name = "preview.png" if kind == "preview" else "candidate.scad"
            shutil.copyfile(src, folder / name)
            rel.setdefault(col["entrant"], {})[kind] = f"{col['entrant']}/{name}"
    if data["reference"]:
        shutil.copyfile(data["reference"], target / "reference.jpg")
    page = render_page(data, rel)
    (target / "index.html").write_text(page, encoding="utf-8")
    summary = {k: v for k, v in data.items() if k not in {"reference"}}
    for col in summary["columns"]:
        col["files"] = rel.get(col["entrant"], {})
    (target / "comparison.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    (Path(out_dir) / "index.html").write_text(
        f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url={html.escape(data["run_id"])}/index.html">'
        f'<a href="{html.escape(data["run_id"])}/index.html">latest CADAM routes smoke</a>', encoding="utf-8")
    return target / "index.html"
