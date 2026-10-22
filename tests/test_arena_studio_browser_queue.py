"""Fixture-only browser checks and the four required queue screenshots (#1160)."""

import json
import os
from pathlib import Path

import pytest

if os.environ.get("ARENA_STUDIO_REQUIRE_BROWSER") == "1":
    from playwright.sync_api import sync_playwright
else:
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

from makerbench.arena_studio import control_room_queue as cq  # noqa: E402
from test_arena_studio_browser_newrun import _launch, _serve  # noqa: E402
from test_control_room_queue import build_queue_repo  # noqa: E402


@pytest.fixture
def queue_studio(tmp_path, monkeypatch, request):
    repo = build_queue_repo(tmp_path)
    monkeypatch.setattr(cq, "fetch_written_go", lambda: False)
    monkeypatch.setattr(cq, "status_lights", lambda root: [
        {"label": "PC awake", "state": "ok", "detail": "Studio is responding"},
        {"label": "SolidWorks seat", "state": "warn", "detail": "bridge not reachable"},
        {"label": "Fusion seat", "state": "warn", "detail": "bridge not reachable"},
        {"label": "Claude CLI", "state": "ok", "detail": "on PATH · sign-in unknown"},
        {"label": "Codex CLI", "state": "ok", "detail": "on PATH · sign-in unknown"},
        *({"label": b, "state": "ok", "detail": "fixture backend"} for b in ("FreeCAD", "OpenSCAD", "Blender")),
        {"label": "OpenRouter key", "state": "warn", "detail": "not in server environment"},
        {"label": "CADAM app", "state": "warn", "detail": "not reachable"},
        {"label": "Claude weekly quota", "state": "unknown", "detail": "unknown"},
        {"label": "Codex weekly quota", "state": "unknown", "detail": "unknown"},
    ])
    def preflight(self, job_id):
        return {"job_id": job_id, "ok": False, "checks": [{"label": "Fixture preflight", "ok": True}],
                "entrants": [{"id": "freecad-sol", "route": "Codex · Sol · FreeCAD", "ok": True, "notes": ["fixture backend ready"]},
                             {"id": "freecad-sonnet", "route": "Claude Code · Sonnet · FreeCAD", "ok": False, "notes": ["fixture sign-in unavailable"]}],
                "notes": []}
    monkeypatch.setattr(cq.QueueStore, "preflight", preflight)
    server, thread, url = _serve(repo, control_room_queue_writes=getattr(request, "param", False))
    yield repo, url
    server.should_exit = True
    thread.join(timeout=10)


def open_queue(browser, url, **options):
    context = browser.new_context(viewport={"width": 1440, "height": 1100}, **options)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url + "/#/queue")
    page.locator(".queue-night").first.wait_for()
    page.locator(".queue-lights li").first.wait_for()
    return context, page, errors


@pytest.mark.parametrize("queue_studio", [False, True], indirect=True)
def test_browser_queue_edits_preflight_and_undo(queue_studio):
    repo, url = queue_studio
    queue = repo / "runs" / "nightly-cad-queue.json"
    before = queue.read_bytes()
    with sync_playwright() as p:
        browser = _launch(p)
        context, page, errors = open_queue(browser, url)
        draft = page.get_by_test_id("draft-banner").count() == 1
        assert page.get_by_role("navigation", name="Control Room screens").get_by_role("link").all_text_contents() == [
            "Morning Review", "New Run", "Tonight's Queue"]
        assert page.get_by_role("switch").is_disabled()
        assert "unknown (Windows task)" in page.locator(".queue-kv").inner_text()
        page.get_by_role("button", name="Move Ocarina up", exact=True).click()
        page.wait_for_function("() => document.querySelectorAll('.queue-night')[1].dataset.job === 'fixture-ocarina'")
        card = page.locator('[data-job="fixture-ocarina"]')
        card.get_by_role("button", name="Move to tomorrow").click()
        page.wait_for_function("() => [...document.querySelectorAll('.queue-night[data-status=queued]')].at(-1).dataset.job === 'fixture-ocarina'")
        card.get_by_role("button", name="Select for preflight").click()
        page.get_by_role("button", name="Run preflight", exact=True).click()
        page.get_by_test_id("preflight-result").wait_for()
        assert "NO-GO" in page.get_by_test_id("preflight-result").inner_text()
        assert "Codex · Sol · FreeCAD" in page.get_by_test_id("preflight-result").inner_text()
        card.get_by_role("button", name="Remove", exact=True).click()
        page.get_by_role("button", name="Undo", exact=True).wait_for()
        assert page.locator(".queue-night").count() == 5
        page.get_by_role("button", name="Undo", exact=True).click()
        page.locator('[data-job="fixture-ocarina"]').wait_for()
        assert page.locator(".queue-night").count() == 6
        if draft:
            assert queue.read_bytes() == before
        else:
            assert json.loads(queue.read_text())["jobs"][4]["job_id"] == "fixture-ocarina"
        assert errors == []
        context.close()
        browser.close()


@pytest.mark.parametrize("width, theme", [(1440, "light"), (1440, "dark"), (390, "light"), (390, "dark")])
def test_queue_layout_screenshots(queue_studio, tmp_path, width, theme):
    _, url = queue_studio
    with sync_playwright() as p:
        browser = _launch(p)
        # Pass viewport separately from open_queue's desktop default.
        context, page, errors = open_queue(browser, url, color_scheme=theme)
        page.set_viewport_size({"width": width, "height": 1100 if width == 1440 else 844})
        page.locator('.queue-thumb img').first.wait_for(state="attached")
        page.evaluate("() => document.fonts.ready")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert page.locator(".queue-night").count() == 6
        if width == 1440:
            assert page.locator(".queue-rail").bounding_box()["x"] > page.locator(".queue-upcoming").bounding_box()["x"]
        target = Path(os.environ.get("ARENA_STUDIO_SCREENSHOTS") or tmp_path / "screens")
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / f"queue-{width}-{theme}.png"), full_page=True)
        assert errors == []
        context.close()
        browser.close()
