"""Opportunity Matrix ("CAD Lore") — the scored model x CAD x plugin cube (#120).

A weighted, multi-axis index over the space of AI-HWE workflow *combinations*. It
ranks which coordinates are the highest-leverage proven stacks and which are empty
**plugin vacancies** — the gaps most worth building next.

The module is deliberately stdlib-only and public-data-only, mirroring
``delta_dossier.py`` and ``site/build_data.py``: it reads committed ``RunResults``
bundles (for the model-capability prior) and any committed ``WorkflowManifest``
files (for *demonstrated* coordinates), and folds them onto a curated axis catalog.
No pydantic / numpy import, so the cube regenerates in any bare Python and the
website / HF Space (#98) can consume the emitted JSON directly.

Axes (3D, with a pluggable optional 4th):
  A — LLM model            (StackDescriptor.orchestrator)
  B — CAD/geometry host    (StackDescriptor.host_application)
  C — plugin / bridge      (StackDescriptor.execution_bridge; absence = vacancy)
  D — domain / field       (optional; off by default to keep the cube tractable)
  D' — fabrication process (optional alternative 4th axis; the explicit *craft* —
                            wood_turning, stave_joinery, cnc_router, laser_cut,
                            cnc_plasma, sheet_metal_brake, hand_power_tools — that
                            #183 calls out, where "best combo per craft" lives;
                            ``dossier.fabrication_process``). Mutually exclusive
                            with axis D.

Scoring — each coordinate gets four 0..1 sub-scores combined by ``WEIGHTS``:
  capability      best demonstrated workflow-artifact score on that stack (/4).
  autonomy        how little human steering it needed (HII autonomy_ratio).
  openness        open, code-first stacks rank above closed GUI copilots.
  ease            inverse setup cost (a ``docker compose up`` stack beats a
                  bespoke license-gated enterprise install).

A cell that *has evidence* gets an ``opportunity_score`` (proven power). Every cell
also gets a ``potential_score`` (how good the combo could be, using the model's
capability prior). A high-potential cell with **no evidence** is a plugin
vacancy — the build backlog, ranked by potential.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


SCHEMA = "makerbench-opportunity-matrix-v1"

# Component weights for the opportunity / potential index. Capability dominates
# (a stack is worth ranking only if it can actually produce graded geometry), but
# autonomy and the openness/ease reproducibility pair carry real weight so the
# matrix rewards open, low-friction, low-steering stacks — the open-baseline goal.
WEIGHTS = {
    "capability": 0.40,
    "autonomy": 0.25,
    "openness": 0.20,
    "ease": 0.15,
}

MAX_ARTIFACT_SCORE = 4.0  # the four-level grader ceiling; capability = score / 4.


# --- Axis catalog ---------------------------------------------------------------
#
# A curated, editable catalog of the combination space. Each axis entry carries the
# static priors that do not depend on run evidence (openness, ease, autonomy
# affinity, capability fallback). Demonstrated capability/autonomy are layered on
# top from WorkflowManifests + results at build time. Edit these tables as new
# stacks appear — that is the intended way to grow the cube.

# Axis A — LLM models. ``match`` tokens are matched (lower-cased substring) against
# a result/manifest ``model_identifier`` so the capability prior can be read from
# real leaderboard data; ``capability_prior`` is the fallback when no run matched.
MODELS: list[dict] = [
    {"id": "claude-opus", "label": "Claude Opus", "match": ["opus"], "capability_prior": 0.55},
    {"id": "claude-sonnet", "label": "Claude Sonnet", "match": ["sonnet"], "capability_prior": 0.50},
    {"id": "claude-haiku", "label": "Claude Haiku", "match": ["haiku"], "capability_prior": 0.35},
    {"id": "gpt-5", "label": "GPT-5.x", "match": ["gpt-5", "codex"], "capability_prior": 0.50},
    {"id": "gemini-3", "label": "Gemini 3.x", "match": ["gemini", "antigravity"], "capability_prior": 0.45},
    {"id": "deepseek", "label": "DeepSeek", "match": ["deepseek"], "capability_prior": 0.35},
    {"id": "qwen", "label": "Qwen", "match": ["qwen"], "capability_prior": 0.35},
]

# Axis B — CAD / geometry hosts. openness: open-source + scriptable rank high,
# license-gated GUI suites rank low. ease: inverse stand-up cost (pip/apt vs a
# license + Windows-only install).
CAD_HOSTS: list[dict] = [
    {"id": "openscad", "label": "OpenSCAD", "openness": 1.00, "ease": 0.95},
    {"id": "cadquery", "label": "CadQuery", "openness": 1.00, "ease": 0.85},
    {"id": "build123d", "label": "build123d", "openness": 1.00, "ease": 0.80},
    {"id": "freecad", "label": "FreeCAD", "openness": 0.90, "ease": 0.60},
    {"id": "blender", "label": "Blender (bpy)", "openness": 0.90, "ease": 0.60},
    {"id": "onshape", "label": "Onshape", "openness": 0.30, "ease": 0.50},
    {"id": "fusion360", "label": "Fusion 360", "openness": 0.20, "ease": 0.30},
    {"id": "solidworks", "label": "SolidWorks", "openness": 0.10, "ease": 0.20},
]

# Axis C — plugin / bridge layer. ``hosts`` lists the CAD hosts a bridge applies to
# ("*" = any, i.e. code-first emission). ``autonomy_affinity`` is how autonomously
# an agent can drive that bridge (code-first and SDKs high; GUI copilots low). The
# empty / "none" bridge is code-first artifact emission — what the shipped core
# packs already do.
PLUGINS: list[dict] = [
    {"id": "none", "label": "code-first (no bridge)", "hosts": ["*"],
     "openness": 1.00, "ease": 1.00, "autonomy_affinity": 1.00},
    {"id": "python-sdk", "label": "Python SDK", "hosts": ["cadquery", "build123d", "freecad", "blender"],
     "openness": 1.00, "ease": 0.85, "autonomy_affinity": 0.90},
    {"id": "blender-mcp", "label": "Blender MCP", "hosts": ["blender"],
     "openness": 0.90, "ease": 0.70, "autonomy_affinity": 0.80},
    {"id": "freecad-mcp", "label": "FreeCAD MCP", "hosts": ["freecad"],
     "openness": 0.85, "ease": 0.65, "autonomy_affinity": 0.78},
    {"id": "onshape-api", "label": "Onshape API", "hosts": ["onshape"],
     "openness": 0.40, "ease": 0.55, "autonomy_affinity": 0.70},
    {"id": "adam", "label": "Adam copilot", "hosts": ["onshape", "fusion360"],
     "openness": 0.35, "ease": 0.55, "autonomy_affinity": 0.55},
    {"id": "leo-aura", "label": "Leo / Aura copilot", "hosts": ["fusion360", "solidworks"],
     "openness": 0.35, "ease": 0.55, "autonomy_affinity": 0.55},
    {"id": "fusion-api", "label": "Fusion 360 API", "hosts": ["fusion360"],
     "openness": 0.30, "ease": 0.40, "autonomy_affinity": 0.65},
    {"id": "solidworks-api", "label": "SolidWorks API", "hosts": ["solidworks"],
     "openness": 0.20, "ease": 0.30, "autonomy_affinity": 0.60},
]

# Optional Axis D — domain / field expertise. Off by default. ``demand`` nudges the
# potential of a coordinate toward domains where a proven stack is most valuable.
DOMAINS: list[dict] = [
    {"id": "3d-print", "label": "3D printing", "demand": 0.9},
    {"id": "sheet-metal", "label": "Sheet metal", "demand": 0.8},
    {"id": "laser-2d", "label": "Laser / vector", "demand": 0.8},
    {"id": "casting", "label": "Casting / foundry", "demand": 0.7},
    {"id": "robotics", "label": "Robotics", "demand": 0.8},
    {"id": "glass-ceramics", "label": "Glass / ceramics", "demand": 0.6},
    {"id": "woodworking", "label": "Woodworking / CNC", "demand": 0.7},
    {"id": "instruments", "label": "Instruments / acoustics", "demand": 0.6},
]

# Optional Axis D' — fabrication *process* / craft. The explicit process dimension
# called out in #183: a combo's power is process-dependent (a stack great at
# code-CAD laser nesting is not automatically great at lathe wood-turning), so this
# is the axis where "best combo per craft" surfaces. Mutually exclusive with the
# generic ``domain`` axis — it is the same 4th slot, narrowed from field to craft.
# ``domain`` rolls each process up to its parent ``DOMAINS`` id (for cross-linking
# the two views); ``demand`` is the per-craft value weight; ``match`` tokens let a
# manifest's ``dossier.fabrication_process`` resolve to a catalog id by substring.
PROCESSES: list[dict] = [
    {"id": "wood_turning", "label": "Wood turning (lathe)", "domain": "woodworking",
     "demand": 0.7, "match": ["wood_turning", "wood-turning", "turning", "lathe"]},
    {"id": "stave_joinery", "label": "Stave / joinery", "domain": "woodworking",
     "demand": 0.7, "match": ["stave_joinery", "stave", "joinery", "cooperage"]},
    {"id": "cnc_router", "label": "CNC router", "domain": "woodworking",
     "demand": 0.8, "match": ["cnc_router", "cnc-router", "router"]},
    {"id": "laser_cut", "label": "Laser cut", "domain": "laser-2d",
     "demand": 0.8, "match": ["laser_cut", "laser-cut", "laser"]},
    {"id": "cnc_plasma", "label": "CNC plasma", "domain": "sheet-metal",
     "demand": 0.8, "match": ["cnc_plasma", "cnc-plasma", "plasma"]},
    {"id": "sheet_metal_brake", "label": "Sheet-metal brake / forming", "domain": "sheet-metal",
     "demand": 0.8, "match": ["sheet_metal_brake", "sheet-metal-brake", "brake", "forming", "bending"]},
    {"id": "hand_power_tools", "label": "Hand / power tools", "domain": "woodworking",
     "demand": 0.6, "match": ["hand_power_tools", "hand-power-tools", "hand_tools", "power_tools", "handtool"]},
]


@dataclass(frozen=True)
class Catalog:
    """The axis catalog the cube is built over (overridable for tests / growth)."""
    models: list[dict] = field(default_factory=lambda: [dict(m) for m in MODELS])
    cad_hosts: list[dict] = field(default_factory=lambda: [dict(c) for c in CAD_HOSTS])
    plugins: list[dict] = field(default_factory=lambda: [dict(p) for p in PLUGINS])
    domains: list[dict] = field(default_factory=lambda: [dict(d) for d in DOMAINS])
    processes: list[dict] = field(default_factory=lambda: [dict(p) for p in PROCESSES])


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _round(value: Optional[float], places: int = 3) -> Optional[float]:
    return round(value, places) if isinstance(value, (int, float)) else None


def _component_name(component: Any) -> Optional[str]:
    """Pull the bare name out of a StackDescriptor slot (str or {name: ...})."""
    if isinstance(component, str):
        return component.strip().lower() or None
    if isinstance(component, dict):
        name = component.get("name")
        return str(name).strip().lower() if name else None
    return None


def _match_axis(catalog_entries: list[dict], raw: Optional[str], *, by_match: bool = False) -> Optional[str]:
    """Resolve a raw axis token to a catalog id by exact id then alias/substring."""
    if not raw:
        return None
    token = raw.strip().lower()
    for entry in catalog_entries:
        if entry["id"] == token:
            return entry["id"]
    for entry in catalog_entries:
        needles = entry.get("match", []) if by_match else [entry["id"], entry["label"].lower()]
        for needle in needles:
            if needle and needle.lower() in token:
                return entry["id"]
    return None


# --- Evidence loading -----------------------------------------------------------


def load_model_capability(results_dir: Path, catalog: Optional[Catalog] = None) -> dict[str, float]:
    """Per-model-family demonstrated capability prior, 0..1, from committed results.

    Reads every ``RunResults`` bundle under ``results_dir``, averages the blind-track
    grade scores for each catalog model family (matched by token), and divides by
    the grader ceiling. Models with no matching run keep the catalog fallback. This
    is the headline capability signal the issue calls for: "best MakerBench
    workflow-track artifact score achieved by that stack."
    """
    catalog = catalog or Catalog()
    scores: dict[str, list[float]] = defaultdict(list)
    for path in sorted(Path(results_dir).rglob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(doc, dict) or "results" not in doc:
            continue
        model_id = doc.get("model_identifier")
        if not model_id:
            continue
        family = _match_axis(catalog.models, str(model_id), by_match=True)
        if family is None:
            continue
        for row in doc.get("results", []):
            if (row.get("track") or "") != "blind":
                continue
            grade = row.get("grade") or {}
            score = grade.get("score")
            if grade.get("notes") == "agent_error" or not isinstance(score, (int, float)):
                continue
            scores[family].append(float(score))

    out: dict[str, float] = {}
    for model in catalog.models:
        mid = model["id"]
        if scores.get(mid):
            out[mid] = _clamp01((sum(scores[mid]) / len(scores[mid])) / MAX_ARTIFACT_SCORE)
        else:
            out[mid] = _clamp01(float(model.get("capability_prior", 0.0)))
    return out


@dataclass
class Evidence:
    """Demonstrated signal for one (model, cad, plugin[, domain]) coordinate."""
    capability: float  # best artifact score / 4
    autonomy: float    # HII autonomy_ratio (0.5 when undisclosed)
    n_runs: int
    sources: list[str]


def _coord_key(
    model: str, cad: str, plugin: str, domain: Optional[str], process: Optional[str] = None
) -> tuple:
    return (model, cad, plugin, domain, process)


def load_manifest_evidence(
    runs_root: Optional[Path], catalog: Optional[Catalog] = None,
    *, with_domain: bool = False, with_process: bool = False,
) -> dict[tuple, Evidence]:
    """Scan committed ``WorkflowManifest`` JSON for demonstrated coordinates.

    Defensive throughout: every field is optional and any unparseable file is
    skipped. Today the workflow track has yet to land manifests, so this typically
    returns ``{}`` — the cube is then all-potential (every cell a vacancy), which is
    exactly the pre-workflow-track state the matrix should report honestly.
    """
    catalog = catalog or Catalog()
    if runs_root is None:
        return {}
    runs_root = Path(runs_root)
    if not runs_root.exists():
        return {}

    raw: dict[tuple, dict] = {}
    for path in sorted(runs_root.rglob("*.json")):
        if path.name not in ("workflow_manifest.json",) and not path.name.endswith(".manifest.json"):
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(manifest, dict):
            continue
        stack = manifest.get("stack") or {}
        model = _match_axis(catalog.models, _component_name(stack.get("orchestrator")), by_match=True)
        cad = _match_axis(catalog.cad_hosts, _component_name(stack.get("host_application")))
        bridge = _component_name(stack.get("execution_bridge"))
        plugin = _match_axis(catalog.plugins, bridge) or ("none" if model and cad else None)
        if not (model and cad and plugin):
            continue
        domain = None
        process = None
        if with_domain:
            dossier = manifest.get("dossier") or {}
            domain = _match_axis(catalog.domains, dossier.get("fabrication_domain"))
        if with_process:
            dossier = manifest.get("dossier") or {}
            process = _match_axis(
                catalog.processes, dossier.get("fabrication_process"), by_match=True
            )

        score = _manifest_capability(manifest)
        autonomy = _manifest_autonomy(manifest)
        key = _coord_key(model, cad, plugin, domain, process)
        bucket = raw.setdefault(key, {"cap": [], "aut": [], "src": []})
        if score is not None:
            bucket["cap"].append(score)
        bucket["aut"].append(autonomy)
        bucket["src"].append(str(path))

    evidence: dict[tuple, Evidence] = {}
    for key, bucket in raw.items():
        caps = bucket["cap"]
        auts = bucket["aut"]
        evidence[key] = Evidence(
            capability=_clamp01(max(caps)) if caps else 0.0,
            autonomy=_clamp01(sum(auts) / len(auts)) if auts else 0.5,
            n_runs=len(bucket["src"]),
            sources=bucket["src"],
        )
    return evidence


def _manifest_capability(manifest: dict) -> Optional[float]:
    """Best artifact score / 4 from a manifest's dossier or metrics, if present."""
    dossier = manifest.get("dossier") or {}
    verification = dossier.get("verification") or {}
    for candidate in (
        verification.get("score"),
        (manifest.get("metrics") or {}).get("score"),
        manifest.get("score"),
    ):
        if isinstance(candidate, (int, float)):
            return _clamp01(float(candidate) / MAX_ARTIFACT_SCORE)
    return None


