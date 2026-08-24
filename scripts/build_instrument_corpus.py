#!/usr/bin/env python3
"""Build the instrument-library workflow-corpus manifest (makerbench-hwe#183).

Scans Tony's musical-instrument design repos under the GitHub root and emits a
deterministic manifest mapping each instrument repo -> fabrication process(es)
-> MakerBench grading domain(s) -> the live task families that exercise them.

This is the corpus-manifest piece of the workflow-track thesis (#100 / #120):
instead of synthetic briefs, a workflow run can pull a *real* instrument brief
from this corpus, and "best (model x CAD x plugin) per craft" can be scored
against a real fabrication ground truth.

Design constraints (mirrors site/domains_data.py and site/build_data.py):
  * stdlib only, no third-party deps;
  * deterministic / byte-identical output for an unchanged corpus snapshot;
  * never reads private/oracles or held-out seeds -- the instrument repos are a
    public design library, not benchmark gold.

The instrument repos live *outside* this repository, so the committed manifest
(tasks/instrument_corpus.json) is a snapshot. Regenerate it on a machine that
has the library checked out:

    python scripts/build_instrument_corpus.py \
        --root /mnt/c/Users/<you>/Documents/GitHub/instruments \
        --out tasks/instrument_corpus.json

`--check` regenerates in memory and fails if the committed manifest has drifted.

The process axis vocabulary and the manifest schema are documented in
docs/INSTRUMENT_WORKFLOW_CORPUS.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = "0.1"

# --- The fabrication-process axis (#183) ------------------------------------
# The seven craft/process surfaces Tony called out. A workflow combo's power is
# process-dependent, so this axis is where "best combo per craft" lives.
PROCESS_AXIS = [
    {
        "id": "wood_turning",
        "title": "Wood turning",
        "blurb": "Solid-billet lathe work and headstock-driven deep-bore drilling "
                 "(bored flutes, turned bodies).",
    },
    {
        "id": "stave_joinery",
        "title": "Stave / joinery",
        "blurb": "Segmented, mitred and coopered shells; compound-angle joinery "
                 "(stave drums, segmented bowls, sectioned bores).",
    },
    {
        "id": "cnc_router",
        "title": "CNC router",
        "blurb": "Sheet-stock pocketing, nesting and dogbone relief from "
                 "parametric DXF/CAM (bodies, panels, jigs).",
    },
    {
        "id": "laser_cut",
        "title": "Laser cut",
        "blurb": "Kerf-aware 2D vector cutting, slots and engraving "
                 "(tongue slots, panels, registration art).",
    },
    {
        "id": "cnc_plasma",
        "title": "CNC plasma",
        "blurb": "Plasma cutting of flat patterns and tone-field blanks before "
                 "forming (horn unwraps, steel shells).",
    },
    {
        "id": "sheet_metal_brake",
        "title": "Sheet-metal brake / forming",
        "blurb": "Bend allowance, flat-pattern unwrap, rolling and brazing of "
                 "formed metal parts (horn bells, drum shells).",
    },
    {
        "id": "hand_power_tools",
        "title": "Hand & power tools",
        "blurb": "Bench assembly, rope/string tensioning, hammer tuning and "
                 "deburring -- the empirical closure step.",
    },
]
PROCESS_ORDER = [p["id"] for p in PROCESS_AXIS]
PROCESS_RANK = {pid: i for i, pid in enumerate(PROCESS_ORDER)}

# --- Process -> live MakerBench domain ---------------------------------------
# Domain keys are the live front-page domains from site/domains_data.py.
# Starting with the live domains (#183): woodworking, instrument_acoustics,
# sheet_metal, laser_vector (+ assembly_bom when a BOM is present).
PROCESS_DOMAINS = {
    "wood_turning": ["woodworking", "instrument_acoustics"],
    "stave_joinery": ["woodworking", "instrument_acoustics"],
    "cnc_router": ["woodworking"],
    "laser_cut": ["laser_vector"],
    "cnc_plasma": ["sheet_metal"],
    "sheet_metal_brake": ["sheet_metal"],
    "hand_power_tools": [],  # bench/assembly step; no standalone live grader
}

# --- Domain -> the live/runnable task families that exercise it ---------------
# Sourced from tasks/registry.json + the acoustics/woodworking ladder docs.
DOMAIN_TASKS = {
    "instrument_acoustics": ["acoustics_resonator_volume", "acoustics_scale_length"],
    "woodworking": ["woodworking_tabbed_cabinet"],
    "sheet_metal": ["sheet_metal_bracket", "sheet_metal_bracket_precise"],
    "laser_vector": ["laser_vector_tab_slot_panel", "laser_tab_slot_panel"],
    "assembly_bom": ["enclosure_fastened"],
}
DOMAIN_ORDER = [
    "instrument_acoustics",
    "woodworking",
    "sheet_metal",
    "laser_vector",
    "assembly_bom",
]
DOMAIN_RANK = {d: i for i, d in enumerate(DOMAIN_ORDER)}

# Every instrument exercises acoustics by construction.
ALWAYS_DOMAIN = "instrument_acoustics"

INSTRUMENT_FAMILIES = ["brass", "idiophones", "percussion", "strings", "woodwind"]

# --- Classification tables ---------------------------------------------------
# Name tokens that mark a metal-formed instrument (steel/aluminum/brass shells,
# bells, pans, gongs, chimes). These steer toward plasma + brake forming.
METAL_TOKENS = {
    "sheetmetal", "steel", "metal", "aluminum", "aluminium", "brass", "bronze",
    "tin", "pan", "bell", "bells", "gong", "chime", "chimes", "carillon",
    "celesta", "glockenspiel", "tubular", "solenoid", "disc", "saw", "armonica",
    "cristal", "baschet", "conch", "timpani", "marine", "bugle", "cornet",
    "trumpet", "trombone", "serpent", "shofar", "psaltery",
}
# Slot/tongue cutting reads as laser/plasma kerf work.
SLOT_TOKENS = {"tongue", "slit", "slot", "laser"}

# Per-process keyword synonyms used only to record observed `process_signals`
# from each repo's design.md / README (evidence, not classification).
PROCESS_KEYWORDS = {
    "wood_turning": ["wood turning", "lathe", "turned", "headstock", "spindle",
                     "deep-bore", "deep bore"],
    "stave_joinery": ["stave", "mitre", "miter", "segmented", "joinery",
                      "cooper", "compound angle"],
    "cnc_router": ["cnc router", "router", "toolpath", "dogbone", "pocketing",
                   "nesting"],
    "laser_cut": ["laser", "kerf", "engrav", "score line"],
    "cnc_plasma": ["plasma", "flat pattern", "flat-pattern", "cut profile"],
    "sheet_metal_brake": ["sheet metal", "sheet-metal", "brake", "bend allowance",
                          "forming", "brazing", "soldering", "rolled", "flare"],
    "hand_power_tools": ["hand tools", "rope tension", "tuning hammer", "hammer",
                         "deburr", "angle grinder", "drill press"],
}

# Explicit overrides for repos where name+family heuristics underspecify the
# craft. Keeps the canonical issue examples exact (fujara, spiral-conch-horn).
OVERRIDES = {
    "fujara": ["stave_joinery", "wood_turning"],
    "spiral-conch-horn": ["cnc_plasma", "sheet_metal_brake"],
    "didgeridoo": ["stave_joinery", "wood_turning"],
}


def _slug_title(name: str) -> str:
    """duduk -> Duduk; spiral-conch-horn -> Spiral Conch Horn."""
    return " ".join(w.capitalize() for w in re.split(r"[-_]", name) if w)


def classify_processes(name: str, family: str) -> list[str]:
    """Return the ordered fabrication process(es) for a repo by name + family."""
    if name in OVERRIDES:
        return sorted(set(OVERRIDES[name]), key=PROCESS_RANK.get)

    n = name.lower()
    tokens = set(t for t in re.split(r"[-_]", n) if t)
    is_sheetmetal = (
        "sheetmetal" in tokens or "sheet-metal" in n or n.endswith("-sheetmetal")
    )
    is_metal = is_sheetmetal or bool(tokens & METAL_TOKENS)

    procs: set[str] = set()
    if is_sheetmetal or family == "brass":
        procs |= {"sheet_metal_brake", "cnc_plasma"}
    elif is_metal:
        procs |= {"cnc_plasma", "sheet_metal_brake"}
        if tokens & SLOT_TOKENS:
            procs.add("laser_cut")
    else:
        # Wood / acoustic-shell instruments, steered by family.
        if family == "woodwind":
            procs.add("wood_turning")
        elif family in ("strings", "percussion"):
            procs |= {"stave_joinery", "hand_power_tools"}
        elif family == "idiophones":
            procs.add("wood_turning")
        else:
            procs.add("hand_power_tools")

    # Name-level signals that augment any branch.
    if "laser" in tokens:
        procs.add("laser_cut")
    if "cnc" in tokens or "router" in tokens:
        procs.add("cnc_router")
    # Strings/percussion are always strung/headed and tensioned by hand.
    if family in ("strings", "percussion"):
        procs.add("hand_power_tools")

    if not procs:
        procs.add("hand_power_tools")
    return sorted(procs, key=PROCESS_RANK.get)


def domains_for(processes: list[str], has_bom: bool) -> list[str]:
    """Map processes -> live MakerBench domains (+ acoustics, + assembly BOM)."""
    doms: set[str] = {ALWAYS_DOMAIN}
    for p in processes:
        doms.update(PROCESS_DOMAINS.get(p, []))
    if has_bom:
        doms.add("assembly_bom")
    return sorted(doms, key=DOMAIN_RANK.get)


def tasks_for(domains: list[str]) -> list[str]:
    """The live/runnable task families exercised by the repo's domains."""
    tasks: list[str] = []
    for d in domains:
        for t in DOMAIN_TASKS.get(d, []):
            if t not in tasks:
                tasks.append(t)
    return tasks


