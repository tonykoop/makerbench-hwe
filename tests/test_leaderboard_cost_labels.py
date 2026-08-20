"""The leaderboard's Cost column must name the *right* reason a cost is missing.

A cost can be absent for two different reasons and they are not interchangeable:

  * `subscription quota` — the run went through a plan that exposes no per-run
    token source at all, so spend is not separable from the plan.
  * `model unpriced` — tokens *were* captured from the runtime's own usage output,
    but the model id has no pricing entry (an alias whose underlying model is not
    pinned), so no API-equivalent figure is computed.

A track can be in both states at once, because `usage_reporting.n_subscription_opaque`
counts historical rows while `local_log_token_usage` comes from newer ones. Blaming
the quota there contradicts the Tokens column on the same row, which is
simultaneously showing an `est · local logs` value.

The functions under test live inside app.js's IIFE, so they are lifted out by
brace matching and evaluated in node. Skips where node is unavailable, matching
`tests/test_inspect_run_viewer.py`.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[1] / "site" / "assets" / "app.js"
_FUNCTIONS = (
    "usageMissingReason",
    "hasLocalLogTokens",
    "costMissingReason",
    "costMissingTitle",
)


def _lift_function(source: str, name: str) -> str:
    """Return the source of a top-level `function <name>(...) { ... }`."""
    start = source.index(f"function {name}(")
    depth = 0
    for index in range(source.index("{", start), len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def _eval(track: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    source = APP_JS.read_text(encoding="utf-8")
    script = "\n".join(
        [_lift_function(source, name) for name in _FUNCTIONS]
        + [
            f"var track = {json.dumps(track)};",
            "console.log(JSON.stringify({"
            "  reason: costMissingReason(track),"
            "  title: costMissingTitle(track)"
            "}));",
        ]
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_no_token_source_at_all_blames_the_subscription_quota():
    out = _eval({"usage_reporting": {"n_subscription_opaque": 318}})
    assert out["reason"] == "subscription quota"
    assert "no local token source" in out["title"]
    assert "never $0" in out["title"]


def test_captured_tokens_with_an_unpriced_model_does_not_blame_the_quota():
    """Tokens are present; the cost is missing because the alias has no pricing."""
    out = _eval({
        "usage_reporting": {"n_subscription_opaque": 0, "n_local_log": 12},
        "local_log_token_usage": {"mean_total_tokens": 1265},
        "mean_api_equivalent_usd": None,
    })
    assert out["reason"] == "model unpriced"
    assert "no pricing entry" in out["title"]
    assert "no local token source" not in out["title"]
    assert "never $0" in out["title"]


def test_mixed_track_reports_the_unpriced_model_and_still_counts_the_opaque_rows():
    """Historical opaque rows and new local-log rows aggregate into one track."""
    out = _eval({
        "usage_reporting": {"n_subscription_opaque": 318, "n_local_log": 12},
        "local_log_token_usage": {"mean_total_tokens": 1265},
        "mean_api_equivalent_usd": None,
    })
    assert out["reason"] == "model unpriced"
    assert "no pricing entry" in out["title"]
    # The quota rows are still disclosed, just no longer given as *the* reason.
    assert "318" in out["title"]


def test_track_with_nothing_recorded_falls_back_to_the_neutral_label():
    out = _eval({"usage_reporting": {}})
    assert out["title"] == "Mean estimated cost — not available"


def test_app_js_passes_node_syntax_check():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    result = subprocess.run([node, "--check", str(APP_JS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
