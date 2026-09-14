"""Wiring tests for the DoE what-if budget slider (#697 S2, stretch)."""

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
def client(fake_registry: Path, tmp_path: Path) -> TestClient:
    app = create_studio_app(registry_path=fake_registry, repo_root=tmp_path)
    return TestClient(app)


def test_doe_static_files_are_served(client: TestClient):
    css = client.get("/static/doe.css")
    js = client.get("/static/doe.js")
    assert css.status_code == 200
    assert js.status_code == 200
    assert "doeBudgetSlider" in css.text
    assert "loadDoeWhatIf" in js.text


def test_index_html_references_doe_whatif(client: TestClient):
    html = client.get("/").text
    assert '/static/doe.css' in html
    assert '/static/doe.js' in html
    assert 'id="doeWhatIfCard"' in html
    assert 'id="doeBudgetSlider"' in html
    assert 'id="doeWhatIfSummary"' in html


def test_studio_js_dispatches_to_doe_whatif_on_tasks_tab(client: TestClient):
    js = client.get("/static/studio.js").text
    assert "loadDoeWhatIf()" in js


@pytest.mark.skipif(
    not Path("makerbench/arena_studio/doe.py").exists(),
    reason="needs #709 (the /api/doe/preview route, backed by doe.py) merged into "
    "this branch's base first; this PR is UI-only and documents that "
    "cross-dependency in its own body",
)
def test_doe_preview_endpoint_never_assumes_zero_cost_for_unknown_models(client: TestClient):
    """The exact property the slider's 'Unknown cost' bucket depends on."""
    response = client.get(
        "/api/doe/preview",
        params={"instruments": "ocarina", "models": "openrouter-some-exotic-model", "levels": "L1", "seeds": "0"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["cells"][0]["estimate"]["cost_usd"] is None