def _read_text(path: Path, cap: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:cap].lower()
    except OSError:
        return ""


def scan_evidence(repo: Path) -> dict:
    """Filesystem + text evidence that the repo is a real, gradable design brief."""
    names = {p.name.lower(): p.name for p in repo.iterdir() if p.is_file()}

    def first(pred) -> str | None:
        for lower, original in sorted(names.items()):
            if pred(lower):
                return original
        return None

    design_table = first(
        lambda n: ("design-table" in n or "design_table" in n or "parameters" in n)
        and n.endswith((".xlsx", ".csv"))
    ) or first(lambda n: n.endswith(".xlsx"))

    design_doc = names.get("design.md")
    readme = names.get("readme.md")
    bom = first(lambda n: n == "bom.csv" or (n.startswith("bom") and n.endswith(".csv")))

    # Record which process keywords actually appear in the repo's design notes.
    text = ""
    if design_doc:
        text += _read_text(repo / design_doc)
    if readme:
        text += "\n" + _read_text(repo / readme)
    signals = sorted(
        (
            pid
            for pid in PROCESS_ORDER
            if any(kw in text for kw in PROCESS_KEYWORDS[pid])
        ),
        key=PROCESS_RANK.get,
    )

    return {
        "has_design_doc": design_doc is not None,
        "has_design_table": design_table is not None,
        "design_table": design_table,
        "has_bom": bom is not None,
        "has_capstone": "capstone-manifest.json" in names,
        "process_signals": signals,
    }


