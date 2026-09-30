"""Actual read-only demo UI and public image display in Chromium."""
import os
import socket
import threading
import time

import pytest

playwright = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")


def test_all_showcases_are_visible_without_mutating_controls():
    from makerbench.arena_studio.demo import create_demo_app
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_demo_app(), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        with playwright.sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch()
            except playwright.Error:
                if os.environ.get("ARENA_STUDIO_REQUIRE_BROWSER") == "1":
                    raise
                pytest.skip("Chromium unavailable")
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.add_init_script("Object.defineProperty(window, 'localStorage', {get() { throw new Error('Storage unavailable'); }});")
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            for run, title, rows in (("post3-models", "Comparing AI models", 2),
                                     ("post3-backends", "Comparing CAD tools", 3),
                                     ("strings-gallery", "String-instrument gallery", 18),
                                     ("kora", "Kora: text brief and reference photo", 6)):
                page.goto(f"http://127.0.0.1:{port}/#/runs/{run}")
                page.get_by_role("heading", name=title, exact=True).wait_for()
                assert page.locator(".matchup-entrant").count() == rows
                assert "physical instruments in CAD" in page.locator(".demo-intro").inner_text()
                assert page.get_by_role("combobox").locator("option", has_text=title).count() == 1
                assert "Server " in page.locator(".server-status").inner_text()
                content = page.locator("main").inner_text()
                for jargon in ("published objective aggregate", "Measured mesh gate rate", "Varied:", "Held:",
                               "claude-code-", "nonzero_volume", "min_wall"):
                    assert jargon not in content
                if run == "post3-models":
                    assert page.get_by_role("heading", name="Claude Opus 5.5", exact=True).count() == 1
                    assert page.get_by_role("heading", name="Claude Sonnet 5.5", exact=True).count() == 1
                    assert page.get_by_text("Passed all 6 build checks in 3 of 3 runs.", exact=True).count() == 2
                    assert "What changed: the AI model" in content and "no reference image" in content
                if run == "strings-gallery":
                    assert "Build checks were not completed." in page.locator(".matchup-entrant").nth(15).inner_text()
                    assert "Passed 0 of 6" not in content
                if run == "kora":
                    assert "Passed 5 of 6 build checks in this run." in content
                page.wait_for_function("Array.from(document.querySelectorAll('img')).every(i => i.complete && i.naturalWidth > 0)")
                assert page.get_by_text("Voting as", exact=True).count() == 0
                assert page.get_by_role("link", name="Launch", exact=True).count() == 0
                assert page.get_by_role("link", name="Blind voting", exact=True).count() == 0
                assert page.locator("[data-open-trial]").count() == 0
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
