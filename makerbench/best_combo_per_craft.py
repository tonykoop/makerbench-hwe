"""Best combo per craft — the Opportunity Matrix narrowed to a fabrication process (#183).

The Opportunity Matrix (``opportunity_matrix.py``) scores the whole
model × CAD × plugin cube. This module specializes it to the **fabrication
process / craft axis** Tony called out in #183 and answers the question the
workflow-track thesis (#100) is really about:

    *For each craft — wood turning, stave joinery, CNC router, laser cut,
    CNC plasma, sheet-metal brake, hand power tools — which (model × CAD ×
    plugin) coordinate is the winning combo, and which high-potential
    coordinates are still empty (the high-value vacancies worth standing up)?*

The instrument library is the low-stakes, effectively-endless corpus that
exercises these crafts (a wood flute, a stave drum, a sheet-metal horn). A
small **corpus manifest** (:data:`CORPUS`) maps each instrument repo to the
process(es) it exercises and the MakerBench domain/task family those processes
live in, so a ``WorkflowManifest`` produced against a real instrument brief can
be attributed to the right craft.

Like ``opportunity_matrix.py`` this module is deliberately **stdlib-only and
public-data-only**: it reads committed ``RunResults`` (for the per-model
capability prior) and committed ``WorkflowManifest`` files (for *demonstrated*
coordinates), and folds them onto the curated craft/corpus catalogs. No
pydantic / numpy import, so the report regenerates in any bare Python and the
front-page Opportunity Matrix surface (#120 / #121) can consume the emitted JSON
directly.

Scoring reuses the Opportunity Matrix weights and component sub-scores verbatim
(:mod:`makerbench.opportunity_matrix`); the only specialization is the
**evidence bucket** — manifests are grouped by the craft they exercise, and a
per-craft domain ``demand`` multiplier (the process's MakerBench domain) nudges
each coordinate's potential. A craft with no committed manifest evidence is
honestly reported as *all-vacancy* — pure potential, a ranked build backlog for
that craft — exactly the pre-workflow-track state the matrix reports today.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import opportunity_matrix as om

SCHEMA = "makerbench-best-combo-per-craft-v1"


# --- Craft / process axis (issue #183 part 1) -----------------------------------
#
# The fabrication-process dimension #183 breaks out of #120's generic "domain /
# field" axis. Each process names the MakerBench ``domain`` (an id in
# ``opportunity_matrix.DOMAINS``) it belongs to so the cube's domain ``demand``
# multiplier and the live-domain grouping carry over. ``live`` marks the
# processes whose MakerBench domain already has live graders (woodworking,
# sheet-metal, laser/vector); the rest are scaffolded.

PROCESSES: list[dict] = [
    {"id": "wood_turning", "label": "Wood turning", "domain": "woodworking",
     "live": True, "blurb": "Lathe-turned bores and bodies — flutes, duduk, drum shells."},
    {"id": "stave_joinery", "label": "Stave / joinery", "domain": "woodworking",
     "live": True, "blurb": "Coopered staves and glued joinery — fujara, stave drums, didgeridoo."},
    {"id": "cnc_router", "label": "CNC router", "domain": "woodworking",
     "live": True, "blurb": "Sheet-good pocketing and profiling — soundboards, lyre frames."},
    {"id": "laser_cut", "label": "Laser cut", "domain": "laser-2d",
     "live": True, "blurb": "2D vector cut/score — tongue-drum tops, soundholes, rosettes."},
    {"id": "cnc_plasma", "label": "CNC plasma", "domain": "sheet-metal",
     "live": True, "blurb": "Plasma-cut steel blanks — horn/drum sheet-metal blueprints."},
    {"id": "sheet_metal_brake", "label": "Sheet-metal brake / forming", "domain": "sheet-metal",
     "live": True, "blurb": "Brake bends and forming — trumpet/trombone/sax sheet-metal bells."},
    {"id": "hand_power_tools", "label": "Hand power tools", "domain": "woodworking",
     "live": True, "blurb": "Drill/router/sander hand operations — finish work across crafts."},
]


# --- Instrument-library corpus manifest (issue #183 part 2) ----------------------
#
# Each entry maps an instrument repo (the low-stakes, real-fabrication-intent
# brief source) to the craft/process(es) it exercises and the MakerBench domain
# + task families those processes are graded against. This is the registry that
# lets a workflow-track run pull a *real* brief from the library and have its
# WorkflowManifest attributed to the right craft. ``repo`` is matched
# case-insensitively against a manifest's instrument/corpus reference.

CORPUS: list[dict] = [
    {"repo": "flutes", "domain": "woodworking",
     "processes": ["wood_turning", "hand_power_tools"],
     "task_families": ["bore_resonance"]},
    {"repo": "duduk", "domain": "woodworking",
     "processes": ["wood_turning", "hand_power_tools"],
     "task_families": ["bore_resonance"]},
    {"repo": "shakuhachi", "domain": "woodworking",
     "processes": ["wood_turning", "hand_power_tools"],
     "task_families": ["bore_resonance"]},
    {"repo": "fujara", "domain": "woodworking",
     "processes": ["stave_joinery", "wood_turning"],
     "task_families": ["bore_resonance", "acoustics_resonator_volume"]},
    {"repo": "didgeridoo", "domain": "woodworking",
     "processes": ["stave_joinery", "hand_power_tools"],
     "task_families": ["bore_resonance"]},
    {"repo": "djembe", "domain": "woodworking",
     "processes": ["wood_turning", "stave_joinery"],
     "task_families": ["acoustics_resonator_volume"]},
    {"repo": "dundun", "domain": "woodworking",
     "processes": ["wood_turning", "stave_joinery"],
     "task_families": ["acoustics_resonator_volume"]},
    {"repo": "lyre", "domain": "woodworking",
     "processes": ["stave_joinery", "cnc_router", "laser_cut"],
     "task_families": ["acoustics_scale_length"]},
    {"repo": "kora", "domain": "woodworking",
     "processes": ["stave_joinery", "hand_power_tools"],
     "task_families": ["acoustics_scale_length"]},
    {"repo": "handpan", "domain": "sheet-metal",
     "processes": ["sheet_metal_brake", "hand_power_tools"],
     "task_families": ["acoustics_resonator_volume"]},
    {"repo": "tongue-drum", "domain": "laser-2d",
     "processes": ["laser_cut", "cnc_router"],
     "task_families": ["acoustics_resonator_volume"]},
    {"repo": "trumpet-sheetmetal", "domain": "sheet-metal",
     "processes": ["sheet_metal_brake", "cnc_plasma"],
     "task_families": ["bore_resonance"]},
    {"repo": "trombone-sheetmetal", "domain": "sheet-metal",
     "processes": ["sheet_metal_brake", "cnc_plasma"],
     "task_families": ["bore_resonance"]},
    {"repo": "saxophone-sheetmetal", "domain": "sheet-metal",
     "processes": ["sheet_metal_brake", "cnc_plasma"],
     "task_families": ["bore_resonance"]},
    {"repo": "spiral-conch-horn-sheetmetal", "domain": "sheet-metal",
     "processes": ["cnc_plasma", "sheet_metal_brake"],
     "task_families": ["bore_resonance"]},
]


@dataclass(frozen=True)
class CraftCatalog:
    """The craft/process + instrument-corpus catalog (overridable for tests / growth)."""

    processes: list[dict] = field(default_factory=lambda: [dict(p) for p in PROCESSES])
    corpus: list[dict] = field(default_factory=lambda: [dict(c) for c in CORPUS])

    def process_ids(self) -> set[str]:
        return {p["id"] for p in self.processes}

    def corpus_by_repo(self) -> dict[str, dict]:
        return {c["repo"].strip().lower(): c for c in self.corpus}


def _process_demand(process: dict, om_catalog: om.Catalog) -> float:
    """Domain ``demand`` multiplier for a process, from the Opportunity Matrix domains."""
    for domain in om_catalog.domains:
        if domain["id"] == process.get("domain"):
            return float(domain["demand"])
    return 1.0


# --- Evidence: attribute each manifest to the craft(s) it exercises -------------


def _str_processes(value, process_ids: set[str], out: set[str]) -> None:
    """Add any string in ``value`` (str or list) that names a known process id."""
    items = value if isinstance(value, list) else [value]
    for item in items:
        if isinstance(item, str) and item.strip().lower() in process_ids:
            out.add(item.strip().lower())


def manifest_processes(
    manifest: dict, craft_catalog: Optional[CraftCatalog] = None
) -> set[str]:
    """Resolve which craft/process(es) a WorkflowManifest exercises.

    A manifest declares its craft one of three ways (checked in order, unioned):

    1. An explicit ``process`` / ``processes`` field (top-level or under
       ``dossier``), naming a process id directly.
    2. ``dossier.fabrication_process`` / ``fabrication_processes``.
    3. An instrument reference (``corpus_ref`` / ``instrument`` / ``repo`` /
       ``dossier.instrument`` / ``dossier.repo``) resolved through the corpus
       manifest to that repo's declared processes.

    Returns the (possibly empty) set of recognized process ids. Defensive: any
    unexpected shape is simply ignored.
    """
    craft_catalog = craft_catalog or CraftCatalog()
    process_ids = craft_catalog.process_ids()
    dossier = manifest.get("dossier") if isinstance(manifest.get("dossier"), dict) else {}

    found: set[str] = set()
    for source in (
        manifest.get("process"),
        manifest.get("processes"),
        dossier.get("process"),
        dossier.get("processes"),
        dossier.get("fabrication_process"),
        dossier.get("fabrication_processes"),
    ):
        if source is not None:
            _str_processes(source, process_ids, found)

    by_repo = craft_catalog.corpus_by_repo()
    for ref in (
        manifest.get("corpus_ref"),
        manifest.get("instrument"),
        manifest.get("repo"),
        dossier.get("instrument"),
        dossier.get("repo"),
        dossier.get("corpus_ref"),
    ):
        if isinstance(ref, str) and ref.strip().lower() in by_repo:
            for pid in by_repo[ref.strip().lower()]["processes"]:
                if pid in process_ids:
                    found.add(pid)
    return found


def load_craft_evidence(
    runs_root: Optional[Path],
    om_catalog: Optional[om.Catalog] = None,
    craft_catalog: Optional[CraftCatalog] = None,
) -> dict[str, dict[tuple, om.Evidence]]:
    """Scan committed WorkflowManifests, bucketing evidence by craft → coordinate.

    Returns ``{process_id: {coord_key: Evidence}}`` where ``coord_key`` is the
    ``opportunity_matrix`` ``(model, cad, plugin, None)`` tuple. A manifest that
    exercises several crafts contributes its coordinate to each. Mirrors
    ``opportunity_matrix.load_manifest_evidence`` (same defensiveness, same coord
    resolution) but partitions by process instead of folding to one cube.
    """
    om_catalog = om_catalog or om.Catalog()
    craft_catalog = craft_catalog or CraftCatalog()
    if runs_root is None:
        return {}
    runs_root = Path(runs_root)
    if not runs_root.exists():
        return {}

    raw: dict[str, dict[tuple, dict]] = defaultdict(dict)
    for path in sorted(runs_root.rglob("*.json")):
        if path.name != "workflow_manifest.json" and not path.name.endswith(".manifest.json"):
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(manifest, dict):
            continue
        stack = manifest.get("stack") or {}
        model = om._match_axis(om_catalog.models, om._component_name(stack.get("orchestrator")), by_match=True)
        cad = om._match_axis(om_catalog.cad_hosts, om._component_name(stack.get("host_application")))
        bridge = om._component_name(stack.get("execution_bridge"))
        plugin = om._match_axis(om_catalog.plugins, bridge) or ("none" if model and cad else None)
        if not (model and cad and plugin):
            continue
        processes = manifest_processes(manifest, craft_catalog)
        if not processes:
            continue

        score = om._manifest_capability(manifest)
        autonomy = om._manifest_autonomy(manifest)
        key = (model, cad, plugin, None)
        for pid in processes:
            bucket = raw[pid].setdefault(key, {"cap": [], "aut": [], "src": []})
            if score is not None:
                bucket["cap"].append(score)
            bucket["aut"].append(autonomy)
            bucket["src"].append(str(path))

    evidence: dict[str, dict[tuple, om.Evidence]] = {}
    for pid, coords in raw.items():
        evidence[pid] = {}
        for key, bucket in coords.items():
            caps = bucket["cap"]
            auts = bucket["aut"]
            evidence[pid][key] = om.Evidence(
                capability=om._clamp01(max(caps)) if caps else 0.0,
                autonomy=om._clamp01(sum(auts) / len(auts)) if auts else 0.5,
                n_runs=len(bucket["src"]),
                sources=bucket["src"],
            )
    return evidence


# --- Per-craft coordinate scoring ----------------------------------------------


def _score_coordinates(
    process: dict,
    capability_prior: dict[str, float],
    evidence: dict[tuple, om.Evidence],
    om_catalog: om.Catalog,
) -> list[dict]:
    """Score every compatible model × CAD × plugin coordinate for one craft.

    Identical component math to ``opportunity_matrix.build_cube`` (so a coordinate
    scores the same as in the global cube), with the process's domain ``demand``
    as the multiplier and this craft's evidence bucket layered on top.
    """
    demand = _process_demand(process, om_catalog)
    coordinates: list[dict] = []
    for model in om_catalog.models:
        cap_prior = capability_prior.get(model["id"], 0.0)
        for cad in om_catalog.cad_hosts:
            for plugin in om_catalog.plugins:
                if not om._plugin_compatible(plugin, cad["id"]):
                    continue
                openness = om._stack_openness(cad, plugin)
                ease = om._stack_ease(cad, plugin)
                affinity = plugin["autonomy_affinity"]
                key = (model["id"], cad["id"], plugin["id"], None)
                ev = evidence.get(key)
                potential = om._clamp01(om._weighted(cap_prior, affinity, openness, ease) * demand)
                coord = {
                    "process": process["id"],
                    "model": model["id"],
                    "cad": cad["id"],
                    "plugin": plugin["id"],
                    "domain": None,
                    "openness": om._round(openness),
                    "ease": om._round(ease),
                    "capability_prior": om._round(cap_prior),
                    "potential_score": om._round(potential),
                    "has_evidence": ev is not None,
                    "n_runs": ev.n_runs if ev else 0,
                }
                if ev is not None:
                    opportunity = om._clamp01(
                        om._weighted(ev.capability, ev.autonomy, openness, ease) * demand
                    )
                    coord.update({
                        "capability": om._round(ev.capability),
                        "autonomy": om._round(ev.autonomy),
                        "opportunity_score": om._round(opportunity),
                        "score": om._round(opportunity),
                        "sources": ev.sources,
                    })
                else:
                    coord.update({
                        "capability": None,
                        "autonomy": None,
                        "opportunity_score": None,
                        "score": om._round(potential),
                        "is_vacancy": True,
                    })
                coordinates.append(coord)
    return coordinates


def _sort_proven(coords: list[dict]) -> list[dict]:
    proven = [c for c in coords if c["has_evidence"]]
    proven.sort(key=lambda c: (-(c["opportunity_score"] or 0.0), c["model"], c["cad"], c["plugin"]))
    return proven


def build_craft_report(
    results_dir: Optional[Path] = None,
    runs_root: Optional[Path] = None,
    *,
    om_catalog: Optional[om.Catalog] = None,
    craft_catalog: Optional[CraftCatalog] = None,
    top_n: int = 10,
) -> dict:
    """Assemble the best-combo-per-craft report.

    For each craft/process: score the full compatible coordinate space, then
    surface (a) the **winning combo** (highest opportunity score among
    coordinates with manifest evidence for this craft, else ``None``) and
    (b) the **high-value vacancies** — empty high-potential coordinates ranked
    by potential, the per-craft build backlog. Deterministic for fixed inputs.
    """
    om_catalog = om_catalog or om.Catalog()
    craft_catalog = craft_catalog or CraftCatalog()

    capability_prior = (
        om.load_model_capability(results_dir, om_catalog)
        if results_dir
        else {m["id"]: om._clamp01(float(m.get("capability_prior", 0.0))) for m in om_catalog.models}
    )
    evidence = load_craft_evidence(runs_root, om_catalog, craft_catalog)

    crafts: list[dict] = []
    total_proven = 0
    for process in craft_catalog.processes:
        coords = _score_coordinates(
            process, capability_prior, evidence.get(process["id"], {}), om_catalog
        )
        proven = _sort_proven(coords)
        vacancies = om.rank_vacancies(coords)
        total_proven += len(proven)
        crafts.append({
            "process": process["id"],
            "label": process["label"],
            "domain": process["domain"],
            "live": bool(process.get("live")),
            "blurb": process.get("blurb"),
            "n_coordinates": len(coords),
            "n_proven": len(proven),
            "n_vacancies": len(vacancies),
            "winner": proven[0] if proven else None,
            "top_proven": proven[:top_n],
            "top_vacancies": vacancies[:top_n],
        })

    return {
        "schema": SCHEMA,
        "weights": dict(om.WEIGHTS),
        "max_artifact_score": om.MAX_ARTIFACT_SCORE,
        "axes": {
            "model": [{"id": m["id"], "label": m["label"]} for m in om_catalog.models],
            "cad": [{"id": c["id"], "label": c["label"]} for c in om_catalog.cad_hosts],
            "plugin": [{"id": p["id"], "label": p["label"]} for p in om_catalog.plugins],
            "process": [
                {"id": p["id"], "label": p["label"], "domain": p["domain"], "live": bool(p.get("live"))}
                for p in craft_catalog.processes
            ],
        },
        "corpus": [dict(c) for c in craft_catalog.corpus],
        "counts": {
            "processes": len(craft_catalog.processes),
            "corpus_instruments": len(craft_catalog.corpus),
            "crafts_with_evidence": sum(1 for c in crafts if c["n_proven"]),
            "proven": total_proven,
        },
        "crafts": crafts,
    }


def coordinate_label(coord: dict, om_catalog: Optional[om.Catalog] = None) -> str:
    """``Model × CAD × Plugin`` label for a coordinate (delegates to the matrix)."""
    return om.coordinate_label(coord, om_catalog)
