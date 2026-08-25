"""Capture-cluster ingest into site/data/landscape.json (issue #674).

The hook scans ``docs/landscape-captures/*.md`` (see that directory's README)
into a ``captures`` array so the landscape page can list the competitive-scan /
teardown / prior-art docs (#610/#611/#612) with links. It must never block:
an absent or empty directory yields an empty array.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "landscape_data", REPO_ROOT / "site" / "landscape_data.py"
)
landscape_data = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(landscape_data)


def test_missing_directory_yields_empty_list(tmp_path):
    assert landscape_data.collect_captures(tmp_path / "nope") == []


def test_readme_is_excluded(tmp_path):
    (tmp_path / "README.md").write_text("# Not a capture", encoding="utf-8")
    assert landscape_data.collect_captures(tmp_path) == []


def test_capture_fields_extracted(tmp_path):
    (tmp_path / "611-tye-teardown.md").write_text(
        "# ty\\e teardown\n\nClosest public voice-to-CAD analog;\nnotes on scope.\n",
        encoding="utf-8",
    )
    (tmp_path / "no-issue-prefix.md").write_text("plain body only\n", encoding="utf-8")

    captures = landscape_data.collect_captures(tmp_path)
    assert [c["path"].rsplit("/", 1)[-1] for c in captures] == [
        "611-tye-teardown.md", "no-issue-prefix.md",
    ]

    teardown = captures[0]
    assert teardown["issue"] == 611
    assert teardown["title"] == "ty\\e teardown"
    assert teardown["summary"] == "Closest public voice-to-CAD analog;"
    assert teardown["path"] == "docs/landscape-captures/611-tye-teardown.md"
    assert teardown["url"].startswith("https://github.com/tonykoop/makerbench-hwe/blob/main/")
    assert teardown["url"].endswith(teardown["path"])

    plain = captures[1]
    assert "issue" not in plain
    assert plain["title"] == "no-issue-prefix"  # falls back to the stem
    assert plain["summary"] == "plain body only"


def test_summary_is_collapsed_and_truncated(tmp_path):
    long_line = "word " * 100
    (tmp_path / "610-scan.md").write_text(
        "# Scan\n\n" + long_line + "\n", encoding="utf-8"
    )
    (capture,) = landscape_data.collect_captures(tmp_path)
    assert len(capture["summary"]) <= landscape_data._SUMMARY_MAX
    assert capture["summary"].endswith("…")
    assert "  " not in capture["summary"]


def test_build_payload_carries_captures(tmp_path):
    yaml_path = tmp_path / "docs" / "landscape.yaml"
    yaml_path.parent.mkdir(parents=True)
    yaml_path.write_text(
        "sweep:\n  date: 2026-06-10\nentries:\n  - name: MakerBench-HWE\n"
        "    type: benchmark\n    source: https://example.com\n",
        encoding="utf-8",
    )
    captures_dir = tmp_path / "docs" / "landscape-captures"
    captures_dir.mkdir()
    (captures_dir / "612-prior-art.md").write_text(
        "# Prior art\n\nPhoenix-bench notes.\n", encoding="utf-8"
    )

    payload = landscape_data.build(yaml_path)  # captures dir derived from yaml path
    assert payload["captures_count"] == 1
    assert payload["captures"][0]["issue"] == 612


def test_committed_json_has_captures_keys():
    data = json.loads(
        (REPO_ROOT / "site" / "data" / "landscape.json").read_text(encoding="utf-8")
    )
    assert "captures" in data
    assert data["captures_count"] == len(data["captures"])
    # Whatever is committed must match a fresh scan of the documented
    # directory (#610/#611/#612 captures have landed).
    fresh = landscape_data.collect_captures(REPO_ROOT / "docs" / "landscape-captures")
    assert data["captures"] == fresh
