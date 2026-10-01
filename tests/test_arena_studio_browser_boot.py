"""Actual browser recovery before ES-module startup, and slow API loading."""
import asyncio
import os
import socket
import threading
import time

import pytest

REQUIRED = os.environ.get("ARENA_STUDIO_REQUIRE_BROWSER") == "1"
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if REQUIRED:
        raise
    pytest.skip("Playwright unavailable", allow_module_level=True)
uvicorn = pytest.importorskip("uvicorn")
pytest.importorskip("fastapi")


@pytest.fixture
def studio(tmp_path):
    from makerbench.arena_studio import create_studio_app
    registry = tmp_path / "registry.json"
    registry.write_text('{"instruments": []}')
    app = create_studio_app(registry_path=registry, repo_root=tmp_path)
    app.state.runs_delay = 0

    @app.middleware("http")
    async def delay_runs(request, call_next):
        if request.url.path == "/api/runs":
            await asyncio.sleep(app.state.runs_delay)
        return await call_next(request)

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        yield f"http://127.0.0.1:{port}", app
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def page():
    with sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch()
        except Exception:
            if REQUIRED:
                raise
            pytest.skip("Chromium unavailable")
        page = browser.new_page()
        yield page
        browser.close()


def assert_recovery(page):
    alert = page.get_by_role("alert")
    alert.get_by_role("heading", name="Arena Studio could not start").wait_for(timeout=8000)
    assert "Ctrl+Shift+R" in alert.inner_text()
    assert "Module scripts" in alert.inner_text()
    assert alert.get_by_role("link", name="Check server health").get_attribute("href") == "/api/health"


@pytest.mark.parametrize("module", ["app/main.js", "vendor/preact.module.js"])
def test_failed_module_load_shows_recovery(page, studio, module):
    page.route("**/static/" + module, lambda route: route.abort("blockedbyclient"))
    page.goto(studio[0], wait_until="domcontentloaded")
    assert_recovery(page)
    assert "failed to load" in page.get_by_role("alert").inner_text()


def test_nonmounting_module_times_out_after_five_seconds(page, studio):
    page.route("**/static/app/main.js", lambda route: route.fulfill(
        content_type="application/javascript", body="export {};"))
    started = time.monotonic()
    page.goto(studio[0], wait_until="domcontentloaded")
    assert_recovery(page)
    assert 4.5 <= time.monotonic() - started < 8
    assert "5 seconds" in page.get_by_role("alert").inner_text()


@pytest.mark.parametrize("code", [
    'throw new Error("BOOT_SENTINEL <img src=x>");',
    'Promise.reject(new Error("BOOT_SENTINEL <img src=x>"));',
])
def test_uncaught_boot_errors_are_visible_as_text(page, studio, code):
    page.route("**/static/app/main.js", lambda route: route.fulfill(
        content_type="application/javascript", body=code))
    page.goto(studio[0], wait_until="domcontentloaded")
    assert_recovery(page)
    assert "BOOT_SENTINEL <img src=x>" in page.get_by_role("alert").inner_text()
    assert page.get_by_role("alert").locator("img").count() == 0


def test_slow_run_scan_shows_loading_without_boot_failure(page, studio):
    url, app = studio
    app.state.runs_delay = 4
    page.goto(url, wait_until="domcontentloaded")
    page.get_by_role("status").filter(has_text="Loading runs…").wait_for(timeout=2000)
    assert page.get_by_role("alert").count() == 0
    page.get_by_text("No arena runs found", exact=False).wait_for(timeout=7000)
    page.wait_for_timeout(1500)
    assert page.get_by_role("alert").count() == 0
    assert page.locator(".studio").count() == 1


def test_cached_older_entry_without_handshake_stays_mounted(page, studio):
    def older_entry(route):
        response = route.fetch()
        body = response.text().replace(
            "if (window.arenaStudioBoot) window.arenaStudioBoot.mounted();", "")
        route.fulfill(response=response, body=body)
    page.route("**/static/app/main.js", older_entry)
    page.goto(studio[0], wait_until="domcontentloaded")
    page.locator(".studio").wait_for()
    page.wait_for_timeout(5500)
    assert page.get_by_role("alert").count() == 0
