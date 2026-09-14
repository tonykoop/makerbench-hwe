"""Real-browser proof that a <model-viewer> renderer failure (including WebGL
context loss) drives the studio back to the zero-WebGL frame turntable (#698).

The earlier version of this safety net listened for the raw `webglcontextlost`
DOM event on `window`. That event, fired on <model-viewer>'s internal
(shadow-DOM) canvas, is not `composed`, so per the DOM spec it never crosses
the shadow boundary and a window-level listener can never observe it — a real
defect caught by cross-provider review (see #710/#713's PR threads) that a
JS-string-matching test could not catch. This module proves the corrected
mechanism (a listener directly on <model-viewer>'s own light-DOM 'error'
event, which model-viewer re-dispatches on itself specifically so host code
can react to internal renderer failures without shadow-DOM access) by driving
a real headless-Chromium session end to end: dispatch a synthetic 'error'
event on the actual #mvLeft element and assert the fallback UI visibly
activates.

Opt-in only (never a hard dependency): requires `pip install playwright` plus
`playwright install chromium`, neither declared in pyproject.toml. Skips
cleanly when playwright is absent.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

playwright_sync_api = pytest.importorskip("playwright.sync_api")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(port: int, timeout_s: float = 20.0) -> None:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.2)
    raise RuntimeError(f"Arena Studio server never became healthy on port {port}")


BOOTSTRAP = """
import sys
from pathlib import Path
import uvicorn
from makerbench.arena_studio import create_studio_app

repo_root = Path(sys.argv[1])
port = int(sys.argv[2])
app = create_studio_app(repo_root=repo_root)
uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
"""


@pytest.fixture
def fixture_run(tmp_path: Path) -> Path:
    """A minimal run_dir with one voteable pair — no real mesh/GLB needed since
    this test never actually renders 3D content, only exercises the error-event
    wiring on a <model-viewer> element that exists as a bare (unregistered, since
    model-viewer.min.js is not vendored in this repo) custom element."""
    run_dir = tmp_path / "runs" / "code_cad_arena" / "context-loss-run"
    run_dir.mkdir(parents=True)
    png_a = run_dir / "a.png"
    png_b = run_dir / "b.png"
    png_a.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    png_b.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    run_log = {
        "started_at": "2026-09-14T00:00:00Z",
        "config": {"model_ids": ["model-a", "model-b"], "instruments": ["sambuca"]},
        "trials": [
            {
                "trial_id": "trial-a",
                "model_id": "model-a",
                "instrument_id": "sambuca",
                "seed": 0,
                "rep": 0,
                "result": {"render_ok": True, "artifacts": {"png_path": str(png_a)}},
            },
            {
                "trial_id": "trial-b",
                "model_id": "model-b",
                "instrument_id": "sambuca",
                "seed": 0,
                "rep": 0,
                "result": {"render_ok": True, "artifacts": {"png_path": str(png_b)}},
            },
        ],
    }
    (run_dir / "run_log.json").write_text(json.dumps(run_log), encoding="utf-8")
    return tmp_path


@pytest.fixture
def studio_server(fixture_run: Path):
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-c", BOOTSTRAP, str(fixture_run), str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_health(port)
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_context_loss_error_event_triggers_turntable_fallback(studio_server: str):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-gpu", "--disable-webgl", "--disable-webgl2"],
            )
        except Exception as exc:
            pytest.skip(f"Chromium not available for Playwright: {exc}")
        try:
            page = browser.new_page()
            page.goto(studio_server + "/", wait_until="networkidle", timeout=15_000)
            page.evaluate("switchTab('arena')")

            # This test's own scope is the error-event wiring, not the data-driven
            # mode-switch branching in renderViewer() (which requires a candidate
            # with a real model3d_path — orthogonal to what's under test here).
            # Put the DOM in the same visible state setViewerMode('webgl') would
            # for a candidate that has one.
            page.evaluate(
                "document.getElementById('imgLeft').style.display = 'none';"
                "document.getElementById('mvLeft').style.display = 'block';"
            )

            # The real, fixed mechanism: model-viewer's own light-DOM 'error' event,
            # dispatched directly on the element (not window) — this is the one part
            # of the DOM contract that does not depend on shadow-composition rules,
            # since it's a plain listener on the very node the event targets.
            page.eval_on_selector("#mvLeft", "el => el.dispatchEvent(new Event('error'))")
            page.wait_for_function(
                "document.getElementById('webglNotice').style.display === 'block'",
                polling=100,
                timeout=5000,
            )

            notice_text = page.eval_on_selector("#webglNotice", "el => el.textContent")
            assert "WebGL context was lost" in notice_text

            # The fallback must have visibly reverted the UI to turntable mode.
            assert page.eval_on_selector(
                "#btnTurntableMode", "el => el.classList.contains('active')"
            )
            assert page.eval_on_selector("#imgLeft", "el => el.style.display") != "none"
            assert page.eval_on_selector("#mvLeft", "el => el.style.display") == "none"
        finally:
            browser.close()
