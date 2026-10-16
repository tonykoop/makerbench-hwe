"""Capability profile comparison controls use measured, current-track rows (#1020)."""

import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]


def test_profile_comparison_and_grid():
    payload = json.loads((ROOT / "site/data/leaderboard.json").read_text())
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(ROOT / "site"))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
            page.wait_for_function("typeof Chart !== 'undefined' && Chart.getChart('chart-profile-0')")
            for track in ("blind", "perception"):
                page.locator(f'#track-toggle button[data-track="{track}"]').click()
                measured = [
                    m for m in payload["models"] if track in m["tracks"]
                    and any(c.get("mean_score") is not None for c in
                            m["tracks"][track].get("capability_profile", {}).values())
                ]
                expected = sorted(measured, key=lambda m: (
                    -(m["tracks"][track].get("overall_mean") if
                      m["tracks"][track].get("overall_mean") is not None else float("-inf")),
                    m["row_id"],
                ))[:3]
                assert page.locator('.profile-picker input').count() == len(measured)
                assert page.locator('.profile-plots canvas').count() == 1
                rows = page.evaluate("""() => Chart.getChart('chart-profile-0').data.datasets.map(
                    d => ({id: d._model.row_id, values: d.data, actual: d._actualData}))""")
                assert [r["id"] for r in rows] == [m["row_id"] for m in expected]
                assert all(r["values"] == r["actual"] for r in rows)
                assert page.locator('.profile-picker input:checked').count() == 3
                assert page.locator('.profile-picker input:not(:checked):enabled').count() == 0
                page.locator('.profile-picker input:checked').first.uncheck()
                assert page.evaluate("Chart.getChart('chart-profile-0').data.datasets.length") == 2
                page.locator('.profile-picker input:not(:checked):enabled').last.check()
                page.locator('.profile-show-all input').check()
                ids = page.evaluate("""() => Array.from(document.querySelectorAll('.profile-plots canvas'))
                    .flatMap(c => Chart.getChart(c).data.datasets.map(d => d._model.row_id))""")
                assert set(ids) == {m["row_id"] for m in measured}
                labels = page.evaluate("""() => Array.from(document.querySelectorAll('.profile-plots canvas'))
                    .flatMap(c => Chart.getChart(c).data.datasets.map(d => d.label))""")
                assert len(labels) == len(set(labels))
                assert all("effort" in label.lower() or "Thinking" in label for label in labels)
                page.locator('.profile-show-all input').uncheck()
                assert page.locator('.profile-plots canvas').count() == 1
            # Synthetic public-data controls: zero is measured; null is missing.
            blind = [m for m in payload["models"] if "blind" in m["tracks"]]
            for m in blind:
                for cell in m["tracks"]["blind"].get("capability_profile", {}).values():
                    cell["mean_score"] = None
            axis = payload["capability_axes"][0]["id"]
            blind[0]["tracks"]["blind"]["capability_profile"][axis]["mean_score"] = 0
            page.route("**/data/leaderboard.json", lambda route: route.fulfill(
                content_type="application/json", body=json.dumps(payload)))
            page.reload(wait_until="networkidle")
            assert page.locator('.profile-picker input').count() == 1
            assert page.locator('.profile-unavailable').inner_text().startswith("No profile data yet:")
            assert page.evaluate("Chart.getChart('chart-profile-0').data.datasets[0].data[0]") == 0
            page.locator('.profile-picker input').uncheck()
            assert page.locator('.profile-plots canvas').count() == 0
            assert "Select up to 3" in page.locator('.profile-plots').inner_text()
            blind[0]["tracks"]["blind"]["capability_profile"][axis]["mean_score"] = None
            page.reload(wait_until="networkidle")
            assert page.locator('.profile-plots canvas').count() == 0
            assert "No chartable" in page.locator('.profile-plots').inner_text()
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
