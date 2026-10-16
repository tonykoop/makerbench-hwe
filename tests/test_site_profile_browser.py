"""Capability profile comparison controls use measured, current-track rows (#1020)."""

import copy
import json
import runpy
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("source", ["real", "fixture"])
def test_profile_comparison_and_grid(source):
    helpers = runpy.run_path(str(ROOT / "tests/test_site_leaderboard_summary.py"))
    payload = json.loads((ROOT / "site/data/leaderboard.json").read_text())
    if source == "fixture":
        payload = helpers["summary_payload"]()
        for flag in ("is_control", "is_human_baseline"):
            row = copy.deepcopy(payload["models"][0])
            row.update(identifier=flag, model_family=flag, **{flag: True})
            for tr in row["tracks"].values():
                tr.update(n_families_scored=49, overall_mean=4.0)
            payload["models"].append(row)
        for i, row in enumerate(payload["models"]):
            row["row_id"] = str(i)
            for tr in row["tracks"].values():
                tr["capability_profile"][payload["capability_axes"][0]["id"]]["mean_score"] = 2.0
        # Ensure the two tracks have different leaders.
        next(m for m in payload["models"] if m["identifier"] == "beta")["tracks"]["perception"]["overall_mean"] = 2.5
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(ROOT / "site"))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            page = browser.new_page()
            page.route("**/data/leaderboard.json", lambda route: route.fulfill(
                content_type="application/json", body=json.dumps(payload)))
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
                current = copy.deepcopy(payload["models"])
                for row in current:
                    row["tracks"]["blind"] = row["tracks"][track]
                ranked, _, _ = helpers["builder"].select_leaderboard_entries(current)
                expected = [entry[0] for entry in ranked
                            if any(m["row_id"] == entry[0]["row_id"] for m in measured)][:3]
                if source == "fixture":
                    assert expected[0]["identifier"] == ("beta" if track == "blind" else "alpha-high")
                    assert not any(m["is_control"] or m["is_human_baseline"] or
                                   m["identifier"] in ("sparse", "alpha-low") for m in expected)
                assert page.locator('.profile-picker input').count() == len(measured)
                assert page.locator('.profile-plots canvas').count() == 1
                rows = page.evaluate("""() => Chart.getChart('chart-profile-0').data.datasets.map(
                    d => ({id: d._model.row_id, values: d.data, actual: d._actualData}))""")
                assert [r["id"] for r in rows] == [m["row_id"] for m in expected]
                assert all(r["values"] == r["actual"] for r in rows)
                assert page.locator('.profile-picker input:checked').count() == len(expected)
                if len(expected) == 3:
                    assert page.locator('.profile-picker input:not(:checked):enabled').count() == 0
                page.locator('.profile-picker input:checked').first.uncheck()
                assert page.evaluate("Chart.getChart('chart-profile-0').data.datasets.length") == len(expected) - 1
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
            page.locator('.profile-picker input').check()
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
