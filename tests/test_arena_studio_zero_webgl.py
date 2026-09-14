"""Opt-in browser verification of the zero-WebGL 24-frame turntable (C5/#704).

Skipped by default — not run in CI, and Playwright is never a required dependency
(nothing in pyproject.toml depends on it). To run locally:

    pip install playwright
    playwright install chromium
    python3 -m pytest tests/test_arena_studio_zero_webgl.py -v

This launches a real Arena Studio server (uvicorn, loopback-only) against a fixture run
built from a real, tiny, deterministic OpenSCAD mesh (not a hand-drawn stand-in — see
amendment-01-local-cad.md), drives it with a real headless Chromium with WebGL disabled
(``--disable-gpu --disable-webgl --disable-webgl2``), and asserts the 24-frame DOM
turntable both renders and visibly rotates (auto-rotate advances the displayed frame).
"""

from __future__ import annotations

import base64
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

from makerbench import render as render_mod  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(port: int, timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
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

app = create_studio_app(default_run_dir=Path(sys.argv[1]), repo_root=Path(sys.argv[2]))
uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[3]), log_level="warning")
"""


@pytest.fixture(scope="module")
def fixture_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real, tiny, deterministic OpenSCAD cube staged as a two-model arena run.

    Real geometry per amendment-01-local-cad.md ("generate small real candidate meshes
    and turntable frames as Studio test fixtures, instead of hand-drawn stand-ins")
    rather than a fake PNG stand-in. Frames themselves are rendered lazily by the
    existing _stage_blind_assets/_stage_turntable_frames pipeline the first time the
    queue is built — this fixture only needs to point at a real, existing STL.
    """
    if not render_mod.openscad_available():
        pytest.skip("OpenSCAD not available on this machine")

    work = tmp_path_factory.mktemp("cedar_c5_zero_webgl")
    cube_source = "cube([12, 12, 12], center=true);\n"

    mesh_dir = work / "mesh"
    result = render_mod.compile_to_mesh(cube_source, str(mesh_dir), fmt="stl")
    stl_path = Path(result.mesh_path)
    assert stl_path.exists() and stl_path.stat().st_size > 0

    preview_path = work / "preview.png"
    render_mod.render_png(cube_source, str(preview_path), size=(320, 320))
    assert preview_path.exists()

    run_dir = work / "run"
    run_dir.mkdir()
    run_log = {
        "started_at": "2026-09-13T00:00:00Z",
        "config": {"model_ids": ["model-a", "model-b"], "instruments": ["ocarina"]},
        "trials": [
            {
                "trial_id": "trial-1",
                "model_id": "model-a",
                "instrument_id": "ocarina",
                "seed": 0,
                "rep": 0,
                "result": {
                    "render_ok": True,
                    "artifacts": {"png_path": str(preview_path), "stl_path": str(stl_path)},
                },
                "grade": {"compiled": True, "manifold": True},
            },
            {
                "trial_id": "trial-2",
                "model_id": "model-b",
                "instrument_id": "ocarina",
                "seed": 0,
                "rep": 0,
                "result": {
                    "render_ok": True,
                    "artifacts": {"png_path": str(preview_path), "stl_path": str(stl_path)},
                },
                "grade": {"compiled": True, "manifold": True},
            },
        ],
    }
    (run_dir / "run_log.json").write_text(json.dumps(run_log), encoding="utf-8")
    return run_dir


@pytest.fixture(scope="module")
def studio_server(fixture_run: Path):
    port = _free_port()
    bootstrap_path = fixture_run.parent / "_bootstrap.py"
    bootstrap_path.write_text(BOOTSTRAP, encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(bootstrap_path), str(fixture_run), str(fixture_run.parent), str(port)],
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


