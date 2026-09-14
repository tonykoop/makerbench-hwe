"""Real-browser checks for the rebuilt Arena Studio frontend (Playwright).

Opt-in: skipped unless Playwright and its Chromium are installed, which they
never are in the base environment (Playwright is not a project dependency).
The non-required, path-filtered `studio-browser` CI workflow installs them and
sets ARENA_STUDIO_REQUIRE_BROWSER=1 so a missing browser fails instead of
skipping. Set ARENA_STUDIO_SCREENSHOTS=<dir> to keep screenshots.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

REQUIRE_BROWSER = os.environ.get("ARENA_STUDIO_REQUIRE_BROWSER") == "1"

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the environment
    if REQUIRE_BROWSER:
        raise
    pytest.skip("playwright is not installed", allow_module_level=True)

pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

from makerbench.arena_studio import create_studio_app

WEBGL_MODES = {
    "webgl": [],
    # No WebGL at all, as on GPU-less RDP sessions.
    "zero-webgl": ["--disable-3d-apis", "--disable-webgl", "--disable-webgl2"],
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def studio_repo(tmp_path: Path) -> Path:
    run_dir = tmp_path / "runs" / "code_cad_arena" / "round-fixture"
    run_dir.mkdir(parents=True)
    run_log = {
        "started_at": "2026-09-14T09:30:00Z",
        "config": {
            "model_ids": ["model-a", "model-b"],
            "instruments": ["ocarina"],
            "backend": "openscad",
            "context_tier": "blind",
        },
        "trials": [
            {"trial_id": "t-1", "model_id": "model-a", "instrument_id": "ocarina", "seed": 0, "rep": 0},
            {"trial_id": "t-2", "model_id": "model-b", "instrument_id": "ocarina", "seed": 0, "rep": 0},
        ],
    }
    (run_dir / "run_log.json").write_text(json.dumps(run_log), encoding="utf-8")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"instruments": []}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def studio_url(studio_repo: Path):
    app = create_studio_app(registry_path=studio_repo / "registry.json", repo_root=studio_repo)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("Studio test server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def screenshot_dir(tmp_path: Path) -> Path:
    target = Path(os.environ.get("ARENA_STUDIO_SCREENSHOTS") or tmp_path / "screens")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _extra_browser_args() -> list[str]:
    # Environment-specific Chromium flags. Under WSL, screenshots only render with
    # "--disable-gpu --disable-software-rasterizer" (which also removes WebGL, so
    # both modes render the zero-WebGL path there). CI leaves this empty.
    return os.environ.get("ARENA_STUDIO_BROWSER_ARGS", "").split()


def _launch(playwright, mode: str):
    try:
        return playwright.chromium.launch(args=[*WEBGL_MODES[mode], *_extra_browser_args()])
    except Exception as error:  # executable missing
        if REQUIRE_BROWSER:
            raise
        pytest.skip(f"chromium is not available: {error}")


def _open(browser, url: str, **context_args):
    context = browser.new_context(**context_args)
    page = context.new_page()
    problems: list[str] = []
    page.on("console", lambda msg: problems.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda error: problems.append(str(error)))
    page.goto(url)
    return context, page, problems


@pytest.mark.parametrize("mode", sorted(WEBGL_MODES))
@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_runs_screen_renders_cleanly(studio_url: str, screenshot_dir: Path, mode: str, color_scheme: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright, mode)
        context, page, problems = _open(
            browser, f"{studio_url}/#/runs", viewport={"width": 1440, "height": 900}, color_scheme=color_scheme
        )
        if mode == "zero-webgl":
            assert page.evaluate("() => document.createElement('canvas').getContext('webgl2')") is None

        page.get_by_role("link", name="round-fixture").click()
        page.get_by_role("heading", name="round-fixture", level=2).wait_for()
        assert page.get_by_text("Unknown", exact=False).first.is_visible()
        assert page.title() == "Runs: Arena Studio"
        assert page.locator("select").input_value() == "round-fixture"
        page.screenshot(path=str(screenshot_dir / f"f1-runs-{mode}-{color_scheme}.png"), full_page=True)
        assert problems == []
        context.close()
        browser.close()


def test_unreachable_run_shows_an_actionable_error(studio_url: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        context, page, problems = _open(browser, f"{studio_url}/#/runs/no-such-run")
        alert = page.get_by_role("alert")
        alert.wait_for()
        assert "not found" in alert.inner_text()
        assert page.get_by_role("button", name="Try again").is_visible()
        # The failed request is the only console error the browser reports.
        assert all("404" in problem for problem in problems)
        context.close()
        browser.close()


def test_keyboard_skip_link_and_screen_focus(studio_url: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        context, page, problems = _open(browser, f"{studio_url}/#/runs")
        page.get_by_role("link", name="round-fixture").wait_for()
        page.keyboard.press("Tab")
        assert page.evaluate("() => document.activeElement.textContent.trim()") == "Skip to content"
        page.keyboard.press("Enter")
        assert page.evaluate("() => document.activeElement.id") == "main"
        # Keyboard-only: reach the run link and open it.
        for _ in range(12):
            page.keyboard.press("Tab")
            if page.evaluate("() => document.activeElement.textContent.trim()") == "round-fixture":
                break
        page.keyboard.press("Enter")
        page.get_by_role("heading", name="round-fixture", level=2).wait_for()
        assert problems == []
        context.close()
        browser.close()


def test_phone_width_has_no_horizontal_scroll(studio_url: str, screenshot_dir: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        context, page, problems = _open(
            browser, f"{studio_url}/#/runs/round-fixture", viewport={"width": 400, "height": 860}
        )
        page.get_by_role("heading", name="round-fixture", level=2).wait_for()
        overflow = page.evaluate(
            "() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth"
        )
        assert overflow <= 0
        page.screenshot(path=str(screenshot_dir / "f1-runs-400px.png"), full_page=True)
        assert problems == []
        context.close()
        browser.close()
