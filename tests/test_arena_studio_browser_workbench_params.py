"""Real-browser checks for the workbench Parameters tab (#788 W5, Playwright).

Opt-in like the other browser specs: skipped unless Playwright and Chromium
are installed; ``ARENA_STUDIO_REQUIRE_BROWSER=1`` (CI) fails instead. The
compile runs in the real OpenSCAD sandbox; ``MAKERBENCH_REQUIRE_SANDBOX=1``
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

HOSTILE_DOC = '<img src=x onerror="window.__pwned=1">'
# Every honest state the tab must show, in one master: a declared range, a
# unit from the suffix, no unit, a bool, string options, a vector, a derived
# expression, a reassigned name, a `$` special and a hostile doc comment.
PARAMS_SCAD = f"""/* [Body] */
// Width of the box
w_mm = 10; // [5:20]
h_mm = 12;
depth = 4;
hollow = false;
label = "a"; // [a, b, c]
scale_v = [1, 2, 3];
derived_mm = w_mm * 2;
dup = 1;
dup = 2;
/* [Advanced] */
$fn = 24;
doc_hostile = 3; // {HOSTILE_DOC}
cube([w_mm, h_mm, depth]);
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def params_repo(tmp_path: Path) -> Path:
    (tmp_path / "runs" / "code_cad_arena").mkdir(parents=True)
    (tmp_path / "registry.json").write_text(json.dumps({
        "schema": "makerbench-code-cad-arena-registry-v1",
        "instruments": [{"id": "boxolin", "display_name": "Boxolin", "family": "strings", "task_kind": "single_part",
                         "envelope_mm": [100, 100, 100], "min_bodies": 1, "repo_path": "strings/boxolin"}],
    }), encoding="utf-8")
    cad = tmp_path / "instruments" / "strings" / "boxolin" / "cad"
    cad.mkdir(parents=True)
    (cad / "params.scad").write_text(PARAMS_SCAD, encoding="utf-8")
    return tmp_path


