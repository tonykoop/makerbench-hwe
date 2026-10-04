"""#983 review: the arena renderer (site/assets/app.js) fails closed on its own.

A non-legacy min_wall estimator renders numbers only when the page knows it AND it carries a
label; anything else shows a withheld message. Legacy payloads render exactly as before. The
functions live inside app.js's IIFE, so they are lifted out by brace matching and evaluated in
node (skipped where node is unavailable, like tests/test_leaderboard_cost_labels.py).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[1] / "site" / "assets" / "app.js"
_FUNCTIONS = ("escapeHTML", "arenaNum", "arenaScoreTable", "arenaEstimator", "arenaWithheldHTML",
              "arenaObjectiveTable", "arenaAgreementHTML", "arenaOneHeadlineHTML", "arenaHeadlineHTML")
_VARS = ("ARENA_KNOWN_ESTIMATORS", "ARENA_ESTIMATOR_WITHHELD")


def _lift_function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    depth = 0
    for index in range(source.index("{", start), len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def _lift_var(source: str, name: str) -> str:
    match = re.search(rf"var {name} =.*?;\n", source, re.S)
    assert match, name
    return match.group(0)


def _render(calls: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    source = APP_JS.read_text(encoding="utf-8")
    script = "\n".join(
        [_lift_var(source, name) for name in _VARS]
        + [_lift_function(source, name) for name in _FUNCTIONS]
        + [f"var calls = {json.dumps(calls)};",
           "var out = {};",
           "Object.keys(calls).forEach(function (k) {",
           "  var c = calls[k];",
           "  out[k] = c.fn === 'agreement' ? arenaAgreementHTML(c.arg, c.arg.rho == null ? 'n/a' : arenaNum(c.arg.rho, 3), '')",
           "    : c.fn === 'headline' ? arenaHeadlineHTML(c.arg) : arenaObjectiveTable(c.arg);",
           "});",
           "console.log(JSON.stringify(out));"])
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


ROWS = [{"entrant": "m", "objective_pass_rate": 0.75, "n_objective_trials": 4}]
LABEL = "min_wall estimator robust-v1 (1st-percentile wall thickness, the default since T2)"


def test_unlabelled_or_unknown_estimators_never_render_numbers():
    out = _render({
        "agreement_no_label": {"fn": "agreement", "arg": {"min_wall_method": "robust-v1", "rho": -1}},
        "agreement_unknown": {"fn": "agreement", "arg": {"min_wall_method": "future-v9", "label": "x", "rho": -1}},
        "headline_no_label": {"fn": "headline", "arg": {
            "headline": {"value": 1.0, "rounds_used": [5]},
            "estimator_headlines": [{"value": -1.0, "rounds_used": [6], "min_wall_method": "robust-v1"}]}},
        "table_unknown": {"fn": "table", "arg": {"scoreline": [], "estimator_scorelines": [
            {"min_wall_method": "future-v9", "label": "future", "rows": ROWS}]}},
        "table_no_label": {"fn": "table", "arg": {"scoreline": [], "estimator_scorelines": [
            {"min_wall_method": "robust-v1", "rows": ROWS}]}},
    })
    for key in ("agreement_no_label", "agreement_unknown"):
        assert "-1.000" not in out[key] and "Withheld" in out[key], out[key]
    assert "−1" not in out["headline_no_label"] and "-1.00" not in out["headline_no_label"]
    assert "Headline withheld" in out["headline_no_label"]
    assert "+1.00" in out["headline_no_label"]  # the legacy headline still renders
    for key in ("table_unknown", "table_no_label"):
        assert "0.75" not in out[key] and "Withheld" in out[key], out[key]


def test_labelled_robust_and_legacy_payloads_render_their_numbers():
    out = _render({
        "agreement_legacy": {"fn": "agreement", "arg": {"rho": 0.5, "n": 3}},
        "agreement_robust": {"fn": "agreement", "arg": {"min_wall_method": "robust-v1", "label": LABEL, "rho": -1}},
        "headline": {"fn": "headline", "arg": {
            "headline": {"value": 1.0, "rounds_used": [5], "min_wall_method": "min", "label": "legacy"},
            "estimator_headlines": [{"value": -1.0, "rounds_used": [6], "min_wall_method": "robust-v1",
                                     "label": LABEL}]}},
        "table": {"fn": "table", "arg": {"scoreline": ROWS, "estimator_scorelines": [
            {"min_wall_method": "robust-v1", "label": LABEL, "rows": ROWS}]}},
        "table_legacy": {"fn": "table", "arg": {"scoreline": ROWS}},
    })
    assert "<strong>0.500</strong>" in out["agreement_legacy"] and "robust-v1" not in out["agreement_legacy"]
    assert "-1.000" in out["agreement_robust"] and "robust-v1" in out["agreement_robust"]
    assert "+1.00" in out["headline"] and "-1.00" in out["headline"] and "robust-v1" in out["headline"]
    assert out["table"].count("0.75") == 2 and "robust-v1" in out["table"]
    assert out["table_legacy"].count("0.75") == 1 and "Withheld" not in out["table_legacy"]
