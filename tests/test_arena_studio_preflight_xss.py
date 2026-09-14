"""Real-browser proof that the Preflight panel renders untrusted API data as
inert text, never active HTML (R2 P4/#739, fixed after review).

The panel interpolates several server-returned fields into innerHTML template
literals: the error `detail`, secret key/status, lock status, queue error text,
and — the highest-risk field — the operator-typed filesystem `path` the server
echoes back verbatim in `paths[].path`. A crafted value in any of these (e.g. an
operator pasting a path containing markup, or a queue/secrets file whose parse
error message embeds file content) could become same-origin script. A JS-string
contract test can prove escapeHtml(...) is *called*, but only a real browser can
prove the resulting DOM text is genuinely inert — this test does that: it mocks
the /api/preflight response with hostile payloads in every interpolated field and
asserts (a) no injected script ever executes and (b) the hostile markup shows up
as literal, escaped text in the rendered panel.

Opt-in only: requires `pip install playwright` + `playwright install chromium`,
neither declared in pyproject.toml. Skips cleanly when playwright is absent.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(port: int, timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_err = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:  # server not up yet
            last_err = exc
            time.sleep(0.2)
    raise RuntimeError(f"Arena Studio server never became healthy: {last_err}")


BOOTSTRAP = """
import sys
from pathlib import Path
import uvicorn
from makerbench.arena_studio import create_studio_app

app = create_studio_app(repo_root=Path(sys.argv[1]))
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")
"""


@pytest.fixture
def studio_server(tmp_path: Path):
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-c", BOOTSTRAP, str(tmp_path), str(port)],
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
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_preflight_panel_renders_hostile_payload_as_inert_text(studio_server: str):
    hostile = "<img src=x onerror=window.__xss_fired=true>"
    fake_response = {
        "ok": False,
        "verdict": hostile,
        "lines": ["x"],
        "secrets": [{"key": hostile, "status": hostile}],
        "lock": {"status": hostile, "pid": None},
        "queue": {"ok": False, "error": hostile, "jobs": []},
        "paths": [{"name": hostile, "path": hostile, "exists": False}],
    }

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
            page.route(
                "**/api/preflight",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(fake_response),
                ),
            )
            page.goto(studio_server + "/", wait_until="networkidle")
            page.evaluate("switchTab('preflight')")

            page.eval_on_selector("#preflightSecretsPath", "el => el.value = 's'")
            page.eval_on_selector("#preflightQueuePath", "el => el.value = 'q'")
            page.eval_on_selector("#preflightOutputRoot", "el => el.value = 'o'")
            page.evaluate("runPreflight()")
            page.wait_for_function(
                "document.getElementById('preflightResult').textContent.includes('img')",
                polling=100,
                timeout=5000,
            )

            # The injected onerror handler must never have actually run.
            fired = page.evaluate("window.__xss_fired")
            assert fired is None, "hostile payload executed as script, not rendered as text"

            # The hostile markup must be visible as literal text (proving it was
            # escaped, not that it silently vanished).
            result_html = page.eval_on_selector("#preflightResult", "el => el.innerHTML")
            assert "<img" not in result_html, "raw <img tag present in innerHTML: not escaped"
            assert "&lt;img" in result_html or "&amp;lt;" in result_html

            result_text = page.eval_on_selector("#preflightResult", "el => el.textContent")
            assert hostile in result_text
        finally:
            browser.close()
