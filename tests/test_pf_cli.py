"""Tests for prototyping_funnel.cli — makerbench-protofunnel command.

Epic #240 — deflationary prototyping funnel.
"""

from __future__ import annotations

import json

import pytest

from prototyping_funnel.cli import main


MINIMAL_STEP = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('cli test STEP'), '2;1');
FILE_NAME('part.step','2026-06-19T00:00:00',(),(),'makerbench','','');
FILE_SCHEMA(('AP214'));
ENDSEC;
DATA;
#1 = PRODUCT('bracket','bracket','', (#2));
#2 = PRODUCT_CONTEXT('',#3,'');
#3 = APPLICATION_CONTEXT('mechanical design');
ENDSEC;
END-ISO-10303-21;
"""


def _step(tmp_path):
    p = tmp_path / "part.step"
    p.write_text(MINIMAL_STEP, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


def test_cli_exits_zero_for_valid_step(tmp_path):
    code = main([str(_step(tmp_path))])
    assert code == 0


def test_cli_exits_nonzero_for_missing_file(tmp_path):
    code = main([str(tmp_path / "missing.step")])
    assert code != 0


def test_cli_exits_nonzero_for_garbage_step(tmp_path):
    bad = tmp_path / "garbage.step"
    bad.write_text("not a step file", encoding="utf-8")
    code = main([str(bad)])
    assert code != 0


def test_cli_json_output_is_valid(tmp_path, capsys):
    code = main([str(_step(tmp_path)), "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert "dfm_score" in payload
    assert "gates_passed" in payload
    assert code == 0


def test_cli_json_includes_bridge(tmp_path, capsys):
    code = main([
        str(_step(tmp_path)),
        "--json",
        "--material-volume-mm3", "12000",
        "--removed-volume-mm3", "3000",
        "--hole-count", "6",
    ])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["bridge"] is not None
    assert payload["bridge"]["process_count"] > 0


def test_cli_json_includes_roi_when_sale_price_provided(tmp_path, capsys):
    code = main([
        str(_step(tmp_path)),
        "--json",
        "--sale-price", "149.0",
        "--quantity", "100",
        "--material-volume-mm3", "12000",
        "--removed-volume-mm3", "3000",
    ])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["roi"] is not None


def test_cli_human_output_not_empty(tmp_path, capsys):
    main([str(_step(tmp_path))])
    captured = capsys.readouterr()
    assert "dfm_score" in captured.out
    assert "sha256" in captured.out


def test_cli_fail_under_roi_causes_nonzero_when_below_threshold(tmp_path):
    # target_volume=1 with any non-trivial dev cost → roi < 0
    code = main([
        str(_step(tmp_path)),
        "--sale-price", "50.0",
        "--quantity", "1",
        "--concept-cost", "10000.0",
        "--fail-under-roi", "1.0",   # require 100% ROI — impossible at qty=1
    ])
    assert code != 0


def test_cli_profile_restriction(tmp_path, capsys):
    code = main([
        str(_step(tmp_path)),
        "--json",
        "--material-volume-mm3", "8000",
        "--removed-volume-mm3", "1000",
        "--profile-ids", "cnc-aluminum-3axis-v1",
    ])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    # Only one profile evaluated
    assert payload["bridge"] is not None
    assert payload["bridge"]["process_count"] <= 1
    assert code == 0
