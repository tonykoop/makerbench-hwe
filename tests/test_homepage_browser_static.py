"""Exercise the actual generated homepage with JavaScript disabled (#837)."""

import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]


def test_homepage_without_javascript_exposes_committed_section_data():
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(ROOT / "site")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch(headless=True)
            except playwright.Error:
                if os.environ.get("REQUIRE_HOMEPAGE_BROWSER") == "1":
                    raise
                pytest.skip("Chromium is not installed")
            context = browser.new_context(java_script_enabled=False)
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="load")
            data = json.loads((ROOT / "site/data/leaderboard.json").read_text())
            assert f"results as of {data['data_updated'][:10]}" in page.locator("#freshness").inner_text()
            assert page.locator("#task-grid .task").count() == len(data["task_families"])
            assert page.locator("#pack-grid .pack-card").count() == len(data["roadmap"]["packs"])
            assert page.locator("#chart-static-data tbody tr").count() > 0
            assert page.locator("#arena").is_visible()
            assert page.locator("#arena-runs tbody tr").count() > 0
            assert page.locator("#findings").is_visible()
            assert page.locator("#ecosystem-grid .eco-node").count() == len(data["ecosystem"]["nodes"])
            assert page.locator("#lscape-container tbody tr").count() == len(
                json.loads((ROOT / "site/data/landscape.json").read_text())["entries"]
            )
            assert page.locator("#cite-bibtex").inner_text() == data["citation"]["bibtex"]
            assert "Loading" not in page.locator("body").inner_text()
            assert all(section.is_visible() for section in page.locator("main section").all())
            screenshot = os.environ.get("HOMEPAGE_STATIC_SCREENSHOT")
            if screenshot:
                page.screenshot(path=screenshot, full_page=True)
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