def build_repo_entry(repo: Path, family: str) -> dict:
    name = repo.name
    evidence = scan_evidence(repo)
    processes = classify_processes(name, family)
    domains = domains_for(processes, evidence["has_bom"])
    return {
        "repo_id": name,
        "title": _slug_title(name),
        "family": family,
        "path": f"instruments/{family}/{name}",
        "processes": processes,
        "domains": domains,
        "aligned_tasks": tasks_for(domains),
        "evidence": evidence,
    }


def build_manifest(root: Path) -> dict:
    repos: list[dict] = []
    for family in INSTRUMENT_FAMILIES:
        fam_dir = root / family
        if not fam_dir.is_dir():
            continue
        for repo in sorted(fam_dir.iterdir(), key=lambda p: p.name):
            if not repo.is_dir() or repo.name.startswith((".", "_")):
                continue
            repos.append(build_repo_entry(repo, family))

    repos.sort(key=lambda r: (r["family"], r["repo_id"]))

    process_counts = {pid: 0 for pid in PROCESS_ORDER}
    domain_counts = {d: 0 for d in DOMAIN_ORDER}
    family_counts: dict[str, int] = {}
    for r in repos:
        for p in r["processes"]:
            process_counts[p] += 1
        for d in r["domains"]:
            domain_counts[d] += 1
        family_counts[r["family"]] = family_counts.get(r["family"], 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "title": "Instrument-library workflow corpus",
        "description": (
            "Maps each musical-instrument design repo to its fabrication "
            "process(es), the live MakerBench grading domain(s) those processes "
            "exercise, and the runnable task families that score them. The "
            "low-stakes, effectively endless workflow-exploration corpus for the "
            "Opportunity Matrix (#120) / Workflow Track (#100). See "
            "docs/INSTRUMENT_WORKFLOW_CORPUS.md."
        ),
        "source_issue": 183,
        "corpus_root": "instruments",
        "process_axis": PROCESS_AXIS,
        "domains": DOMAIN_ORDER,
        "process_domains": PROCESS_DOMAINS,
        "domain_tasks": DOMAIN_TASKS,
        "counts": {
            "repos": len(repos),
            "by_family": {k: family_counts[k] for k in sorted(family_counts)},
            "by_process": process_counts,
            "by_domain": domain_counts,
        },
        "repos": repos,
    }


def dumps(manifest: dict) -> str:
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default="/mnt/c/Users/Tony/Documents/GitHub/instruments",
        help="Path to the instrument library (the folder holding brass/, "
             "idiophones/, percussion/, strings/, woodwind/).",
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[1] / "tasks" / "instrument_corpus.json"),
        help="Where to write the manifest JSON.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Regenerate in memory and exit non-zero if the committed manifest "
             "has drifted (no write).",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"error: corpus root not found: {root}", file=sys.stderr)
        return 2

    manifest = build_manifest(root)
    text = dumps(manifest)
    out = Path(args.out)

    if args.check:
        current = out.read_text(encoding="utf-8") if out.exists() else ""
        if current != text:
            print(f"DRIFT: {out} is out of date; run build_instrument_corpus.py",
                  file=sys.stderr)
            return 1
        print(f"ok: {out} matches the live corpus ({manifest['counts']['repos']} repos)")
        return 0

    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({manifest['counts']['repos']} repos)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
