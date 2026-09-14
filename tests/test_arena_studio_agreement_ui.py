"""Wiring tests for the Agreement Studio UI extras (#699 D2).

No headless browser here (out of scope for this repo's test setup) -- these
confirm the static files are actually served, the page references them, and
the backend endpoints they call return shapes the JS expects. The JS logic
itself (CI bars, family filter, outlier badges) is straightforward DOM
rendering over already-tested backend payloads (see test_arena_studio_analytics.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from makerbench.arena_studio import create_studio_app


@pytest.fixture
def fake_registry(tmp_path: Path) -> Path:
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(
        json.dumps({"instruments": [{"id": "ocarina", "family": "woodwind"}]}), encoding="utf-8"
    )
    return reg_path


@pytest.fixture
def fake_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run_log.json").write_text(
        json.dumps(
            {
                "config": {"model_ids": ["model-a", "model-b"]},
                "trials": [
                    {
                        "instrument_id": "ocarina",
                        "model_id": "model-a",
                        "status": "completed",
                        "result": {"objective": {"objective_pass_rate": 1.0}},
                    },
                    {
                        "instrument_id": "ocarina",
                        "model_id": "model-b",
                        "status": "completed",
                        "result": {"objective": {"objective_pass_rate": 0.0}},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "votes.revealed.jsonl").write_text(
        json.dumps(
            {
                "winner": "left",
                "instrument_id": "ocarina",
                "reveal": {"left": {"model_id": "model-a"}, "right": {"model_id": "model-b"}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return run_dir


@pytest.fixture
def client(fake_run: Path, fake_registry: Path, tmp_path: Path) -> TestClient:
    app = create_studio_app(default_run_dir=fake_run, registry_path=fake_registry, repo_root=tmp_path)
    return TestClient(app)


def test_agreement_static_files_are_served(client: TestClient):
    css = client.get("/static/agreement.css")
    js = client.get("/static/agreement.js")
    assert css.status_code == 200
    assert js.status_code == 200
    assert "ci-bar" in css.text
    assert "loadAgreementStudioExtras" in js.text


def test_index_html_references_agreement_extras(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert '/static/agreement.css' in html
    assert '/static/agreement.js' in html
    assert 'id="agreementStudioExtras"' in html
    assert 'id="familyFilterSelect"' in html
    assert 'id="ciBars"' in html
    assert 'id="outlierCallouts"' in html


def test_studio_js_dispatches_to_agreement_extras_on_leaderboard_tab(client: TestClient):
    js = client.get("/static/studio.js").text
    assert "loadAgreementStudioExtras()" in js


def test_agreement_js_checks_response_ok_before_treating_body_as_data(client: TestClient):
    # A 404 with a JSON error body (e.g. {"detail": "Not Found"}) parses
    # successfully, so a fetch() without an `ok` check would silently treat
    # an error response as real data (#727 R2 finding).
    js = client.get("/static/agreement.js").text
    assert "res.ok" in js or ".ok)" in js


def test_agreement_js_builds_family_options_via_dom_not_string_html(client: TestClient):
    # escapeHtml() only safely encodes for text-node insertion, not for a
    # value landing inside a double-quoted HTML attribute -- family options
    # must be built via element properties, not `<option value="...">`
    # string templating (#727 R2 finding).
    js = client.get("/static/agreement.js").text
    assert "createElement('option')" in js
    assert 'value="${escapeHtml(name)}"' not in js


def test_backend_endpoints_the_ui_calls_return_expected_shape(client: TestClient, fake_run: Path):
    ci = client.get(f"/api/runs/{fake_run.name}/leaderboard/ci").json()
    assert "leaderboard" in ci
    assert all("ci_low" in row and "ci_high" in row for row in ci["leaderboard"])

    detailed = client.get(f"/api/runs/{fake_run.name}/agreement/detailed").json()
    assert "small_sample" in detailed["agreement"]
    assert "rankings" in detailed

    families = client.get(f"/api/runs/{fake_run.name}/agreement/families").json()
    assert "families" in families
    assert "woodwind" in families["families"]
