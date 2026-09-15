"""Real-browser checks for the design workbench screen (#788 W4).

Same opt-in as the other browser specs: skipped unless Playwright's Chromium is
installed; ``ARENA_STUDIO_REQUIRE_BROWSER=1`` (the studio-browser workflow)
turns that into a failure. The compile flow runs the real detached
``arena workbench-job`` inside the real Bubblewrap sandbox, so those tests also
need it (``MAKERBENCH_REQUIRE_SANDBOX=1`` makes a missing sandbox fail).
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

WEBGL_MODES = {"webgl": [], "zero-webgl": ["--disable-3d-apis", "--disable-webgl", "--disable-webgl2"]}
HOSTILE_TITLE = '"><img src=x onerror="window.__pwned=1">'
HOSTILE_NOTE = '`${alert(1)}`<img src=x onerror=alert(1)>'
CUBE = "w_mm = 10;\ncube(w_mm);\n"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def workbench_repo(tmp_path: Path) -> Path:
    run_dir = tmp_path / "runs" / "code_cad_arena" / "round-fixture"
    gen = run_dir / "gen" / "t-1"
    gen.mkdir(parents=True)
    (gen / "candidate.scad").write_text("size = 30;\ncube(size);\n", encoding="utf-8")
    run_log = {
        "started_at": "2026-09-14T09:30:00Z",
        "config": {"model_ids": ["model-a"], "instruments": ["boxolin"], "backend": "openscad"},
        "trials": [
            {"trial_id": "t-1", "model_id": "model-a", "instrument_id": "boxolin", "seed": 0, "rep": 0,
             "result": {"render_ok": True, "gen": {"scad_path": str(gen / "candidate.scad")}}},
        ],
    }
    (run_dir / "run_log.json").write_text(json.dumps(run_log), encoding="utf-8")
    (tmp_path / "registry.json").write_text(json.dumps({
        "schema": "makerbench-code-cad-arena-registry-v1",
        "instruments": [{"id": "boxolin", "display_name": "Boxolin", "family": "strings", "task_kind": "single_part",
                         "envelope_mm": [100, 100, 100], "min_bodies": 1, "repo_path": "strings/boxolin"}],
    }), encoding="utf-8")
    cad = tmp_path / "instruments" / "strings" / "boxolin" / "cad"
    cad.mkdir(parents=True)
    (cad / "boxolin.scad").write_text(CUBE, encoding="utf-8")
    return tmp_path


@pytest.fixture
def studio_url(workbench_repo: Path):
    app = create_studio_app(
        registry_path=workbench_repo / "registry.json", repo_root=workbench_repo,
        instruments_root=workbench_repo / "instruments",
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


def _extra_browser_args() -> list[str]:
    return os.environ.get("ARENA_STUDIO_BROWSER_ARGS", "").split()


def _launch(playwright, mode: str):
    try:
        return playwright.chromium.launch(args=[*WEBGL_MODES[mode], *_extra_browser_args()])
    except Exception as error:  # executable missing
        if REQUIRE_BROWSER:
            raise
        pytest.skip(f"chromium is not available: {error}")


class Session:
    def __init__(self, browser, url: str, **context_args):
        self.context = browser.new_context(**context_args)
        self.page = self.context.new_page()
        self.errors: list[str] = []
        self.dialogs: list[str] = []
        self.page.on("console", lambda message: self.errors.append(message.text) if message.type == "error" else None)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("dialog", self._on_dialog)
        self.page.goto(url)

    def _on_dialog(self, dialog):
        self.dialogs.append(dialog.message)
        dialog.dismiss()

    def close(self):
        self.context.close()


def _tab_to(page, predicate: str, *, key: str = "Tab", limit: int = 120) -> None:
    for _ in range(limit):
        if page.evaluate(predicate):
            return
        page.keyboard.press(key)
    raise AssertionError(f"never reached: {predicate}")


def _active(page) -> str:
    return page.evaluate("() => (document.activeElement?.id || document.activeElement?.dataset?.action || document.activeElement?.tagName || '').toString()")


@pytest.mark.parametrize("mode", sorted(WEBGL_MODES))
@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_workbench_list_renders_the_empty_state(studio_url: str, screenshot_dir: Path, mode: str, color_scheme: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright, mode)
        session = Session(browser, f"{studio_url}/#/workbench", viewport={"width": 1440, "height": 900}, color_scheme=color_scheme)
        page = session.page
        if mode == "zero-webgl":
            assert page.evaluate("() => document.createElement('canvas').getContext('webgl2')") is None
        page.locator(".state-empty").wait_for()
        assert "No designs yet" in page.locator(".state-empty").inner_text()
        assert page.locator("nav.rail a[aria-current=page]").inner_text().strip() == "Workbench"
        assert page.evaluate("() => document.title") == "Workbench: Arena Studio"
        page.screenshot(path=str(screenshot_dir / f"w4-workbench-empty-{mode}-{color_scheme}.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()


def test_blind_screens_never_link_the_workbench(studio_url: str):
    """G12 across the whole rendered page: the rail hides the Workbench entry on
    blind routes (Sol, #815), and nothing in the page links to it. A non-blind
    screen still lists it in the rail."""
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        for screen in ("vote", "morning"):
            session = Session(browser, f"{studio_url}/#/{screen}", viewport={"width": 1440, "height": 900})
            session.page.locator("main h1").wait_for()
            session.page.locator("nav.rail a[aria-current=page]").wait_for()
            assert session.page.locator("a[href*='workbench']").count() == 0, screen
            assert "Workbench" not in session.page.locator("nav.rail").inner_text()
            # the fixture run has no votes yet; the only console error is that 404
            assert all("404" in error for error in session.errors), session.errors
            session.close()
        session = Session(browser, f"{studio_url}/#/runs", viewport={"width": 1440, "height": 900})
        session.page.locator("nav.rail a[aria-current=page]").wait_for()
        assert session.page.locator("nav.rail a[href*='workbench']").count() == 1
        session.close()
        browser.close()


@needs_sandbox
def test_running_compile_cannot_start_a_second_draft(studio_url: str):
    """Sol (#815): Ctrl+Enter while a compile runs must not POST a second
    draft. The editor keeps focus and is read-only, so the shortcut is
    swallowed there and the compile callback refuses while running."""
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/launch", viewport={"width": 1440, "height": 1000})
        page = session.page
        page.locator("[data-open-master='boxolin']").click()
        page.locator("[data-open-master-go='boxolin']").wait_for()
        page.locator("[data-open-master-go='boxolin']").click()
        page.wait_for_url("**/#/workbench/d-*", timeout=15_000)
        page.locator("[data-action='save']").wait_for(timeout=120_000)
        page.locator("[data-action='save']").click()
        page.locator("[data-action='confirm-save']").click()
        page.wait_for_url("**/#/workbench/d-*/r-*")

        posts: list[str] = []
        page.on("request", lambda request: posts.append(request.url) if request.method == "POST" and request.url.endswith("/drafts") else None)
        editor = page.locator(".code-editor-text")
        editor.fill("union() { for (i = [0:400]) translate([i * 3, 0, 0]) sphere(10, $fn = 120); }\n")
        editor.focus()
        page.keyboard.press("Control+Enter")
        page.locator("[data-action='cancel']").wait_for(timeout=10_000)
        assert page.evaluate("() => document.activeElement?.classList.contains('code-editor-text')")
        for _ in range(3):
            page.keyboard.press("Control+Enter")
        page.locator("[data-action='compile']").click(force=True)  # aria-disabled while running; the handler must refuse
        page.wait_for_timeout(500)
        assert posts == [posts[0]], posts
        page.locator("[data-action='cancel']").click()
        page.wait_for_function("() => (document.querySelector('.job-status p[role=status]')?.textContent || '').startsWith('Cancelled')", timeout=15_000)
        assert len(posts) == 1
        assert session.dialogs == [] and session.errors == []
        session.close()
        browser.close()


@needs_sandbox
def test_keyboard_only_open_edit_compile_save_compare(studio_url: str, workbench_repo: Path, screenshot_dir: Path):
    """Plan §7: open a trial, edit, compile, save, compare with the keyboard only,
    asserting where focus is at every step; zero-WebGL, so the preview is the image."""
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/runs/round-fixture", viewport={"width": 1440, "height": 1000})
        page = session.page
        page.locator("[data-open-trial='t-1']").wait_for()
        assert "Vote first if you want to stay blind" in page.locator(".open-in-workbench").inner_text()
        _tab_to(page, "() => document.activeElement?.dataset?.openTrial === 't-1'")
        page.keyboard.press("Enter")
        page.wait_for_url("**/#/workbench/d-*", timeout=15_000)
        page.locator(".workbench-design").wait_for()

        # The origin draft compiles on its own; wait for it and save it by keyboard.
        page.locator("[data-action='save']").wait_for(timeout=120_000)
        assert page.locator(".job-status p[role=status]").inner_text().startswith("Compiled")
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'save'")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.id === 'save-title'")
        _tab_to(page, "() => document.activeElement?.name === 'note'")
        page.keyboard.type("origin")
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'confirm-save'")
        page.keyboard.press("Enter")
        page.wait_for_function("() => (document.querySelector('.workbench-head .panel-status')?.textContent || '').startsWith('Saved revision 1')")
        assert page.evaluate("() => document.activeElement?.classList.contains('panel-status')")
        page.wait_for_url("**/#/workbench/d-*/r-*")

        # Edit the source, compile with Ctrl+Enter, watch the log, save as revision 2.
        _tab_to(page, "() => document.activeElement?.classList.contains('code-editor-text')")
        page.keyboard.press("Control+End")
        page.keyboard.type("\ntranslate([20, 0, 0]) cube(5);\n")
        page.keyboard.press("Control+Enter")
        page.locator("[data-action='cancel']").wait_for(timeout=10_000)
        assert page.evaluate("() => document.activeElement?.classList.contains('code-editor-text')"), "focus left the editor during the compile"
        page.wait_for_function("() => (document.querySelector('pre.log')?.textContent || '').includes('compiling in the sandbox')", timeout=20_000)
        page.locator("[data-action='save']").wait_for(timeout=120_000)
        page.wait_for_function("() => document.activeElement?.closest('.workbench-result') !== null", timeout=5_000)
        assert page.locator(".workbench-result h3").inner_text().startswith("Result of the succeeded compile")
        assert page.locator(".workbench-result img.workbench-image").count() == 1
        assert "Pass rate" in page.locator(".workbench-result").inner_text()
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'save'")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.id === 'save-title'")
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'confirm-save'")
        page.keyboard.press("Enter")
        page.wait_for_function("() => (document.querySelector('.workbench-head .panel-status')?.textContent || '').startsWith('Saved revision 2 from 1')")
        page.locator("table.revisions tbody tr").nth(1).wait_for()

        # Compare revision 1 against the current revision 2, by keyboard.
        _tab_to(page, "() => document.activeElement?.dataset?.compare !== undefined")
        compare_id = page.evaluate("() => document.activeElement.dataset.compare")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.id === 'compare-title'")
        page.locator("pre.diff").wait_for()
        diff = page.locator("pre.diff").inner_text()
        assert "translate([20, 0, 0]) cube(5);" in diff
        assert "added: " in page.locator("pre.diff .visually-hidden").first.inner_text() or page.locator("pre.diff .diff-added").count() >= 1
        assert page.locator(".compare-panel img.workbench-image").count() == 2
        _tab_to(page, "() => (document.activeElement?.textContent || '').trim() === 'Close'")
        page.keyboard.press("Enter")
        page.locator(".compare-panel").wait_for(state="detached")
        assert page.evaluate("() => document.activeElement?.dataset?.compare") == compare_id
        page.screenshot(path=str(screenshot_dir / "w4-workbench-keyboard.png"), full_page=True)
        assert session.dialogs == []
        assert session.errors == []
        # nothing was written into the arena run or the instrument repo
        assert sorted(p.name for p in (workbench_repo / "runs" / "code_cad_arena" / "round-fixture").iterdir()) == ["gen", "run_log.json"]
        assert (workbench_repo / "instruments" / "strings" / "boxolin" / "cad" / "boxolin.scad").read_text() == CUBE
        session.close()
        browser.close()


@needs_sandbox
def test_failed_compile_shows_the_error_with_line_jump_and_cancel_stops_a_job(studio_url: str, screenshot_dir: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/launch", viewport={"width": 1440, "height": 1000})
        page = session.page
        page.locator("[data-open-master='boxolin']").wait_for()
        page.locator("[data-open-master='boxolin']").click()
        page.locator("[data-open-master-go='boxolin']").wait_for()
        assert page.locator("select[name='master-boxolin'] option").count() == 1
        page.locator("[data-open-master-go='boxolin']").click()
        page.wait_for_url("**/#/workbench/d-*", timeout=15_000)
        page.locator("[data-action='save']").wait_for(timeout=120_000)
        page.locator("[data-action='save']").click()
        page.locator("[data-action='confirm-save']").click()
        page.wait_for_url("**/#/workbench/d-*/r-*")

        editor = page.locator(".code-editor-text")
        editor.fill("w_mm = 10;\ncube(w_mm;\n")
        page.locator("[data-action='compile']").click()
        page.locator(".job-status [role=alert]").wait_for(timeout=120_000)
        alert = page.locator(".job-status [role=alert]").inner_text()
        assert "OpenSCAD exited" in alert or "syntax error" in alert.lower()
        assert page.locator("[data-line]").count() >= 1
        page.locator("[data-line]").first.click()
        assert page.evaluate("() => document.activeElement?.classList.contains('code-editor-text')")
        assert page.locator("[data-action='save']").count() == 0  # a failed draft cannot be saved
        page.screenshot(path=str(screenshot_dir / "w4-workbench-failed.png"), full_page=True)

        # A slow compile can be cancelled; the log says so and Save never appears.
        editor.fill("union() { for (i = [0:400]) translate([i * 3, 0, 0]) sphere(10, $fn = 120); }\n")
        page.locator("[data-action='compile']").click()
        page.locator("[data-action='cancel']").wait_for(timeout=10_000)
        page.locator("[data-action='cancel']").click()
        page.wait_for_function("() => (document.querySelector('.job-status p[role=status]')?.textContent || '').startsWith('Cancelled')", timeout=15_000)
        assert page.locator("[data-action='save']").count() == 0
        assert session.dialogs == [] and session.errors == []
        session.close()
        browser.close()


@needs_sandbox
def test_hostile_title_and_note_render_as_inert_text(studio_url: str, workbench_repo: Path):
    import urllib.request

    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        # Create a design through the API with a hostile title, then annotate it.
        body = json.dumps({"blank": {"backend": "openscad", "instrument_id": "boxolin"}, "title": HOSTILE_TITLE}).encode()
        req = urllib.request.Request(f"{studio_url}/api/workbench/designs", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Origin": studio_url})
        created = json.loads(urllib.request.urlopen(req).read())
        design_id = created["design_id"]
        session = Session(browser, f"{studio_url}/#/workbench", viewport={"width": 1440, "height": 900})
        page = session.page
        page.locator("table.workbench-list").wait_for()
        assert HOSTILE_TITLE in page.locator("table.workbench-list").inner_text()
        page.goto(f"{studio_url}/#/workbench/{design_id}")
        page.locator(".workbench-head h2").wait_for()
        assert page.locator(".workbench-head h2").inner_text() == HOSTILE_TITLE
        page.locator("[data-action='save']").wait_for(timeout=120_000)
        page.locator("[data-action='save']").click()
        page.locator("input[name=note]").fill(HOSTILE_NOTE)
        page.locator("[data-action='confirm-save']").click()
        page.wait_for_url("**/#/workbench/d-*/r-*")
        page.wait_for_load_state("networkidle")
        assert session.dialogs == []
        assert page.evaluate("() => window.__pwned") is None
        assert page.locator("main img:not(.workbench-image)").count() == 0
        assert session.errors == []
        session.close()
        browser.close()


def test_workbench_at_phone_width_has_no_horizontal_scroll(studio_url: str, screenshot_dir: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/workbench", viewport={"width": 400, "height": 860})
        page = session.page
        page.locator(".state-empty").wait_for()
        overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0
        page.screenshot(path=str(screenshot_dir / "w4-workbench-400px.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()


@needs_sandbox
def test_design_view_at_phone_width_stacks_without_horizontal_scroll(studio_url: str, screenshot_dir: Path):
    import urllib.request

    body = json.dumps({"master": {"instrument_id": "boxolin", "file": "boxolin.scad"}}).encode()
    req = urllib.request.Request(f"{studio_url}/api/workbench/designs", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Origin": studio_url})
    created = json.loads(urllib.request.urlopen(req).read())
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        for color_scheme in ("light", "dark"):
            session = Session(browser, f"{studio_url}/#/workbench/{created['design_id']}", viewport={"width": 400, "height": 860}, color_scheme=color_scheme)
            page = session.page
            page.locator("[data-action='save']").wait_for(timeout=120_000)
            overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            assert overflow <= 0
            page.screenshot(path=str(screenshot_dir / f"w4-workbench-design-400px-{color_scheme}.png"), full_page=True)
            assert session.errors == []
            session.close()
        browser.close()
