"""Schema and parity checks for the landscape-sweep data artifacts.

``docs/landscape.yaml`` is the machine-readable source of truth for the
competitive-landscape sweep; ``docs/LANDSCAPE.md`` is its public rendering and
``docs/landscape-evidence/<date>.yaml`` preserves the primary-source evidence
per sweep. These tests keep the three in sync: the YAML must parse, every
entry must carry the core fields, names must be unique, sources must be URLs,
enumerated axes must use the documented vocabulary (with the free-text escape
hatches the data actually uses), every bolded project row in the public
tables must have a YAML entry, and evidence entries must point at real
landscape entries.

PyYAML is not part of the locked grading environment (requirements.lock), so
these checks skip where it is unavailable rather than adding a runtime
dependency for a docs artifact.
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip(
    "yaml", reason="PyYAML not installed (not part of the locked grading env)"
)

ROOT = Path(__file__).resolve().parents[1]
LANDSCAPE_YAML = ROOT / "docs" / "landscape.yaml"
LANDSCAPE_MD = ROOT / "docs" / "LANDSCAPE.md"
LANDSCAPE_SWEEP_MD = ROOT / "docs" / "LANDSCAPE_SWEEP.md"
EVIDENCE_DIR = ROOT / "docs" / "landscape-evidence"

# Documented in the landscape.yaml header. `type` is strictly enumerated.
ALLOWED_TYPES = {
    "benchmark",
    "leaderboard",
    "method",
    "method+benchmark",
    "dataset",
    "benchmark+method",
    "method+dataset",
    "product",
}

# Documented `grading` vocabulary. The data also uses descriptive free text
# (e.g. "executability + numeric correctness", "n/a (method; ...)"), which is
# an accepted escape hatch — but a value that *looks* like a vocabulary token
# (single hyphenated word) must actually be in the vocabulary, so typos like
# "determinstic-geometric" fail.
DOCUMENTED_GRADING = {
    "deterministic-geometric",
    "executability",
    "simulation-fea",
    "vlm-judge",
    "llm-judge",
    "human-preference",
    "task-completion",
    "mixed",
}
_VOCAB_TOKEN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Fields every entry carries today (methods omit fab_processes / agentic /
# integrity / dataset_scale, so those are intentionally not required).
REQUIRED_FIELDS = {
    "name",
    "type",
    "source",
    "date",
    "recent",
    "framing_own",
    "input_modality",
    "output_representation",
    "grading",
    "scope",
    "openness",
    "backing",
}

REQUIRED_SWEEP_CHECKS = {
    "reverify_sources",
    "hunt_new_entries",
    "flag_recent_entries",
    "refresh_promised_releases",
    "refresh_cadgenbench_leaderboard",
    "refresh_muse_grading",
    "diff_landscape_yaml",
    "append_what_changed",
    "refresh_strategy_rankings",
    "verify_benchmark_vs_method_labels",
}

REQUIRED_VOLATILE_WATCHLIST = {
    "UniCAD",
    "Physics-in-the-Loop",
    "Hephaestus-CCX",
    "GenCAD-3D",
    "CADGenBench",
    "MUSE",
}

# LANDSCAPE.md table rows whose display name differs from the YAML `name`.
MD_TO_YAML_NAME = {
    "GD&T mapping": "GD&T drawing-annotation mapping",
}


def _load_landscape() -> dict:
    data = yaml.safe_load(LANDSCAPE_YAML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "landscape.yaml must parse to a mapping"
    return data


def _entries() -> list[dict]:
    entries = _load_landscape()["entries"]
    assert isinstance(entries, list) and entries, "entries must be a non-empty list"
    return entries


def test_top_level_structure():
    data = _load_landscape()

    assert "sweep" in data, "top-level `sweep` block is required"
    assert "entries" in data, "top-level `entries` list is required"
    sweep_date = data["sweep"].get("date")
    assert isinstance(
        sweep_date, _dt.date
    ), "sweep.date must be a YYYY-MM-DD date (YAML date scalar)"


def test_quarterly_sweep_process_metadata():
    data = _load_landscape()
    sweep = data["sweep"]
    sweep_date = sweep["date"]
    next_due = sweep.get("next_due")

    assert isinstance(
        next_due, _dt.date
    ), "sweep.next_due must be a YYYY-MM-DD date (YAML date scalar)"
    assert next_due > sweep_date, "sweep.next_due must be after sweep.date"
    assert 80 <= (next_due - sweep_date).days <= 110, (
        "sweep.next_due should remain roughly quarterly after sweep.date"
    )
    assert sweep.get("cadence") == "quarterly"
    assert sweep.get("process_doc") == "docs/LANDSCAPE_SWEEP.md"
    assert sweep.get("evidence_template") == (
        "docs/landscape-evidence/<YYYY-MM-DD>.yaml"
    )
    assert REQUIRED_SWEEP_CHECKS <= set(sweep.get("required_checks", []))
    assert REQUIRED_VOLATILE_WATCHLIST <= set(sweep.get("volatile_watchlist", []))


def test_quarterly_watchlist_names_are_landscape_entries():
    data = _load_landscape()
    entry_names = {entry["name"] for entry in data["entries"]}
    watchlist = set(data["sweep"].get("volatile_watchlist", []))

    assert watchlist
    assert watchlist <= entry_names, (
        f"sweep.volatile_watchlist contains names with no entry: "
        f"{sorted(watchlist - entry_names)}"
    )


def test_landscape_markdown_links_quarterly_runbook():
    md_text = LANDSCAPE_MD.read_text(encoding="utf-8")
    runbook_text = LANDSCAPE_SWEEP_MD.read_text(encoding="utf-8")

    assert "[`LANDSCAPE_SWEEP.md`](LANDSCAPE_SWEEP.md)" in md_text
    assert "Next full sweep due: 2026-09-10" in runbook_text
    assert "python -m pytest tests/test_landscape_yaml.py" in runbook_text


def test_every_entry_has_required_fields():
    for entry in _entries():
        missing = REQUIRED_FIELDS - entry.keys()
        assert not missing, f"{entry.get('name', '<unnamed>')}: missing {sorted(missing)}"
        for field in REQUIRED_FIELDS:
            value = entry[field]
            assert value not in (None, "", []), (
                f"{entry['name']}: required field `{field}` is empty"
            )


def test_entry_names_are_unique():
    names = [entry["name"] for entry in _entries()]

    duplicates = {n for n in names if names.count(n) > 1}
    assert not duplicates, f"duplicate entry names: {sorted(duplicates)}"


def test_sources_are_urls():
    for entry in _entries():
        for field in ("source", "source_alt"):
            if field in entry:
                assert str(entry[field]).startswith(("http://", "https://")), (
                    f"{entry['name']}: `{field}` must be an http(s) URL, "
                    f"got {entry[field]!r}"
                )


def test_type_uses_documented_vocabulary():
    for entry in _entries():
        assert entry["type"] in ALLOWED_TYPES, (
            f"{entry['name']}: type {entry['type']!r} not in documented "
            f"vocabulary {sorted(ALLOWED_TYPES)} (extend the header vocab "
            "and this test together if a new type is genuinely needed)"
        )


def test_grading_vocabulary_tokens_are_spelled_correctly():
    for entry in _entries():
        grading = str(entry["grading"]).strip()
        assert grading, f"{entry['name']}: grading must be non-empty"
        # Free text (spaces, parentheses, "n/a (...)" etc.) is allowed; bare
        # vocabulary-shaped tokens must match the documented vocabulary.
        if _VOCAB_TOKEN.fullmatch(grading):
            assert grading in DOCUMENTED_GRADING, (
                f"{entry['name']}: grading {grading!r} looks like a "
                f"vocabulary token but is not in {sorted(DOCUMENTED_GRADING)}"
            )


def test_recent_is_boolean():
    for entry in _entries():
        assert isinstance(entry["recent"], bool), (
            f"{entry['name']}: `recent` must be a YAML boolean"
        )


def test_every_landscape_md_table_row_has_a_yaml_entry():
    yaml_names = {entry["name"] for entry in _entries()}
    md_text = LANDSCAPE_MD.read_text(encoding="utf-8")
    # Project rows start with a bolded name: `| **Name** (...`.
    md_names = re.findall(r"^\|\s*\*\*([^*]+?)\*\*", md_text, flags=re.MULTILINE)
    assert md_names, "no bolded project rows found in LANDSCAPE.md — table format changed?"

    unmatched = []
    for md_name in md_names:
        mapped = MD_TO_YAML_NAME.get(md_name, md_name)
        if mapped in yaml_names:
            continue
        # Allow the YAML name to be a longer form of the display name,
        # e.g. "EngDesign" -> "EngDesign (Toward Engineering AGI)".
        if any(y.startswith(mapped) for y in yaml_names):
            continue
        unmatched.append(md_name)
    assert not unmatched, (
        f"LANDSCAPE.md rows without a landscape.yaml entry: {unmatched} "
        "(add the entry, or extend MD_TO_YAML_NAME if only the display "
        "name differs)"
    )


def test_evidence_sidecars_parse_and_reference_real_entries():
    evidence_files = sorted(EVIDENCE_DIR.glob("*.yaml"))
    assert evidence_files, "expected at least one docs/landscape-evidence/<date>.yaml"

    yaml_names = {entry["name"] for entry in _entries()}
    landscape_fields = set().union(*(entry.keys() for entry in _entries()))

    for path in evidence_files:
        # File name doubles as the sweep date.
        _dt.date.fromisoformat(path.stem)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(data.get("sweep_date"), _dt.date), f"{path.name}: sweep_date"
        for item in data["entries"]:
            name = item["name"]
            assert name in yaml_names, (
                f"{path.name}: evidence entry {name!r} has no landscape.yaml entry"
            )
            assert item["sources"], f"{path.name}: {name}: sources required"
            for url in item["sources"]:
                assert str(url).startswith(("http://", "https://")), (
                    f"{path.name}: {name}: source {url!r} is not an http(s) URL"
                )
            assert isinstance(item.get("fetched_on"), _dt.date), (
                f"{path.name}: {name}: fetched_on must be a date"
            )
            unknown = set(item.get("supports", [])) - landscape_fields
            assert not unknown, (
                f"{path.name}: {name}: `supports` names unknown landscape.yaml "
                f"fields {sorted(unknown)}"
            )


def _entry_by_name(name: str) -> dict:
    for entry in _entries():
        if entry["name"] == name:
            return entry
    raise AssertionError(f"no landscape.yaml entry named {name!r}")


def test_marb_entry_is_assembly_integrity_neighbour():
    """MARB/CADCLAW (issue #77) is the macro-assembly neighbour: it must be
    typed as an assembly-integrity, deterministic, MIT-licensed entry so it is
    not silently miscategorised as a process-DFM competitor."""
    marb = _entry_by_name("MARB / CADCLAW")

    assert marb["scope"] == "assembly-integrity", (
        "MARB grades macro-assembly/system integrity, not micro process-DFM; "
        "scope must be assembly-integrity to keep the boundary explicit"
    )
    assert marb["type"] == "benchmark+method"
    assert marb["grading"] == "deterministic-geometric"
    assert marb["recent"] is True
    # CADCLAW is the open-source engine; the openness note must record MIT.
    assert "MIT" in str(marb["openness"])
    # Both primary sources (landing page + engine repo) must be captured.
    assert str(marb["source"]).startswith("https://")
    assert str(marb["source_alt"]).startswith("https://")


def test_marb_scope_token_is_documented_in_header():
    """The `assembly-integrity` scope token MARB introduces must be listed in
    the landscape.yaml header vocabulary, so the data and its documented
    vocabulary stay in sync."""
    header = LANDSCAPE_YAML.read_text(encoding="utf-8").split("entries:", 1)[0]
    assert "assembly-integrity" in header, (
        "scope token `assembly-integrity` is used but not documented in the "
        "landscape.yaml header `scope:` vocabulary"
    )


def test_marb_has_dated_primary_source_evidence():
    """Acceptance criterion for #77: the MARB entry ships with a dated evidence
    sidecar citing the fetched primary sources."""
    evidence_files = sorted(EVIDENCE_DIR.glob("*.yaml"))
    marb_evidence = []
    for path in evidence_files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for item in data["entries"]:
            if item["name"] == "MARB / CADCLAW":
                marb_evidence.append((path, item))

    assert marb_evidence, "no evidence sidecar entry for 'MARB / CADCLAW'"
    _, item = marb_evidence[0]
    hosts = " ".join(item["sources"])
    assert "marb.cadclaw.io" in hosts, "evidence must cite the MARB landing page"
    assert "CADCLAW" in hosts, "evidence must cite the CADCLAW engine repo"