@pytest.fixture
def studio_url(params_repo: Path):
    app = create_studio_app(
        registry_path=params_repo / "registry.json", repo_root=params_repo,
        instruments_root=params_repo / "instruments",
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


def _open_saved_master(page, studio_url: str) -> None:
    """Setup only (clicks): open the params master and save its origin revision."""

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


def _row_text(page, name: str) -> str:
    return page.locator(f"[data-param-row='{name}']").inner_text()


@needs_sandbox
def test_keyboard_only_parameters_apply_refuse_reset_and_compare(studio_url: str, params_repo: Path, screenshot_dir: Path):
    """Plan §6 capability 2 and §7: honest states, inline refusal, Apply and
    compile, Reset, then Compare shows the parameter delta; keyboard only after
    setup, with document.activeElement asserted at every step."""
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        session = Session(browser, f"{studio_url}/#/workbench", viewport={"width": 1440, "height": 1100})
        page = session.page
        _open_saved_master(page, studio_url)

        # Arrow keys move between the live tabs; the panel switches with them.
        _tab_to(page, "() => document.activeElement?.id === 'tab-code'")
        page.keyboard.press("ArrowRight")
        page.wait_for_function("() => document.activeElement?.id === 'tab-parameters'")
        assert page.locator("#tab-parameters").get_attribute("aria-selected") == "true"
        assert page.locator("#panel-parameters").is_visible() and not page.locator("#panel-code").is_visible()
        page.locator("[data-param-row='w_mm']").wait_for()
        assert "11 parameters, 8 editable." in page.locator(".parameters-tab > p[role=status]").inner_text()

        # Honest states, verbatim from the API, and the hostile doc as text.
        groups = page.locator(".param-group h4").all_inner_texts()
        assert groups == ["Body", "Advanced"]
        assert "Declared: 5 to 20" in _row_text(page, "w_mm") and "Width of the box" in _row_text(page, "w_mm")
        assert "Envelope 100 × 100 × 100 mm" in _row_text(page, "w_mm")
        assert "mm" in _row_text(page, "h_mm") and "range not declared" in _row_text(page, "h_mm")
        assert "unit unknown" in _row_text(page, "depth") and "range not declared" in _row_text(page, "depth")
        assert "derived, edit it in the code" in _row_text(page, "derived_mm") and "w_mm * 2" in _row_text(page, "derived_mm")
        assert page.locator("[data-param-row='derived_mm']").get_attribute("data-editable") == "false"
        assert page.locator("[data-param-row='dup']").count() == 2  # both assignments, both read-only
        assert "assigned more than once" in page.locator("[data-param-row='dup']").first.inner_text()
        assert page.locator("[data-param-row='dup'] input").count() == 0
        assert page.locator("[data-param-row='label'] select option").all_inner_texts() == ["a", "b", "c"]
        assert page.locator("[data-param-row='scale_v'] input").count() == 3
        assert HOSTILE_DOC in _row_text(page, "doc_hostile")
        assert page.locator("main img:not(.workbench-image)").count() == 0 and page.evaluate("() => window.__pwned") is None
        page.screenshot(path=str(screenshot_dir / "w5-parameters-light.png"), full_page=True)

        # An out-of-range value is refused inline; Apply focuses the field.
        _tab_to(page, "() => document.activeElement?.dataset?.param === 'w_mm'")
        page.keyboard.press("Control+a")
        page.keyboard.type("25")
        page.locator("[data-param-row='w_mm'] .param-error").wait_for()
        assert "between 5 and 20" in page.locator("[data-param-row='w_mm'] .param-error").inner_text()
        assert page.locator("[data-param='w_mm']").get_attribute("aria-invalid") == "true"
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'apply-params'")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.dataset?.param === 'w_mm'")
        assert "refused" in page.locator(".param-changes .panel-status").inner_text()
        assert page.locator("[data-action='cancel']").count() == 0  # nothing compiled

        # A non-finite value is refused too; a valid one lands in Changes.
        page.keyboard.press("Control+a")
        page.keyboard.type("1e999")
        page.locator("[data-param-row='w_mm'] .param-error").wait_for()
        page.keyboard.press("Control+a")
        page.keyboard.type("15")
        page.wait_for_function("() => document.querySelector(\"[data-param-row='w_mm']\")?.dataset?.changed === 'true'")
        assert page.locator("[data-param-row='w_mm'] .param-error").count() == 0
        _tab_to(page, "() => document.activeElement?.dataset?.param === 'hollow'")
        page.keyboard.press("Space")
        changes = page.locator(".param-changes ul").inner_text()
        assert "w_mm: 10 → 15" in changes and "hollow: false → true" in changes

        # Reset restores the revision's values and takes focus to the summary.
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'reset-params'")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.id === 'param-changes-title'")
        assert "No changes yet" in page.locator(".param-changes").inner_text()
        assert page.locator("[data-param='w_mm']").input_value() == "10"
        assert not page.locator("[data-param='hollow']").is_checked()

        # Apply and compile: the job runs like any compile, fields go read-only.
        _tab_to(page, "() => document.activeElement?.dataset?.param === 'w_mm'", key="Shift+Tab")
        page.keyboard.press("Control+a")
        page.keyboard.type("15")
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'apply-params'")
        page.keyboard.press("Enter")
        page.locator("[data-action='cancel']").wait_for(timeout=10_000)
        assert page.locator("[data-param='w_mm']").get_attribute("readonly") is not None
        page.wait_for_function("() => (document.querySelector('pre.log')?.textContent || '').includes('compiling in the sandbox')", timeout=20_000)
        page.locator("[data-action='save']").wait_for(timeout=120_000)
        page.wait_for_function("() => document.activeElement?.closest('.workbench-result') !== null", timeout=5_000)
        assert page.locator(".workbench-result h3").inner_text().startswith("Result of the succeeded compile")
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'save'")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.id === 'save-title'")
        _tab_to(page, "() => document.activeElement?.dataset?.action === 'confirm-save'")
        page.keyboard.press("Enter")
        page.wait_for_function("() => (document.querySelector('.workbench-head .panel-status')?.textContent || '').startsWith('Saved revision 2 from 1')")
        page.locator("table.revisions tbody tr").nth(1).wait_for()
        assert "parameters changed" in page.locator("table.revisions tbody tr").nth(1).inner_text()

        # The new revision's tab reads 15 and lists no changes; the source moved with it.
        page.wait_for_function("() => document.querySelector(\"[data-param='w_mm']\")?.value === '15'")
        assert "No changes yet" in page.locator(".param-changes").inner_text()
        page.locator("#tab-code").click()
        page.wait_for_function("() => (document.querySelector('.code-editor-text')?.value || '').includes('w_mm = 15; // [5:20]')")
        source = page.locator(".code-editor-text").input_value()
        assert "// Width of the box\nw_mm = 15; // [5:20]\nh_mm = 12;" in source  # only the literal moved

        # Compare 1 vs 2: the parameter delta names w_mm only.
        _tab_to(page, "() => document.activeElement?.dataset?.compare !== undefined")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.activeElement?.id === 'compare-title'")
        page.locator(".compare-panel table.parameter-delta").wait_for()
        delta = page.locator(".compare-panel table.parameter-delta").inner_text()
        assert "w_mm" in delta and "10" in delta and "15" in delta and "hollow" not in delta

        page.emulate_media(color_scheme="dark")
        page.locator("#tab-parameters").click()
        page.locator("[data-param-row='w_mm']").wait_for()
        page.screenshot(path=str(screenshot_dir / "w5-parameters-dark.png"), full_page=True)
        page.set_viewport_size({"width": 400, "height": 860})
        overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0
        page.screenshot(path=str(screenshot_dir / "w5-parameters-400px.png"), full_page=True)
        assert session.dialogs == []
        assert session.errors == []
        # nothing was written into the instrument repo
        assert (params_repo / "instruments" / "strings" / "boxolin" / "cad" / "params.scad").read_text() == PARAMS_SCAD
        assert sorted(p.name for p in (params_repo / "instruments" / "strings" / "boxolin" / "cad").iterdir()) == ["params.scad"]
        session.close()
        browser.close()


def test_parameters_tab_without_a_revision_points_at_saving_first(studio_url: str):
    import urllib.request

    body = json.dumps({"blank": {"backend": "openscad", "instrument_id": "boxolin"}}).encode()
    req = urllib.request.Request(f"{studio_url}/api/workbench/designs", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Origin": studio_url})
    created = json.loads(urllib.request.urlopen(req).read())
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        session = Session(browser, f"{studio_url}/#/workbench/{created['design_id']}", viewport={"width": 1440, "height": 900})
        page = session.page
        page.locator("#tab-parameters").wait_for()
        page.locator("#tab-parameters").click()
        assert "Save the origin revision first" in page.locator("#panel-parameters").inner_text()
        assert page.locator("[data-action='apply-params']").count() == 0
        assert session.errors == []
        session.close()
        browser.close()
