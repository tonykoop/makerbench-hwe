"""The workbench screenshot set (#788 W9): every workbench state, in both
themes, from one real-Chromium session. The states are reached the way a
person reaches them (the UI, not planted files), so each screenshot is also
a smoke test of that state. Zero-WebGL, like the CI runner.

Opt-in like the other browser specs; ``ARENA_STUDIO_REQUIRE_BROWSER=1`` (CI)
fails instead of skipping. Compiles run in the real OpenSCAD sandbox;
``MAKERBENCH_REQUIRE_SANDBOX=1`` fails loudly when it cannot start.
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

from makerbench import scad_sandbox  # noqa: E402
from makerbench.arena_studio import create_studio_app  # noqa: E402

REQUIRE_SANDBOX = os.environ.get("MAKERBENCH_REQUIRE_SANDBOX") == "1"
_SANDBOX = scad_sandbox.sandbox_available()
if REQUIRE_SANDBOX and not _SANDBOX:  # pragma: no cover - CI guard
    pytest.fail("MAKERBENCH_REQUIRE_SANDBOX=1 but the OpenSCAD sandbox cannot start", pytrace=False)
needs_sandbox = pytest.mark.skipif(not _SANDBOX, reason="OpenSCAD sandbox unavailable here")

MASTER = """/* [Body] */
// Width of the box
w_mm = 10; // [5:20]
h_mm = 12;
hollow = false;
cube([w_mm, h_mm, 6]);
"""
THEMES = ("light", "dark")
# Every state the guide names, in the order a first session meets them.
STATES = [
    "list-empty", "design-origin", "code-result", "parameters", "revise", "revise-confirm",
    "curate", "export-confirm", "export-done", "compare", "compile-failed", "list", "phone",
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def states_repo(tmp_path: Path) -> Path:
    (tmp_path / "runs" / "code_cad_arena").mkdir(parents=True)
    (tmp_path / "registry.json").write_text(json.dumps({
        "schema": "makerbench-code-cad-arena-registry-v1",
        "instruments": [{"id": "boxolin", "display_name": "Boxolin", "family": "strings", "task_kind": "single_part",
                         "envelope_mm": [100, 100, 100], "min_bodies": 1, "repo_path": "strings/boxolin"}],
    }), encoding="utf-8")
    cad = tmp_path / "instruments" / "strings" / "boxolin" / "cad"
    cad.mkdir(parents=True)
    (cad / "boxolin.scad").write_text(MASTER, encoding="utf-8")
    return tmp_path


@pytest.fixture
def studio_url(states_repo: Path):
    app = create_studio_app(
        registry_path=states_repo / "registry.json", repo_root=states_repo,
        instruments_root=states_repo / "instruments",
    )
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


def _launch(playwright):
    args = ["--disable-3d-apis", "--disable-webgl", "--disable-webgl2", *os.environ.get("ARENA_STUDIO_BROWSER_ARGS", "").split()]
    try:
        return playwright.chromium.launch(args=args)
    except Exception as error:  # executable missing
        if REQUIRE_BROWSER:
            raise
        pytest.skip(f"chromium is not available: {error}")


@needs_sandbox
def test_every_workbench_state_screenshots_in_both_themes(studio_url: str, states_repo: Path, screenshot_dir: Path):
    taken: dict[str, set[str]] = {theme: set() for theme in THEMES}
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        page = context.new_page()
        errors: list[str] = []
        dialogs: list[str] = []
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))

        def shot(state: str) -> None:
            assert state in STATES, state
            for theme in THEMES:
                page.emulate_media(color_scheme=theme)
                page.wait_for_timeout(50)
                page.screenshot(path=str(screenshot_dir / f"w9-workbench-{state}-{theme}.png"), full_page=True)
                taken[theme].add(state)
            page.emulate_media(color_scheme="light")

        # The empty list.
        page.goto(f"{studio_url}/#/workbench")
        page.locator(".state-empty").wait_for()
        shot("list-empty")

        # A master opened from Launch; the origin draft compiles on its own.
        page.goto(f"{studio_url}/#/launch")
        page.locator("[data-open-master='boxolin']").click()
        page.locator("[data-open-master-go='boxolin']").click()
        page.wait_for_url("**/#/workbench/d-*", timeout=15_000)
        page.locator("[data-action='save']").wait_for(timeout=120_000)
        shot("design-origin")
        page.locator("[data-action='save']").click()
        page.locator("[data-action='confirm-save']").click()
        page.wait_for_url("**/#/workbench/d-*/r-*")
        page.wait_for_function("() => (document.querySelector('.workbench-head .panel-status')?.textContent || '').startsWith('Saved revision 1')")
        design_id, rev1 = page.url.split("#/workbench/")[1].split("/")

        # An edit, compiled: the result heading, preview and checks.
        editor = page.locator(".code-editor-text")
        editor.focus()
        page.keyboard.press("Control+End")
        page.keyboard.type("\ntranslate([20, 0, 0]) cube(4);\n")
        page.keyboard.press("Control+Enter")
        page.locator("[data-action='cancel']").wait_for(timeout=10_000)
        page.locator("[data-action='save']").wait_for(timeout=120_000)
        shot("code-result")
        page.locator("[data-action='save']").click()
        page.locator("[data-action='confirm-save']").click()
        page.wait_for_function("() => (document.querySelector('.workbench-head .panel-status')?.textContent || '').startsWith('Saved revision 2 from 1')")

        # Parameters, with one change pending.
        page.locator("#tab-parameters").click()
        page.locator("[data-param='w_mm']").wait_for()
        page.locator("[data-param='w_mm']").fill("15")
        page.wait_for_function("() => document.querySelector(\"[data-param-row='w_mm']\")?.dataset?.changed === 'true'")
        shot("parameters")

        # Revise: the picker, then the in-page confirm for the stub.
        page.locator("#tab-revise").click()
        page.locator("select[name=entrant]").wait_for()
        page.locator("textarea[name=feedback]").fill("make it hollow")
        shot("revise")
        page.locator("[data-action='revise-start']").click()
        page.wait_for_function("() => document.activeElement?.id === 'revise-confirm-title'")
        shot("revise-confirm")
        page.locator("[data-action='revise-cancel']").click()

        # Curate, then the export confirm and the export result.
        page.locator("#tab-curate").click()
        page.locator("[data-curate='pick']").wait_for()
        page.locator("[data-curate='pick']").check()
        page.locator("[data-curate='title']").fill("Boxolin, wider")
        page.locator("[data-action='save-curation']").click()
        page.wait_for_function("() => (document.querySelector('.curate-form .panel-status')?.textContent || '').startsWith('Curation saved')")
        shot("curate")
        page.locator("[data-action='export']").click()
        page.wait_for_function("() => document.activeElement?.id === 'export-confirm-title'")
        shot("export-confirm")
        page.locator("[data-action='confirm-export']").click()
        page.wait_for_function("() => document.activeElement?.classList.contains('export-result')")
        shot("export-done")

        # Compare revision 1 against 2.
        page.locator(f"[data-compare='{rev1}']").click()
        page.wait_for_function("() => document.activeElement?.id === 'compare-title'")
        page.locator("pre.diff").wait_for()
        shot("compare")
        page.get_by_role("button", name="Close").click()

        # A failed compile with its line jump.
        page.locator("#tab-code").click()
        editor.fill("w_mm = 10;\ncube(w_mm;\n")
        page.locator("[data-action='compile']").click()
        page.locator(".job-status [role=alert]").wait_for(timeout=120_000)
        shot("compile-failed")

        # The list with a picked, titled design, then the phone-width design view.
        page.goto(f"{studio_url}/#/workbench")
        page.locator("table.workbench-list").wait_for()
        assert "Picked" in page.locator("table.workbench-list").inner_text()
        shot("list")
        page.set_viewport_size({"width": 400, "height": 860})
        page.goto(f"{studio_url}/#/workbench/{design_id}")
        page.locator(".workbench-design").wait_for()
        page.wait_for_load_state("networkidle")
        overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0
        shot("phone")

        assert dialogs == []
        assert errors == []
        for theme in THEMES:
            assert taken[theme] == set(STATES), (theme, set(STATES) - taken[theme])
        assert (states_repo / "instruments" / "strings" / "boxolin" / "cad" / "boxolin.scad").read_text() == MASTER
        context.close()
        browser.close()
