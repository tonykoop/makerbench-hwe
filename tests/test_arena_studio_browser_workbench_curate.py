"""Real-browser checks for the workbench Curate tab and export (#788 W7).

Opt-in like the other browser specs: skipped unless Playwright and Chromium
are installed; ``ARENA_STUDIO_REQUIRE_BROWSER=1`` (CI) fails instead. The
origin compile runs in the real OpenSCAD sandbox; ``MAKERBENCH_REQUIRE_SANDBOX=1``
fails loudly when it cannot start.
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

HOSTILE_TITLE = '"><img src=x onerror="window.__pwned=1">'
HOSTILE_NOTE = "`${alert(1)}`<img src=x onerror=alert(1)>"
CUBE = "w_mm = 10;\ncube(w_mm);\n"
EXPORT_NAMES = ["boxolin-workbench-r1.scad", "boxolin-workbench-r1.stl", "boxolin-workbench-r1.png", "provenance.json", "README.md"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def curate_repo(tmp_path: Path) -> Path:
    (tmp_path / "runs" / "code_cad_arena").mkdir(parents=True)
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
def studio_url(curate_repo: Path):
    app = create_studio_app(
        registry_path=curate_repo / "registry.json", repo_root=curate_repo,
        instruments_root=curate_repo / "instruments",
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


def _tab_to(page, predicate: str, *, key: str = "Tab", limit: int = 160) -> None:
    for _ in range(limit):
        if page.evaluate(predicate):
            return
        page.keyboard.press(key)
    raise AssertionError(f"never reached: {predicate}")


def _open_saved_master(page, studio_url: str) -> str:
    """Setup only (clicks): open the master and save its origin revision."""

    page.goto(f"{studio_url}/#/launch")
    page.locator("[data-open-master='boxolin']").wait_for()
    page.locator("[data-open-master='boxolin']").click()
    page.locator("[data-open-master-go='boxolin']").wait_for()
    page.locator("[data-open-master-go='boxolin']").click()
    page.wait_for_url("**/#/workbench/d-*", timeout=15_000)
    page.locator("[data-action='save']").wait_for(timeout=120_000)
    page.locator("[data-action='save']").click()
    page.locator("[data-action='confirm-save']").click()
    page.wait_for_url("**/#/workbench/d-*/r-*")
    page.wait_for_function("() => (document.querySelector('.workbench-head .panel-status')?.textContent || '').startsWith('Saved revision 1')")
    return page.url.split("#/workbench/")[1]  # "<design_id>/<rev_id>"


@needs_sandbox
def test_keyboard_only_curate_then_export_preview_confirm_and_replace(studio_url: str, curate_repo: Path, screenshot_dir: Path):
    """Plan §6 capability 4 and G14: pick, title and note append to the log
    (hostile text inert); export previews the paths, writes only under
    arena/workbench/, asks before replacing, never touches cad/. Keyboard only
    after setup, with document.activeElement asserted at every step."""
    repo = curate_repo / "instruments" / "strings" / "boxolin"
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        session = Session(browser, f"{studio_url}/#/workbench", viewport={"width": 1440, "height": 1100})
        page = session.page
        ids = _open_saved_master(page, studio_url)
        design_id, rev_id = ids.split("/")

        # End jumps to the last live tab: Curate.
        _tab_to(page, "() => document.activeElement?.id === 'tab-code'")
        page.keyboard.press("End")
        page.wait_for_function("() => document.activeElement?.id === 'tab-curate'")
        assert page.locator("#panel-curate").is_visible() and not page.locator("#panel-code").is_visible()
        page.locator(".curate-form").wait_for()
        assert "No revision is picked" in page.locator(".curate-form p[role=status]").first.inner_text()
        assert "Nothing curated yet" in page.locator(".curate-form").inner_text()
        page.screenshot(path=str(screenshot_dir / "w7-curate-light.png"), full_page=True)

        # Pick, title and note by keyboard; Save appends one row and the header updates.
        _tab_to(page, "() => document.activeElement?.dataset?.curate === 'pick'")
        page.keyboard.press("Space")
        _tab_to(page, "() => document.activeElement?.dataset?.curate === 'title'")
        page.keyboard.type(HOSTILE_TITLE)
        _tab_to(page, "() => document.activeElement?.dataset?.curate === 'note'")
        page.keyboard.type(HOSTILE_NOTE)
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'save-curation'")
        page.keyboard.press("Enter")
        page.wait_for_function("() => (document.activeElement?.textContent || '').startsWith('Curation saved')")
        assert page.locator(".curate-form .panel-status").inner_text() == "Curation saved: picked this revision, title saved, note saved."
        page.wait_for_function("(title) => document.querySelector('.workbench-head h2')?.textContent === title", arg=HOSTILE_TITLE)
        assert "This revision is the catalog pick." in page.locator(".curate-form p[role=status]").first.inner_text()
        page.locator(".curation-history li").wait_for()
        history = page.locator(".curation-history li").all_inner_texts()
        assert len(history) == 1 and "picked" in history[0] and HOSTILE_TITLE in history[0] and HOSTILE_NOTE in history[0]
        assert page.locator("main img:not(.workbench-image)").count() == 0 and page.evaluate("() => window.__pwned") is None
        assert page.locator("[data-action='save-curation']").get_attribute("aria-disabled") == "true"  # nothing left to save

        # Export: the preview lists the exact target paths; nothing is written yet.
        assert not (repo / "arena").exists()
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'export'")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.id === 'export-confirm-title'")
        assert page.locator("#export-confirm-title").inner_text() == "Export these files?"
        target = f"strings/boxolin/arena/workbench/{design_id}/{rev_id}"
        summary = page.locator(".export-confirm p[role=status]").inner_text()
        assert f"5 files to {target}/" in summary and "does not exist yet" in summary
        assert page.locator(".export-confirm .export-paths li code").all_inner_texts() == [f"{target}/{name}" for name in EXPORT_NAMES]
        assert not (repo / "arena").exists()
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'confirm-export'")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.classList.contains('export-result')")
        result = page.locator(".export-result").inner_text()
        assert f"Wrote 5 files to {target}/" in result and "Commit it in the instrument repo when you're ready." in result
        assert page.locator(".export-result .export-paths li").count() == 5
        dest = repo / "arena" / "workbench" / design_id / rev_id
        assert sorted(p.name for p in dest.iterdir()) == sorted(EXPORT_NAMES)
        assert (dest / "boxolin-workbench-r1.scad").read_text() == CUBE
        assert "NOT a measured master" in (dest / "README.md").read_text() and HOSTILE_TITLE in (dest / "README.md").read_text()
        provenance = json.loads((dest / "provenance.json").read_text())
        assert provenance["generated"] is True and provenance["curation"]["pick"] is True and provenance["curation"]["title"] == HOSTILE_TITLE
        assert (repo / "cad" / "boxolin.scad").read_text() == CUBE
        assert sorted(p.name for p in (repo / "cad").iterdir()) == ["boxolin.scad"]
        assert not (repo / ".git").exists()

        # A second export asks before replacing; Keep leaves the files alone.
        (dest / "README.md").write_text("edited by hand\n", encoding="utf-8")
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'export'", key="Shift+Tab")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.id === 'export-confirm-title'")
        assert page.locator("#export-confirm-title").inner_text() == "Replace the existing export?"
        assert "All of them already exist" in page.locator(".export-confirm p[role=status]").inner_text()
        assert page.locator(".export-confirm .export-paths .gate").count() == 5
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'cancel-export'")
        page.keyboard.press("Enter")
        page.locator(".export-confirm").wait_for(state="detached")
        page.wait_for_function("() => document.activeElement?.dataset?.action === 'export'")  # focus returns to the button
        assert (dest / "README.md").read_text() == "edited by hand\n"
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.id === 'export-confirm-title'")
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'confirm-replace'")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.classList.contains('export-result')")
        assert "replacing 5" in page.locator(".export-result").inner_text()
        assert "NOT a measured master" in (dest / "README.md").read_text()
        assert sorted(p.name for p in dest.iterdir()) == sorted(EXPORT_NAMES)

        page.emulate_media(color_scheme="dark")
        page.screenshot(path=str(screenshot_dir / "w7-curate-dark.png"), full_page=True)
        page.set_viewport_size({"width": 400, "height": 860})
        overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0
        page.screenshot(path=str(screenshot_dir / "w7-curate-400px.png"), full_page=True)
        assert session.dialogs == []
        assert session.errors == []

        # The design list shows the pick and the title.
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(f"{studio_url}/#/workbench")
        page.locator("table.workbench-list").wait_for()
        row = page.locator("table.workbench-list tbody tr").first.inner_text()
        assert "Picked" in row and HOSTILE_TITLE in row
        assert page.evaluate("() => window.__pwned") is None
        session.close()
        browser.close()


def test_curate_tab_without_a_revision_cannot_pick_or_export(studio_url: str):
    import urllib.request

    body = json.dumps({"blank": {"backend": "openscad", "instrument_id": "boxolin"}}).encode()
    req = urllib.request.Request(f"{studio_url}/api/workbench/designs", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Origin": studio_url})
    created = json.loads(urllib.request.urlopen(req).read())
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        session = Session(browser, f"{studio_url}/#/workbench/{created['design_id']}", viewport={"width": 1440, "height": 900})
        page = session.page
        page.locator("#tab-curate").wait_for()
        page.locator("#tab-curate").click()
        page.locator(".curate-form").wait_for()
        assert "Save a revision before picking one" in page.locator(".curate-form").inner_text()
        assert page.locator("[data-curate='pick']").is_disabled()
        assert page.locator("[data-action='export']").get_attribute("aria-disabled") == "true"
        page.locator("[data-action='export']").click(force=True)
        page.wait_for_timeout(300)
        assert page.locator(".export-confirm").count() == 0
        assert session.errors == []
        session.close()
        browser.close()
