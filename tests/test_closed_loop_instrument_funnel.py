"""Closed-loop instrument demo Phase 3b: DFM + cost funnel gate (#83, #243).

The pilot lyre/kora bridge must pass BOTH MakerBench gates:
  * the deterministic acoustic scale_length gate (existing), and
  * the deflationary funnel (DFM + local cost + quote stub) from epic #240.

Everything stays deterministic and offline; the funnel never authorizes a PO.
"""

from __future__ import annotations

import json
from pathlib import Path

from makerbench_core import score_file

from examples.closed_loop_instrument_funnel_demo import (
    GEOMETRY_REL,
    build_combined_result,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS_JSON = ROOT / "examples" / "closed_loop_instrument_funnel_demo.results.json"
STEP = ROOT / "examples" / "closed_loop_instrument_bridge.step"
DOC = ROOT / "docs" / "CLOSED_LOOP_INSTRUMENT_DEMO.md"


def test_pilot_step_passes_makerbench_core_dfm():
    result = score_file(STEP)
    assert result.passed is True
    assert result.makerbench_dfm_score == 100.0
    assert result.input["format"] == "step"


def test_combined_result_passes_both_gates_and_blocks_purchase_orders():
    result = build_combined_result()

    # DFM gate
    assert result["dfm"]["passed"] is True
    assert result["dfm"]["score"] == 100.0

    # Manufacturing funnel reached the human-approval stage, no PO authorized.
    mfg = result["manufacturing"]
    assert mfg["gates_passed"] == ["dfm", "local_cost", "quote", "approval_request"]
    assert mfg["blocked_at"] is None
    assert mfg["local_cost_usd"] is not None and mfg["local_cost_usd"] > 0
    assert mfg["quote_status"] == "quoted"
    assert mfg["purchase_order_allowed"] is False

    # Acoustic gate
    assert result["acoustics"]["feasible"] == 1.0

    assert result["both_gates_passed"] is True
    assert result["geometry"] == GEOMETRY_REL


def test_demo_is_deterministic():
    assert build_combined_result() == build_combined_result()


def test_captured_results_json_matches_live_recomputation():
    captured = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    assert captured == build_combined_result()


def test_doc_documents_the_funnel_phase():
    doc = DOC.read_text(encoding="utf-8")
    assert "examples/closed_loop_instrument_funnel_demo.py" in doc
    assert "examples/closed_loop_instrument_funnel_demo.results.json" in doc
    assert "run_funnel" in doc
