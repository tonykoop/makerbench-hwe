"""Domain-breadth gallery contract tests (issue #169, epic #176).

Tests for ``build_domain_gallery`` and ``write_domains`` in
``site/domains_data.py``.  The gallery is derived entirely from
``tasks/registry.json`` so the landing page can never drift from the benchmark.

Import pattern mirrors ``tests/test_inspect_payload.py`` / ``test_site_build_data.py``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "makerbench_domains_data",
    ROOT / "site" / "domains_data.py",
)
domains_data = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(domains_data)

build_domain_gallery = domains_data.build_domain_gallery
write_domains = domains_data.write_domains

REGISTRY = ROOT / "tasks" / "registry.json"
MESHES = ROOT / "site" / "data" / "meshes.json"


# ---------------------------------------------------------------------------
# 1. Builds from the real registry without error; top-level keys present
# ---------------------------------------------------------------------------

def test_builds_from_real_registry_without_error():
    payload = build_domain_gallery(REGISTRY)
    required = {
        "_generated",
        "benchmark_profile",
        "benchmark_version",
        "tiers",
        "domains",
        "horizon",
        "longer_horizon",
    }
    missing = required - set(payload.keys())
    assert not missing, f"Missing top-level keys: {missing}"


def test_returns_dict_type():
    payload = build_domain_gallery(REGISTRY)
    assert isinstance(payload, dict)


# ---------------------------------------------------------------------------
# 2. _generated string mentions registry as single source; no "oracle"
# ---------------------------------------------------------------------------

def test_generated_mentions_registry_as_single_source():
    payload = build_domain_gallery(REGISTRY)
    gen = payload["_generated"]
    assert "registry.json" in gen or "registry" in gen.lower()
    # Should describe provenance in terms of the single source
    assert "single source" in gen.lower() or "no-drift" in gen.lower() or "drift" in gen.lower() or "domains_data.py" in gen


def test_generated_does_not_mention_oracle():
    payload = build_domain_gallery(REGISTRY)
    assert "oracle" not in payload["_generated"].lower()


def test_generated_is_nonempty_string():
    payload = build_domain_gallery(REGISTRY)
    assert isinstance(payload["_generated"], str)
    assert len(payload["_generated"]) > 10


# ---------------------------------------------------------------------------
# 3. Each domain has required keys; counts.total == sum of status buckets
# ---------------------------------------------------------------------------

_DOMAIN_KEYS = {
    "key", "title", "icon", "blurb", "tier", "live", "status",
    "thumbnail", "capability_axes", "counts", "entries",
}

_COUNT_KEYS = {"scored", "runnable", "alpha", "planned", "total"}


def test_each_domain_has_required_keys():
    payload = build_domain_gallery(REGISTRY)
    for domain in payload["domains"]:
        missing = _DOMAIN_KEYS - set(domain.keys())
        assert not missing, f"Domain {domain.get('key')!r} missing keys: {missing}"


def test_each_domain_counts_has_required_keys():
    payload = build_domain_gallery(REGISTRY)
    for domain in payload["domains"]:
        missing = _COUNT_KEYS - set(domain["counts"].keys())
        assert not missing, f"Domain {domain.get('key')!r} counts missing: {missing}"


def test_each_domain_counts_total_equals_sum_of_status_buckets():
    payload = build_domain_gallery(REGISTRY)
    for domain in payload["domains"]:
        counts = domain["counts"]
        expected_total = counts["scored"] + counts["runnable"] + counts["alpha"] + counts["planned"]
        assert counts["total"] == expected_total, (
            f"Domain {domain['key']!r}: counts.total={counts['total']} "
            f"but scored+runnable+alpha+planned={expected_total}"
        )


def test_domains_is_nonempty_list():
    payload = build_domain_gallery(REGISTRY)
    assert isinstance(payload["domains"], list)
    assert len(payload["domains"]) > 0


# ---------------------------------------------------------------------------
# 4. Every entry has required keys; entry.status ∈ valid set
# ---------------------------------------------------------------------------

_ENTRY_KEYS = {
    "id", "title", "summary", "status", "doc", "site_href", "task_dir",
    "thumbnail", "issue", "tracks", "input_modalities", "capability_axes",
    "grader_primitives", "maturity_rank",
}

_VALID_STATUSES = {"scored", "runnable", "alpha", "planned"}


def test_every_entry_has_required_keys():
    payload = build_domain_gallery(REGISTRY)
    for domain in payload["domains"]:
        for entry in domain["entries"]:
            missing = _ENTRY_KEYS - set(entry.keys())
            assert not missing, (
                f"Entry {entry.get('id')!r} in domain {domain['key']!r} "
                f"missing keys: {missing}"
            )


def test_every_entry_status_is_valid():
    payload = build_domain_gallery(REGISTRY)
    for domain in payload["domains"]:
        for entry in domain["entries"]:
            assert entry["status"] in _VALID_STATUSES, (
                f"Entry {entry.get('id')!r} has invalid status {entry['status']!r}"
            )


def test_scored_entries_have_site_href():
    """Scored entries must carry a site_href pointing to their detail page."""
    payload = build_domain_gallery(REGISTRY)
    for domain in payload["domains"]:
        for entry in domain["entries"]:
            if entry["status"] == "scored":
                assert entry["site_href"] is not None, (
                    f"Scored entry {entry['id']!r} has no site_href"
                )
                assert entry["site_href"].startswith("tasks/")


def test_non_scored_entries_have_no_site_href():
    """Non-scored entries have no published detail page yet."""
    payload = build_domain_gallery(REGISTRY)
    for domain in payload["domains"]:
        for entry in domain["entries"]:
            if entry["status"] != "scored":
                assert entry["site_href"] is None, (
                    f"Non-scored entry {entry['id']!r} unexpectedly has site_href={entry['site_href']!r}"
                )


# ---------------------------------------------------------------------------
# 5. Determinism: building twice yields byte-identical json.dumps output
# ---------------------------------------------------------------------------

def test_determinism_byte_identical_on_two_calls():
    payload_a = build_domain_gallery(REGISTRY)
    payload_b = build_domain_gallery(REGISTRY)
    dump_a = json.dumps(payload_a, sort_keys=True)
    dump_b = json.dumps(payload_b, sort_keys=True)
    assert dump_a == dump_b


def test_determinism_with_meshes_byte_identical():
    if not MESHES.exists():
        return  # skip if meshes not present
    payload_a = build_domain_gallery(REGISTRY, MESHES)
    payload_b = build_domain_gallery(REGISTRY, MESHES)
    assert json.dumps(payload_a, sort_keys=True) == json.dumps(payload_b, sort_keys=True)


# ---------------------------------------------------------------------------
# 6. Public-data guard: missing meshes_path → no crash; no "oracle" in dump
# ---------------------------------------------------------------------------

def test_missing_meshes_path_does_not_crash(tmp_path):
    """A meshes_path that does not exist must not raise; thumbnails become null."""
    nonexistent = tmp_path / "does_not_exist" / "meshes.json"
    payload = build_domain_gallery(REGISTRY, nonexistent)
    # Must still build a valid payload
    assert isinstance(payload, dict)
    assert "domains" in payload
    # All thumbnails should be null when mesh index is empty
    for domain in payload["domains"]:
        assert domain["thumbnail"] is None
        for entry in domain["entries"]:
            assert entry["thumbnail"] is None


def test_payload_json_contains_no_oracle_path(tmp_path):
    """The full JSON dump must never contain an oracle file/directory path.

    Task summaries may legitimately contain the word 'oracle' in prose (e.g.
    "graded by oracle"), but path strings like '/oracle/', 'oracles/', or
    'private/oracles' are boundary violations.
    """
    nonexistent = tmp_path / "no_meshes.json"
    payload = build_domain_gallery(REGISTRY, nonexistent)
    dump = json.dumps(payload)
    # Detect path-like oracle references, not prose uses of the word
    import re as _re
    oracle_paths = _re.findall(r"""['"\/][^\s'"]*oracle[^\s'"]*['"\/]""", dump, _re.IGNORECASE)
    assert not oracle_paths, (
        f"JSON dump contains oracle path references: {oracle_paths[:3]}"
    )


def test_payload_json_contains_no_private_oracles_path(tmp_path):
    """The full JSON dump must never contain 'private/oracles'."""
    payload = build_domain_gallery(REGISTRY)
    dump = json.dumps(payload)
    assert "private/oracles" not in dump, (
        "JSON dump contains 'private/oracles' path — oracle boundary violated"
    )


# ---------------------------------------------------------------------------
# 7. Thumbnail wiring: real meshes.json populates at least one entry thumbnail
# ---------------------------------------------------------------------------

def test_thumbnail_wiring_with_real_meshes():
    """When building with the real meshes.json, at least one entry should get a
    non-null thumbnail pointing under assets/meshes/. If no task_id in the mesh
    manifest matches any domain entry, we just assert the field is present and null."""
    if not MESHES.exists():
        # meshes.json absent: verify thumbnail key is present and null
        payload = build_domain_gallery(REGISTRY)
        for domain in payload["domains"]:
            for entry in domain["entries"]:
                assert "thumbnail" in entry
        return

    payload = build_domain_gallery(REGISTRY, MESHES)
    thumbnails_found = []
    for domain in payload["domains"]:
        for entry in domain["entries"]:
            assert "thumbnail" in entry, f"Entry {entry.get('id')!r} missing 'thumbnail' key"
            if entry["thumbnail"] is not None:
                thumbnails_found.append(entry["thumbnail"])

    # If the meshes.json is non-empty, at least one entry should have a thumbnail
    meshes_data = json.loads(MESHES.read_text(encoding="utf-8"))
    if meshes_data.get("meshes"):
        assert thumbnails_found, (
            "meshes.json has entries but no domain entry received a thumbnail"
        )
        for thumb in thumbnails_found:
            assert "assets/meshes/" in thumb, (
                f"Thumbnail {thumb!r} does not point under assets/meshes/"
            )


def test_thumbnail_field_always_present():
    """The 'thumbnail' field must always be present on entries, even when null."""
    payload = build_domain_gallery(REGISTRY)
    for domain in payload["domains"]:
        for entry in domain["entries"]:
            assert "thumbnail" in entry


# ---------------------------------------------------------------------------
# 8. Horizon items each have required keys including issue (int or null)
# ---------------------------------------------------------------------------

_HORIZON_KEYS = {"key", "title", "blurb", "tier", "issue"}


def test_horizon_items_have_required_keys():
    payload = build_domain_gallery(REGISTRY)
    horizon = payload["horizon"]
    assert isinstance(horizon, list)
    assert len(horizon) > 0
    for item in horizon:
        missing = _HORIZON_KEYS - set(item.keys())
        assert not missing, f"Horizon item {item.get('key')!r} missing keys: {missing}"


def test_horizon_issue_is_int_or_null():
    """The issue field on each horizon item must be an int or None."""
    payload = build_domain_gallery(REGISTRY)
    for item in payload["horizon"]:
        issue = item["issue"]
        assert issue is None or isinstance(issue, int), (
            f"Horizon item {item.get('key')!r} has invalid issue={issue!r} "
            f"(expected int or None)"
        )


def test_horizon_title_and_blurb_are_nonempty_strings():
    payload = build_domain_gallery(REGISTRY)
    for item in payload["horizon"]:
        assert isinstance(item["title"], str) and item["title"], (
            f"Horizon item {item.get('key')!r} has empty title"
        )
        assert isinstance(item["blurb"], str) and item["blurb"], (
            f"Horizon item {item.get('key')!r} has empty blurb"
        )


def test_horizon_tier_is_string():
    """Tier should be one of the known tier keys."""
    payload = build_domain_gallery(REGISTRY)
    valid_tiers = {"alpha", "beta", "v1"}
    for item in payload["horizon"]:
        assert item["tier"] in valid_tiers, (
            f"Horizon item {item.get('key')!r} has unknown tier {item['tier']!r}"
        )


# ---------------------------------------------------------------------------
# 9. write_domains: writes a readable, deterministic JSON file
# ---------------------------------------------------------------------------

def test_write_domains_creates_file(tmp_path):
    payload = build_domain_gallery(REGISTRY)
    out = tmp_path / "domains.json"
    write_domains(out, payload)
    assert out.exists()
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["benchmark_version"] == payload["benchmark_version"]


def test_write_domains_is_deterministic(tmp_path):
    payload = build_domain_gallery(REGISTRY)
    out_a = tmp_path / "a" / "domains.json"
    out_b = tmp_path / "b" / "domains.json"
    write_domains(out_a, payload)
    write_domains(out_b, payload)
    assert out_a.read_text(encoding="utf-8") == out_b.read_text(encoding="utf-8")


def test_write_domains_ends_with_newline(tmp_path):
    payload = build_domain_gallery(REGISTRY)
    out = tmp_path / "domains.json"
    write_domains(out, payload)
    assert out.read_text(encoding="utf-8").endswith("\n")


def test_write_domains_creates_parent_dirs(tmp_path):
    payload = build_domain_gallery(REGISTRY)
    out = tmp_path / "nested" / "deeply" / "domains.json"
    write_domains(out, payload)  # must not raise
    assert out.exists()
