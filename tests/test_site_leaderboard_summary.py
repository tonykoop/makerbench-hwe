"""Summary board grouping, rank integrity, native details, and responsive layout."""

import copy
import importlib.util
import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("summary_builder", ROOT / "site/build_data.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def summary_payload():
    payload = json.loads((ROOT / "site/data/leaderboard.json").read_text())
    template = next(m for m in payload["models"] if m["tracks"]["blind"].get("n_families_scored", 0) >= 4)

    def model(identifier, family, coverage, score, effort="high", league="autonomous"):
        row = copy.deepcopy(template)
        row.update(identifier=identifier, model_family=family, reasoning_level=effort,
                   agent_identifier="test_harness", league=league, is_control=False,
                   is_human_baseline=False, model_page="models/test/index.html")
        for track in row["tracks"].values():
            track.update(has_data=True, n_families_scored=coverage, overall_mean=score)
        return row

    payload["models"] = [
        model("alpha-low", "alpha", 4, 4.0, "low"),
        model("alpha-medium", "alpha", 8, 3.0, "medium"),
        model("alpha-xhigh", "alpha", 8, 3.0, "xhigh"),
        model("alpha-high", "alpha", 8, 3.0),
        model("alpha-high-low-score", "alpha", 8, 2.0),
        model("beta", "beta", 4, 3.5),
        model("sparse", "sparse", 1, 4.0),
        model("assisted", "assisted", 6, 4.0, league="assisted_workflow"),
    ]
    payload["leagues"] = [{"id": "autonomous", "label": "Autonomous"},
                          {"id": "assisted_workflow", "label": "Assisted workflow"}]
    return payload


def test_prerender_keeps_every_variant_and_family_without_mutating_data():
    data = summary_payload()
    original = copy.deepcopy(data)
    html = builder._prerender_leaderboard_html(data)
    assert data == original
    assert html.count('class="lb-primary"') == 4
    assert html.count('class="lb-variant"') == 4
    assert "+4 variants" in html
    assert html.count('class="lb-families"') == len(data["models"])
    assert html.count('class="lb-family-table"') == len(data["models"])
    assert "Limited coverage · unranked" in html
    assert " open" not in html


@pytest.mark.parametrize("javascript", [False, True])
@pytest.mark.parametrize("width", [1440, 390])
def test_summary_rank_details_and_responsiveness(javascript, width):
    playwright = pytest.importorskip("playwright.sync_api")
    payload = summary_payload()
    html = (ROOT / "site/index.html").read_text()
    import re
    html = re.sub(r'(<!-- prerender:leaderboard -->).*?(<!-- /prerender:leaderboard -->)',
                  lambda m: m[1] + builder._prerender_leaderboard_html(payload) + m[2],
                  html, flags=re.S)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(ROOT / "site")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(args=os.environ.get("ARENA_STUDIO_BROWSER_ARGS", "").split())
            page = browser.new_page(java_script_enabled=javascript, viewport={"width": width, "height": 1000})
            base = f"http://127.0.0.1:{server.server_port}"
            page.route(base + "/", lambda route: route.fulfill(content_type="text/html", body=html))
            page.route("**/data/leaderboard.json", lambda route: route.fulfill(
                content_type="application/json", body=json.dumps(payload)))
            page.goto(base + "/", wait_until="networkidle")
            if javascript:
                page.locator("#lb-note").filter(has_text="4 models").wait_for()
            board = page.locator("#lb-container")
            autonomous = board.locator('.lb-league[aria-label="Autonomous"]')
            broad = autonomous.locator("table.lb-summary").first
            primary = broad.locator(":scope > tbody > tr.lb-primary")
            assert primary.count() == 2
            assert "beta" in primary.nth(0).inner_text()
            assert "alpha-high" in primary.nth(1).inner_text()
            assert "alpha-high-low-score" not in primary.nth(1).inner_text()
            assert primary.locator(".lb-rank").all_text_contents() == ["1", "2"]
            assert board.locator(".lb-variant .lb-rank").all_text_contents() == ["—"] * 4
            sparse_table = autonomous.locator("table.lb-summary").last
            assert sparse_table.locator(".lb-primary .lb-rank").inner_text() == "—"
            assert "sparse" in sparse_table.locator(".lb-primary").inner_text()
            assert board.locator('.lb-league[aria-label="Assisted workflow"] .lb-primary .lb-rank').inner_text() == "1"
            assert broad.locator(":scope > thead th").count() == 14
            for heading in broad.locator(":scope > thead th").all():
                assert heading.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
            details = broad.locator(".lb-families").first
            assert details.locator("tbody tr").count() == len(payload["task_families"])
            assert not details.get_attribute("open")
            details.locator("summary").click()
            assert details.locator("tbody tr").first.is_visible()
            variants = broad.locator(".lb-variants")
            variants.locator(":scope > summary").click()
            assert variants.locator(".lb-variant").count() == 4
            assert variants.locator(".lb-variant").first.is_visible()
            assert board.evaluate("el => el.scrollWidth <= el.clientWidth")
            assert broad.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
            if javascript:
                broad.locator('button[data-key="overall"]').click()
                # Sorting changes position, never the overall-derived rank.
                assert broad.locator(":scope > tbody > .lb-primary .lb-rank").all_text_contents() == ["2", "1"]
                page.get_by_role("button", name="Perception", exact=True).click()
                assert board.locator(".lb-primary").count() == 4
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
