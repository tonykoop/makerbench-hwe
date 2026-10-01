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
            requests = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append(request.url))
            for run, title, rows in (("post3-models", "Comparing AI models", 2),
                                     ("post3-backends", "Comparing CAD tools", 3),
                                     ("strings-gallery", "String-instrument gallery", 18),
                                     ("kora", "Kora: text brief and reference photo", 6)):
                page.goto(f"http://127.0.0.1:{port}/#/runs/{run}")
                page.get_by_role("heading", name=title, exact=True).wait_for()
                assert page.locator(".matchup-entrant").count() == rows
                assert "physical instruments in CAD" in page.locator(".demo-intro").inner_text()
                assert page.get_by_role("combobox").locator("option", has_text=title).count() == 1
                playwright.expect(page.locator(".server-status")).to_contain_text("Server ")
                assert page.locator(".demo-header-links a").count() == 3
                assert page.get_by_role("link", name="Try it locally", exact=True).get_attribute("href") == "https://github.com/tonykoop/makerbench-hwe/blob/main/docs/showcase/try-it.md"
                assert page.locator(".demo-hero img").count() == 1
                source = page.locator(".demo-source")
                assert source.get_attribute("href").startswith("https://github.com/tonykoop/makerbench-hwe/blob/main/docs/showcase/")
                assert source.get_attribute("rel") == "noopener noreferrer"
                content = page.locator("main").inner_text()
                for jargon in ("published objective aggregate", "Measured mesh gate rate", "Varied:", "Held:",
                               "claude-code-", "nonzero_volume", "min_wall"):
                    assert jargon not in content
                if run == "post3-models":
                    assert page.get_by_role("heading", name="Claude Opus 5.5", exact=True).count() == 1
                    assert page.get_by_role("heading", name="Claude Sonnet 5.5", exact=True).count() == 1
                    assert page.get_by_text("Passed all 6 build checks in 3 of 3 runs.", exact=True).count() == 2
                    assert "What changed: the AI model" in content and "no reference image" in content
                if run in {"post3-models", "post3-backends"}:
                    for card in page.locator(".matchup-entrant").all():
                        assert card.locator(".demo-render-strip img").count() == 3
                        assert card.locator(".demo-render-strip figcaption").all_text_contents() == ["Run 0", "Run 1", "Run 2"]
                        assert card.locator(".demo-check[data-state='pass']").count() == 6
                    chip = page.locator(".demo-check").first
                    chip.hover()
                    tooltip = chip.locator("[role='tooltip']")
                    assert tooltip.is_visible()
                    assert "Measurements and thresholds were not saved" in tooltip.inner_text()
                    chip.focus()
                    page.keyboard.press("Escape")
                    assert not tooltip.is_visible()
                if run == "post3-backends":
                    story = page.locator(".demo-story")
                    assert "caught a bug in its own scoring" in story.inner_text()
                    assert "one introductory ocarina task" in story.inner_text()
                    assert "stays at zero" in story.inner_text()
                    assert story.locator("a").count() == 2
                    table_rows = story.locator("tbody tr")
                    assert table_rows.count() == 3
                    assert table_rows.nth(0).locator("td").all_text_contents() == ["100.0%", "100.0%", "100.0%"]
                    assert table_rows.nth(1).locator("td").all_text_contents() == ["77.8%", "88.9%", "100.0%"]
                    assert table_rows.nth(2).locator("td").all_text_contents() == ["55.6%", "66.7%", "100.0%"]
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    page.locator("h1").hover()
                    page.locator("h1").focus()
                    assert not tooltip.is_visible()
                if run == "strings-gallery":
                    assert page.locator(".demo-gallery .matchup-entrant").count() == 18
                    assert "Build checks were not completed." in page.locator(".matchup-entrant").nth(15).inner_text()
                    assert "Passed 0 of 6" not in content
                    assert page.locator(".matchup-entrant").nth(15).locator(".demo-check[data-state='unknown']").count() == 6
                if run == "kora":
                    assert page.locator(".demo-context-arm").count() == 2
                    assert all(arm.locator(".matchup-entrant").count() == 3 for arm in page.locator(".demo-context-arm").all())
                    assert page.locator(".demo-reference img").count() == 1
                    assert "Photo from tonykoop/kora, CC BY 4.0." in page.locator(".demo-reference figcaption").inner_text()
                    assert page.get_by_role("link", name="CC BY 4.0", exact=True).count() == 1
                    assert "Passed 5 of 6 build checks in this run." in content
                    chip = page.locator(".matchup-entrant").nth(5).locator(".demo-check[data-state='fail']")
                    chip.focus()
                    assert chip.locator("[role='tooltip']").is_visible()
                    explanation = chip.locator("[role='tooltip']").inner_text()
                    assert "0.0702 mm" in explanation and "threshold 1 mm" in explanation
                    assert "tolerance 0.05 mm" in explanation and "body_0" in explanation
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.wait_for_function("Array.from(document.querySelectorAll('img')).every(i => i.complete && i.naturalWidth > 0)")
                assert page.get_by_text("Voting as", exact=True).count() == 0
                assert page.get_by_role("link", name="Launch", exact=True).count() == 0
                assert page.get_by_role("link", name="Blind voting", exact=True).count() == 0
                assert page.locator("[data-open-trial]").count() == 0
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert not errors
            assert requests and all(url.startswith(f"http://127.0.0.1:{port}/") for url in requests)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
