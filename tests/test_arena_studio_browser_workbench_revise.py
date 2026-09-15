"""Real-browser checks for the workbench Revise tab with the stub entrant
(#788 W6, non-live). No model is ever called: the stub answers in-process.

Opt-in like the other browser specs: skipped unless Playwright and Chromium
are installed; ``ARENA_STUDIO_REQUIRE_BROWSER=1`` (CI) fails instead. The
compiles run in the real OpenSCAD sandbox; ``MAKERBENCH_REQUIRE_SANDBOX=1``
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

CUBE = "w_mm = 10;\ncube(w_mm);\n"
HOSTILE_FEEDBACK = '"><img src=x onerror="window.__pwned=1"> make it hollow'


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def revise_repo(tmp_path: Path) -> Path:
    (tmp_path / "runs" / "code_cad_arena").mkdir(parents=True)
    (tmp_path / "registry.json").write_text(json.dumps({
        "schema": "makerbench-code-cad-arena-registry-v1",
        "instruments": [{"id": "boxolin", "display_name": "Boxolin", "family": "strings", "task_kind": "single_part",
                         "envelope_mm": [100, 100, 100], "min_bodies": 1, "repo_path": "strings/boxolin"}],
    }), encoding="utf-8")
    repo = tmp_path / "instruments" / "strings" / "boxolin"
    (repo / "cad").mkdir(parents=True)
    (repo / "cad" / "boxolin.scad").write_text(CUBE, encoding="utf-8")
    (repo / "private").mkdir()
    (repo / "private" / "oracle.json").write_text('{"secret": true}\n', encoding="utf-8")
    return tmp_path


@pytest.fixture
def studio_url(revise_repo: Path):
    # No --allow-live: live entrants must be disabled with the reason; the stub works.
    app = create_studio_app(
        registry_path=revise_repo / "registry.json", repo_root=revise_repo,
        instruments_root=revise_repo / "instruments",
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
    return page.url.split("#/workbench/")[1]


@needs_sandbox
def test_keyboard_only_stub_revise_confirm_log_compare_and_save(studio_url: str, revise_repo: Path, screenshot_dir: Path):
    """Plan §6 capability 3 with the stub only: the picker is honest about
    live entrants (off without --allow-live), the confirm says nothing is
    called, the job streams its log with Cancel, Compare opens against the
    parent on success, and Save records the model provenance."""
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        session = Session(browser, f"{studio_url}/#/workbench", viewport={"width": 1440, "height": 1100})
        page = session.page
        ids = _open_saved_master(page, studio_url)
        design_id, rev_id = ids.split("/")
        draft_root = revise_repo / "runs" / "workbench" / design_id / "drafts"

        # Reach the Revise tab by keyboard (two ArrowRights from Code).
        _tab_to(page, "() => document.activeElement?.id === 'tab-code'")
        page.keyboard.press("ArrowRight")
        page.keyboard.press("ArrowRight")
        page.wait_for_function("() => document.activeElement?.id === 'tab-revise'")
        page.locator("select[name=entrant]").wait_for()
        options = page.locator("select[name=entrant] option")
        assert options.count() == 4
        texts = options.all_inner_texts()
        assert any("Stub (no model call)" in t for t in texts)
        assert "--allow-live" in page.locator("#panel-revise").inner_text()
        disabled = page.evaluate("() => [...document.querySelectorAll('select[name=entrant] option')].map((o) => o.hasAttribute('disabled'))")
        for index, text in enumerate(texts):
            live = "Stub" not in text
            assert disabled[index] == live, text  # live entrants are off, the stub is on
            if live:
                assert "--allow-live" in text or "not installed" in text or "cannot start" in text, text
        page.screenshot(path=str(screenshot_dir / "w6-revise-light.png"), full_page=True)

        # Start without feedback is refused in the page; the refusal takes focus.
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'revise-start'")
        page.keyboard.press("Enter")
        page.wait_for_function("() => (document.activeElement?.textContent || '').includes('Say what to change')")
        assert page.locator(".revise-confirm").count() == 0 and not list(draft_root.glob("j-*"))[1:]

        # Feedback (hostile text stays text), then Start opens the in-page confirm.
        _tab_to(page, "() => document.activeElement?.dataset?.revise === 'feedback'", key="Shift+Tab")
        page.keyboard.type(HOSTILE_FEEDBACK)
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'revise-start'")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.id === 'revise-confirm-title'")
        assert page.locator("#revise-confirm-title").inner_text() == "Run the stub?"
        assert "no model is called and nothing is spent" in page.locator(".revise-confirm").inner_text()
        # Cancel returns focus to Start and creates nothing.
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'revise-cancel'")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.dataset?.action === 'revise-start'")
        assert len(list(draft_root.glob("j-*"))) == 1  # only the origin draft
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.id === 'revise-confirm-title'")
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'revise-confirm'", key="Shift+Tab")
        page.keyboard.press("Enter")

        # The job runs like a compile: Cancel is offered, the log names the staging and the stub.
        page.locator("[data-action='cancel']").wait_for(timeout=10_000)
        assert page.locator("textarea[name=feedback]").get_attribute("readonly") is not None
        page.wait_for_function("() => (document.querySelector('pre.log')?.textContent || '').includes('revise: staged studio workspace')", timeout=30_000)
        page.locator("[data-action='save']").wait_for(timeout=120_000)
        log = page.locator("pre.log").inner_text()
        assert "calling stub (stub)" in log and "confinement not_applicable (from launch evidence)" in log and "compiling in the sandbox" in log
        assert "Revised by stub (stub); no model ran (stub)." in page.locator(".job-status").inner_text()
        assert page.evaluate("() => window.__pwned") is None and page.locator("main img:not(.workbench-image)").count() == 0

        # Compare opened against the parent: unsaved draft on the right, a real diff.
        page.wait_for_function("() => document.activeElement?.id === 'compare-title'", timeout=10_000)
        assert "Unsaved draft" in page.locator(".compare-panel").inner_text()
        assert page.locator(".compare-panel [data-provenance=model]").inner_text() == "Revised by stub (stub); no model ran (stub)."
        assert page.locator("pre.diff .diff-added").count() >= 1
        assert page.locator(".compare-panel img.workbench-image").count() == 2
        page.screenshot(path=str(screenshot_dir / "w6-revise-compare.png"), full_page=True)
        _tab_to(page, "() => (document.activeElement?.textContent || '').trim() === 'Close'")
        page.keyboard.press("Enter")
        page.locator(".compare-panel").wait_for(state="detached")
        page.wait_for_function("() => document.activeElement?.dataset?.action === 'save'")

        # Save records the model provenance on revision 2.
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.id === 'save-title'")
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'confirm-save'")
        page.keyboard.press("Enter")
        page.wait_for_function("() => (document.querySelector('.workbench-head .panel-status')?.textContent || '').startsWith('Saved revision 2 from 1')")
        page.locator("table.revisions tbody tr").nth(1).wait_for()
        assert "revised by a model" in page.locator("table.revisions tbody tr").nth(1).inner_text()

        # On disk: the staged workspace holds the repo copy but never private/; the repo itself is untouched.
        revise_dirs = [d for d in draft_root.glob("j-*") if (d / "revise.json").exists()]
        assert len(revise_dirs) == 1
        staged = sorted(p.relative_to(revise_dirs[0] / "workspace").as_posix() for p in (revise_dirs[0] / "workspace").rglob("*") if p.is_file())
        assert "cad/boxolin.scad" in staged and ".staging_manifest.json" in staged and "source.scad" in staged
        assert not any(part == "private" for s in staged for part in s.split("/")), staged
        evidence = json.loads((revise_dirs[0] / "revise.evidence.json").read_text())
        assert evidence["confinement"] == "not_applicable" and evidence["provider"] == "stub"
        assert (revise_repo / "instruments" / "strings" / "boxolin" / "cad" / "boxolin.scad").read_text() == CUBE
        assert sorted(p.name for p in (revise_repo / "instruments" / "strings" / "boxolin").iterdir()) == ["cad", "private"]

        page.emulate_media(color_scheme="dark")
        page.locator("#tab-revise").click()
        page.locator("select[name=entrant]").wait_for()
        page.screenshot(path=str(screenshot_dir / "w6-revise-dark.png"), full_page=True)
        page.set_viewport_size({"width": 400, "height": 860})
        overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0
        page.screenshot(path=str(screenshot_dir / "w6-revise-400px.png"), full_page=True)
        assert session.dialogs == []
        assert session.errors == []
        session.close()
        browser.close()


def test_revise_tab_without_a_revision_cannot_start(studio_url: str):
    import urllib.request

    body = json.dumps({"blank": {"backend": "openscad", "instrument_id": "boxolin"}}).encode()
    req = urllib.request.Request(f"{studio_url}/api/workbench/designs", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Origin": studio_url})
    created = json.loads(urllib.request.urlopen(req).read())
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        session = Session(browser, f"{studio_url}/#/workbench/{created['design_id']}", viewport={"width": 1440, "height": 900})
        page = session.page
        page.locator("#tab-revise").wait_for()
        page.locator("#tab-revise").click()
        page.locator("select[name=entrant]").wait_for()
        page.locator("textarea[name=feedback]").fill("hollow")
        page.locator("[data-action='revise-start']").click()
        page.wait_for_function("() => (document.activeElement?.textContent || '').includes('Save the origin revision first')")
        assert page.locator(".revise-confirm").count() == 0
        assert session.errors == []
        session.close()
        browser.close()