def test_zero_webgl_turntable_renders_and_rotates(
    studio_server: str, fixture_run: Path, tmp_path: Path
):
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-gpu", "--disable-webgl", "--disable-webgl2"],
            )
        except Exception as exc:
            pytest.skip(f"Chromium not available for Playwright: {exc}")

        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})

            # Registered before navigation so nothing from initial load is missed.
            console_errors: list[str] = []
            page.on(
                "console",
                lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
            )

            page.goto(studio_server, wait_until="networkidle")

            # Confirm the page's own feature-detection agrees WebGL is unavailable, and
            # that it correctly leaves the WebGL toggle disabled rather than presenting a
            # mode that would blank-canvas.
            webgl_supported = page.evaluate(
                "() => { try { const c = document.createElement('canvas');"
                " return !!(c.getContext('webgl2')); } catch (e) { return false; } }"
            )
            assert webgl_supported is False, "expected WebGL2 to be genuinely disabled"

            # Note on polling: Playwright's default wait_for_function polling strategy is
            # requestAnimationFrame, which never fires in headless Chromium launched with
            # --disable-gpu (no compositor to schedule frames against) — the underlying
            # DOM condition becomes true almost immediately but the wait never re-checks
            # it and hangs to the timeout. polling=100 switches to setInterval-based
            # polling instead, which is unaffected. Same reasoning applies to Playwright's
            # click() actionability "stability" check below, so that's dispatched as a
            # raw DOM click instead of using .click().

            # Other runs may exist on this machine (discover_runs() isn't scoped to just
            # the fixture) — select the fixture run explicitly rather than relying on it
            # sorting first, then switch tabs.
            page.wait_for_function(
                "(name) => Array.from(document.querySelectorAll('#runSelect option'))"
                ".some(o => o.value === name)",
                arg=fixture_run.name,
                timeout=10000,
                polling=100,
            )
            page.select_option("#runSelect", value=fixture_run.name)
            page.eval_on_selector("#runSelect", "el => el.dispatchEvent(new Event('change'))")

            page.eval_on_selector('[data-tab="arena"]', "el => el.click()")
            page.wait_for_function(
                "() => document.getElementById('imgLeft') && document.getElementById('imgLeft').src"
                " && !document.getElementById('imgLeft').src.endsWith('/')",
                timeout=20000,
                polling=100,
            )

            btn_webgl = page.locator("#btnWebglMode")
            assert btn_webgl.is_disabled(), "WebGL toggle must stay disabled with no WebGL2"
            btn_turntable = page.locator("#btnTurntableMode")
            assert "active" in (btn_turntable.get_attribute("class") or "")

            left_src_before = page.eval_on_selector("#imgLeft", "el => el.src")
            assert left_src_before, "left turntable must have a real frame src, not blank"
            assert "/vote_pages/blind/" in left_src_before

            # Auto-rotate advances every 180ms — give it enough time for several ticks
            # and confirm the frame actually changed (rotation is real, not a static img).
            page.wait_for_function(
                "(prev) => document.getElementById('imgLeft').src !== prev",
                arg=left_src_before,
                timeout=5000,
                polling=100,
            )
            left_src_after = page.eval_on_selector("#imgLeft", "el => el.src")
            assert left_src_after != left_src_before

            # No console errors, no broken canvases (issue #698's stated bar) — listener
            # was armed before navigation, so this covers the whole session so far.
            assert not console_errors, f"unexpected console errors: {console_errors}"

            # Screenshot is evidence, not a correctness assertion — every assertion above
            # this point already proves the actual acceptance bar (WebGL genuinely
            # disabled, turntable renders a real frame, it visibly rotates, zero console
            # errors). Locator.screenshot() and page.screenshot() both depend on a
            # composited frame from Chromium's screenshot pipeline (CDP
            # Page.captureScreenshot), which hung indefinitely in this sandbox even under
            # Xvfb and without --disable-gpu — an environment/rendering-library gap, not
            # something this test can control. Best-effort: try once with a short
            # timeout, warn and move on if it's unavailable here rather than failing the
            # whole (otherwise-passing) verification on it.
            screenshot_path = tmp_path / "zero_webgl_turntable.png"
            try:
                vote_stage_box = page.eval_on_selector(
                    "#voteStage",
                    "el => { const r = el.getBoundingClientRect();"
                    " return {x: r.x, y: r.y, width: r.width, height: r.height}; }",
                )
                page.screenshot(path=str(screenshot_path), clip=vote_stage_box, timeout=8000)
                assert screenshot_path.stat().st_size > 0
                print(f"\nzero-WebGL turntable screenshot: {screenshot_path}")
                print(
                    "screenshot (data URI, first 100 chars): "
                    + base64.b64encode(screenshot_path.read_bytes())[:100].decode()
                    + "..."
                )
            except Exception as exc:
                print(
                    f"\nSCREENSHOT UNAVAILABLE in this environment ({exc!r}) — the "
                    "turntable render/rotate/no-console-error assertions above already "
                    "passed, which is the actual acceptance bar. Not failing the test on "
                    "evidence capture alone."
                )
        finally:
            browser.close()