def _manifest_autonomy(manifest: dict) -> float:
    """HII autonomy_ratio from a manifest, defaulting to 0.5 when undisclosed."""
    hii = manifest.get("hii") or manifest.get("human_intervention_index") or {}
    if isinstance(hii, dict):
        ratio = hii.get("autonomy_ratio")
        if isinstance(ratio, (int, float)):
            return _clamp01(float(ratio))
    return 0.5


# --- Cube assembly --------------------------------------------------------------


def _stack_openness(cad: dict, plugin: dict) -> float:
    return _clamp01((cad["openness"] + plugin["openness"]) / 2.0)


def _stack_ease(cad: dict, plugin: dict) -> float:
    return _clamp01((cad["ease"] + plugin["ease"]) / 2.0)


def _weighted(capability: float, autonomy: float, openness: float, ease: float) -> float:
    return (
        WEIGHTS["capability"] * capability
        + WEIGHTS["autonomy"] * autonomy
        + WEIGHTS["openness"] * openness
        + WEIGHTS["ease"] * ease
    )


def _plugin_compatible(plugin: dict, cad_id: str) -> bool:
    hosts = plugin.get("hosts", [])
    return "*" in hosts or cad_id in hosts


def build_cube(
    results_dir: Optional[Path] = None,
    runs_root: Optional[Path] = None,
    *,
    catalog: Optional[Catalog] = None,
    with_domain: bool = False,
    with_process: bool = False,
) -> dict:
    """Assemble the scored opportunity cube over the compatible coordinate space.

    Returns a JSON-ready dict with the catalog, weights, every scored coordinate,
    and the ranked plugin-vacancy backlog. Deterministic for fixed inputs.

    The optional 4th axis is pluggable: ``with_domain`` adds the generic field axis,
    ``with_process`` adds the explicit fabrication-process / craft axis (#183). They
    are the *same* slot narrowed two different ways, so they are mutually exclusive;
    passing both raises ``ValueError``. When ``with_process`` is set the cube also
    carries a ``per_process`` summary — the winning combo and top vacancy per craft.
    """
    if with_domain and with_process:
        raise ValueError(
            "with_domain and with_process are mutually exclusive 4th axes "
            "(process is the domain slot narrowed from field to craft)"
        )
    catalog = catalog or Catalog()
    capability_prior = load_model_capability(results_dir, catalog) if results_dir else {
        m["id"]: _clamp01(float(m.get("capability_prior", 0.0))) for m in catalog.models
    }
    evidence = load_manifest_evidence(
        runs_root, catalog, with_domain=with_domain, with_process=with_process
    )

    if with_process:
        facet_iter: list = catalog.processes
    elif with_domain:
        facet_iter = catalog.domains
    else:
        facet_iter = [None]

    coordinates: list[dict] = []
    for model in catalog.models:
        cap_prior = capability_prior.get(model["id"], 0.0)
        for cad in catalog.cad_hosts:
            for plugin in catalog.plugins:
                if not _plugin_compatible(plugin, cad["id"]):
                    continue
                openness = _stack_openness(cad, plugin)
                ease = _stack_ease(cad, plugin)
                affinity = plugin["autonomy_affinity"]
                for facet in facet_iter:
                    domain_id = None
                    process_id = None
                    if facet is None:
                        demand = 1.0
                    elif with_process:
                        process_id = facet["id"]
                        domain_id = facet.get("domain")  # parent-domain rollup
                        demand = facet["demand"]
                    else:
                        domain_id = facet["id"]
                        demand = facet["demand"]
                    # The key's domain slot is the *identity* domain (None in
                    # process mode, where the rollup domain_id is metadata only),
                    # matching how load_manifest_evidence builds its keys.
                    key = _coord_key(model["id"], cad["id"], plugin["id"],
                                     None if with_process else domain_id, process_id)
                    ev = evidence.get(key)

                    potential = _clamp01(
                        _weighted(cap_prior, affinity, openness, ease) * demand
                    )
                    coord = {
                        "model": model["id"],
                        "cad": cad["id"],
                        "plugin": plugin["id"],
                        "domain": domain_id,
                        "process": process_id,
                        "openness": _round(openness),
                        "ease": _round(ease),
                        "capability_prior": _round(cap_prior),
                        "potential_score": _round(potential),
                        "has_evidence": ev is not None,
                        "n_runs": ev.n_runs if ev else 0,
                    }
                    if ev is not None:
                        opportunity = _clamp01(
                            _weighted(ev.capability, ev.autonomy, openness, ease) * demand
                        )
                        coord.update(
                            {
                                "capability": _round(ev.capability),
                                "autonomy": _round(ev.autonomy),
                                "opportunity_score": _round(opportunity),
                                "score": _round(opportunity),
                                "sources": ev.sources,
                            }
                        )
                    else:
                        coord.update(
                            {
                                "capability": None,
                                "autonomy": None,
                                "opportunity_score": None,
                                "score": _round(potential),
                                "is_vacancy": True,
                            }
                        )
                    coordinates.append(coord)

    vacancies = rank_vacancies(coordinates)
    proven = [c for c in coordinates if c["has_evidence"]]
    proven.sort(key=lambda c: (-(c["opportunity_score"] or 0.0), c["model"], c["cad"], c["plugin"]))

    cube = {
        "schema": SCHEMA,
        "weights": dict(WEIGHTS),
        "max_artifact_score": MAX_ARTIFACT_SCORE,
        "with_domain": with_domain,
        "with_process": with_process,
        "axes": {
            "model": [{"id": m["id"], "label": m["label"]} for m in catalog.models],
            "cad": [{"id": c["id"], "label": c["label"]} for c in catalog.cad_hosts],
            "plugin": [{"id": p["id"], "label": p["label"]} for p in catalog.plugins],
            "domain": [{"id": d["id"], "label": d["label"]} for d in catalog.domains]
            if with_domain else [],
            "process": [
                {"id": p["id"], "label": p["label"], "domain": p["domain"]}
                for p in catalog.processes
            ] if with_process else [],
        },
        "counts": {
            "coordinates": len(coordinates),
            "proven": len(proven),
            "vacancies": len(vacancies),
        },
        "coordinates": coordinates,
        "top_proven": proven[:20],
        "top_vacancies": vacancies[:20],
    }
    if with_process:
        cube["per_process"] = summarize_per_process(coordinates, catalog)
    return cube


def summarize_per_process(
    coordinates: list[dict], catalog: Optional[Catalog] = None
) -> list[dict]:
    """Per-craft strength / vacancy roll-up — the "best combo per craft" surface.

    For each fabrication process present in ``coordinates``, returns the winning
    proven combo (highest ``opportunity_score``) and the highest-potential empty
    coordinate (the per-craft build vacancy). This is the #183 narrowing of the
    Opportunity Matrix down to Tony's favorite crafts.
    """
    catalog = catalog or Catalog()
    order = {p["id"]: i for i, p in enumerate(catalog.processes)}
    by_proc: dict[str, list[dict]] = defaultdict(list)
    for coord in coordinates:
        pid = coord.get("process")
        if pid:
            by_proc[pid].append(coord)

    summary: list[dict] = []
    for pid, coords in by_proc.items():
        proven = sorted(
            (c for c in coords if c["has_evidence"]),
            key=lambda c: (-(c["opportunity_score"] or 0.0), c["model"], c["cad"], c["plugin"]),
        )
        vacant = sorted(
            (c for c in coords if not c["has_evidence"]),
            key=lambda c: (-(c["potential_score"] or 0.0), c["model"], c["cad"], c["plugin"]),
        )
        summary.append({
            "process": pid,
            "domain": coords[0].get("domain"),
            "n_proven": len(proven),
            "n_vacancies": len(vacant),
            "winner": proven[0] if proven else None,
            "top_vacancy": vacant[0] if vacant else None,
        })
    summary.sort(key=lambda r: order.get(r["process"], len(order)))
    return summary


def rank_vacancies(coordinates: list[dict]) -> list[dict]:
    """Empty (no-evidence) coordinates ranked by potential — the build backlog."""
    vacancies = [c for c in coordinates if not c["has_evidence"]]
    vacancies.sort(
        key=lambda c: (-(c["potential_score"] or 0.0), c["model"], c["cad"], c["plugin"])
    )
    return vacancies


def coordinate_label(coord: dict, catalog: Optional[Catalog] = None) -> str:
    """Human-readable ``Model x CAD x Plugin[ x Domain]`` label for a coordinate."""
    catalog = catalog or Catalog()
    labels = {
        "model": {m["id"]: m["label"] for m in catalog.models},
        "cad": {c["id"]: c["label"] for c in catalog.cad_hosts},
        "plugin": {p["id"]: p["label"] for p in catalog.plugins},
        "domain": {d["id"]: d["label"] for d in catalog.domains},
    }
    parts = [
        labels["model"].get(coord["model"], coord["model"]),
        labels["cad"].get(coord["cad"], coord["cad"]),
        labels["plugin"].get(coord["plugin"], coord["plugin"]),
    ]
    if coord.get("domain"):
        parts.append(labels["domain"].get(coord["domain"], coord["domain"]))
    return " × ".join(parts)
