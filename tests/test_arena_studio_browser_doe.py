"""Real-browser checks for the rebuilt DoE matrix screen (Playwright, opt-in).

Covers the Phase 2 verification plan for this screen:
- A live preview of instruments × entrants × levels × tiers × seeds.
- Per-entrant cost badges.
- A budget what-if that keeps unknown-cost jobs in their own bucket.
- Unknown-cost entrants need an explicit positive ceiling before the queue
  can be written.
- Gatekeeper skips are predicted and then reported.
- Writing the queue never executes anything.
- Hostile registry text stays inert; zero-WebGL, dark theme and phone width.

Same opt-in rules as tests/test_arena_studio_browser.py.
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

from PIL import Image  # noqa: E402 - after the optional-dependency guards

from makerbench import nightly_cad  # noqa: E402 - after the optional-dependency guards
from makerbench.arena_studio import create_studio_app  # noqa: E402 - after the optional-dependency guards
from makerbench.arena_studio.service import ArenaStudioService  # noqa: E402 - after the optional-dependency guards

HOSTILE_FAMILY = '"><img src=x onerror="window.__pwned=1">'
SUBSCRIPTION = "claude-code-opus-5, codex-gpt-5.6"
UNKNOWN_MODEL = "openrouter-nobody-has-run-this"
WEBGL_MODES = {
    "webgl": [],
    "zero-webgl": ["--disable-3d-apis", "--disable-webgl", "--disable-webgl2"],
}


def _extra_browser_args() -> list[str]:
    return os.environ.get("ARENA_STUDIO_BROWSER_ARGS", "").split()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def doe_repo(tmp_path: Path) -> Path:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "instruments": [
                    {"id": "ocarina", "display_name": "Ocarina", "family": "woodwind", "envelope_mm": [140, 90, 70]},
                    {"id": "kora", "display_name": "Kora", "family": "strings", "envelope_mm": [1200, 350, 250]},
                    {"id": "guzheng", "display_name": "Guzheng", "family": HOSTILE_FAMILY, "envelope_mm": [1600, 350, 120]},
                ]
            }
        ),
        encoding="utf-8",
    )
    for task_id in ("ocarina", "kora"):
        image = tmp_path / "tasks" / task_id / "reference.png"
        image.parent.mkdir(parents=True)
        Image.new("RGB", (320, 240), (120, 110, 90)).save(image)
    ArenaStudioService(registry_path=registry, repo_root=tmp_path).set_task_approval("ocarina", True)
    return tmp_path


@pytest.fixture
def studio_url(doe_repo: Path):
    app = create_studio_app(registry_path=doe_repo / "registry.json", repo_root=doe_repo)
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


def _launch(playwright, mode: str):
    try:
        return playwright.chromium.launch(args=[*WEBGL_MODES[mode], *_extra_browser_args()])
    except Exception as error:
        if REQUIRE_BROWSER:
            raise
        pytest.skip(f"chromium is not available: {error}")


class Session:
    def __init__(self, browser, url: str, **context_args):
        self.context = browser.new_context(**context_args)
        self.page = self.context.new_page()
        self.errors: list[str] = []
        self.dialogs: list[str] = []
        self.requests: list[str] = []
        self.page.on("console", lambda message: self.errors.append(message.text) if message.type == "error" else None)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("dialog", self._on_dialog)
        self.page.on("request", lambda request: self.requests.append(request.url))
        self.page.goto(url)

    def _on_dialog(self, dialog):
        self.dialogs.append(dialog.message)
        dialog.dismiss()

    def pick(self, *instruments: str):
        for instrument in instruments:
            self.page.locator(f"input[name=instrument][value={instrument}]").check()

    def close(self):
        self.context.close()


def _write_button(page):
    return page.locator(".doe-write-form button[type=submit]")


@pytest.mark.parametrize("mode", sorted(WEBGL_MODES))
@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_doe_screen_previews_the_matrix(studio_url: str, screenshot_dir: Path, mode: str, color_scheme: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright, mode)
        session = Session(
            browser, f"{studio_url}/#/doe", viewport={"width": 1440, "height": 1000}, color_scheme=color_scheme
        )
        page = session.page
        page.locator("input[name=instrument][value=ocarina]").wait_for()
        session.pick("ocarina", "kora")
        page.fill("textarea[name=models]", SUBSCRIPTION)
        page.fill("input[name=seeds]", "0, 1")

        preview = page.locator(".doe-preview")
        # 2 instruments x 2 entrants x 4 levels x 1 tier x 2 seeds.
        preview.locator(".facts dd.measure").first.filter(has_text="32").wait_for()
        assert preview.locator(".facts").inner_text().count("4") >= 1
        assert preview.locator(".cost", has_text="$0 subscription").count() == 2
        assert "4 jobs within $5.00, 0 jobs over, and 0 jobs with unknown cost" in preview.locator(".budget-summary").inner_text()
        assert preview.locator("table.jobs-what-if tbody tr").count() == 4
        page.locator(".skips li", has_text="kora").filter(has_text="Reference image not approved").wait_for()
        assert page.locator(".skips li", has_text="ocarina").count() == 0

        page.screenshot(path=str(screenshot_dir / f"f5-doe-{mode}-{color_scheme}.png"), full_page=True)
        page.wait_for_load_state("networkidle")
        assert session.dialogs == []
        assert page.evaluate("() => window.__pwned") is None
        assert session.errors == []
        session.close()
        browser.close()


def test_unknown_cost_needs_a_ceiling_and_the_queue_write_runs_nothing(
    studio_url: str, doe_repo: Path, screenshot_dir: Path
):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/doe", viewport={"width": 1440, "height": 1000})
        page = session.page
        page.locator("input[name=instrument][value=ocarina]").wait_for()
        session.pick("ocarina")
        page.fill("textarea[name=models]", f"claude-code-opus-5, {UNKNOWN_MODEL}")
        page.locator("input[name=level][value=L2]").uncheck()
        page.locator("input[name=level][value=L3]").uncheck()
        page.locator("input[name=level][value=L4]").uncheck()
        page.fill("input[name=run_id]", "doe-ui-night")

        summary = page.locator(".budget-summary")
        summary.filter(has_text="1 job with unknown cost").wait_for()
        assert "0 jobs within" in summary.inner_text()
        # A huge budget never makes an unknown-cost job affordable.
        page.fill("input[name=budget]", "1000")
        summary.filter(has_text="within $1000.00").wait_for()
        assert "0 jobs within" in summary.inner_text() and "1 job with unknown cost" in summary.inner_text()
        assert page.locator("input[name=budget-slider]").input_value() == "1000"
        page.locator("input[name=budget-slider]").fill("12.5")
        assert page.locator("input[name=budget]").input_value() == "12.5"

        ceiling = page.locator(f"input[name='ceiling-{UNKNOWN_MODEL}']")
        ceiling.wait_for()
        page.locator(".blockers li", has_text=f"Set a cost ceiling for {UNKNOWN_MODEL}").wait_for()
        write = _write_button(page)
        assert write.get_attribute("aria-disabled") == "true"
        write.click(force=True)
        ceiling.fill("0")
        page.wait_for_load_state("networkidle")
        assert write.get_attribute("aria-disabled") == "true"
        assert not [url for url in session.requests if url.endswith("/api/doe/queue")]
        run_dir = doe_repo / "runs" / "code_cad_arena" / "doe-ui-night"
        assert not run_dir.exists()

        ceiling.fill("0.75")
        page.locator(".doe-write .blockers").wait_for(state="detached")
        write.click()
        result = page.locator(".doe-result")
        result.wait_for()
        assert "Wrote 1 nightly job to runs/code_cad_arena/doe-ui-night/doe_queue.json. Nothing has run." in " ".join(
            result.inner_text().split()
        )

        payload, jobs = nightly_cad.load_queue(run_dir / "doe_queue.json")
        assert len(jobs) == 1
        ceilings = {entrant.model_id: entrant.max_cost_usd for entrant in jobs[0].entrants}
        assert ceilings == {"claude-code-opus-5": 0.0, UNKNOWN_MODEL: 0.75}
        assert jobs[0].budget_usd == 12.5
        assert sorted(path.name for path in run_dir.iterdir()) == ["doe_queue.json"]

        page.screenshot(path=str(screenshot_dir / "f5-doe-ceiling-and-write.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()


def test_unapproved_instruments_are_reported_as_skipped(studio_url: str, doe_repo: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/doe", viewport={"width": 1440, "height": 1000})
        page = session.page
        page.locator("input[name=instrument][value=kora]").wait_for()
        session.pick("kora", "guzheng")
        page.fill("textarea[name=models]", SUBSCRIPTION)
        page.fill("input[name=run_id]", "doe-ui-skips")
        page.locator(".skips li", has_text="guzheng").wait_for()
        write = _write_button(page)
        page.wait_for_function(
            "() => document.querySelector('.doe-write-form button[type=submit]').getAttribute('aria-disabled') === 'false'"
        )
        write.click()
        result = page.locator(".doe-result")
        result.wait_for()
        text = " ".join(result.inner_text().split())
        assert "Wrote 0 nightly jobs" in text
        assert "kora: Reference image not approved" in text
        assert "guzheng: Reference image not approved" in text
        assert (doe_repo / "runs" / "code_cad_arena" / "doe-ui-skips" / "doe_queue.json").exists()
        session.close()
        browser.close()


def test_existing_queue_asks_before_it_is_replaced(studio_url: str, doe_repo: Path, screenshot_dir: Path):
    """Claude UI review #780: writing a run name whose doe_queue.json already exists asks in
    the page first. Keeping it writes nothing, and keyboard focus never falls to <body>."""
    run_dir = doe_repo / "runs" / "code_cad_arena" / "doe-ui-existing"
    run_dir.mkdir(parents=True)
    existing = run_dir / "doe_queue.json"
    existing.write_text('{"schema": "existing-queue-sentinel", "jobs": []}', encoding="utf-8")
    before = existing.read_bytes()
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/doe", viewport={"width": 1440, "height": 1000})
        page = session.page
        page.locator("input[name=instrument][value=ocarina]").wait_for()
        session.pick("ocarina")
        page.fill("textarea[name=models]", SUBSCRIPTION)
        page.fill("input[name=run_id]", "doe-ui-existing")
        page.wait_for_function(
            "() => document.querySelector('.doe-write-form button[type=submit]').getAttribute('aria-disabled') === 'false'"
        )
        _write_button(page).focus()
        page.keyboard.press("Enter")
        confirm = page.locator(".doe-replace")
        confirm.wait_for()
        assert "runs/code_cad_arena/doe-ui-existing/doe_queue.json" in " ".join(confirm.inner_text().split())
        page.wait_for_function("() => document.activeElement?.id === 'doe-replace-title'", timeout=5_000)
        assert existing.read_bytes() == before
        page.screenshot(path=str(screenshot_dir / "f5-doe-replace-confirm.png"), full_page=True)

        confirm.get_by_role("button", name="Keep the existing queue").focus()
        page.keyboard.press("Enter")
        confirm.wait_for(state="detached")
        page.wait_for_function("() => document.activeElement?.matches('.doe-write-form button[type=submit]')", timeout=5_000)
        assert existing.read_bytes() == before
        assert page.locator(".doe-result").count() == 0

        page.keyboard.press("Enter")
        confirm.wait_for()
        page.wait_for_function("() => document.activeElement?.id === 'doe-replace-title'", timeout=5_000)
        confirm.get_by_role("button", name="Replace the queue").focus()
        page.keyboard.press("Enter")
        page.locator(".doe-result").wait_for()
        # Re-review #780: the Replace button unmounts; focus lands on the result, not <body>.
        page.wait_for_function("() => Boolean(document.activeElement?.closest('.doe-result'))", timeout=5_000)
        _payload, jobs = nightly_cad.load_queue(existing)
        assert existing.read_bytes() != before and len(jobs) == 1
        assert len([url for url in session.requests if url.endswith("/api/doe/queue")]) == 3
        # The browser logs the 409 answers the page turns into a question.
        assert [error for error in session.errors if "409" not in error] == []
        session.close()
        browser.close()


def test_a_failed_replace_keeps_focus_and_offers_retry(studio_url: str, doe_repo: Path, screenshot_dir: Path):
    """Re-review #780: when "Replace the queue" fails, the error takes keyboard focus and
    offers Try again, which re-sends the replace; nothing is written until it succeeds."""
    run_dir = doe_repo / "runs" / "code_cad_arena" / "doe-ui-replace-fails"
    run_dir.mkdir(parents=True)
    existing = run_dir / "doe_queue.json"
    existing.write_text('{"schema": "existing-queue-sentinel", "jobs": []}', encoding="utf-8")
    before = existing.read_bytes()

    def fail_replace(route):
        if '"replace":true' in (route.request.post_data or "").replace(" ", ""):
            route.fulfill(status=500, content_type="application/json", body=json.dumps({"detail": "disk full"}))
        else:
            route.continue_()

    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/doe", viewport={"width": 1440, "height": 1000})
        page = session.page
        page.locator("input[name=instrument][value=ocarina]").wait_for()
        session.pick("ocarina")
        page.fill("textarea[name=models]", SUBSCRIPTION)
        page.fill("input[name=run_id]", "doe-ui-replace-fails")
        page.wait_for_function(
            "() => document.querySelector('.doe-write-form button[type=submit]').getAttribute('aria-disabled') === 'false'"
        )
        page.route("**/api/doe/queue", fail_replace)
        _write_button(page).focus()
        page.keyboard.press("Enter")
        confirm = page.locator(".doe-replace")
        confirm.wait_for()
        page.wait_for_function("() => document.activeElement?.id === 'doe-replace-title'", timeout=5_000)
        confirm.get_by_role("button", name="Replace the queue").focus()
        page.keyboard.press("Enter")

        error = page.locator(".doe-write-error")
        error.get_by_text("disk full").wait_for()
        page.wait_for_function("() => Boolean(document.activeElement?.closest('.doe-write-error'))", timeout=5_000)
        assert existing.read_bytes() == before
        page.screenshot(path=str(screenshot_dir / "f5-doe-replace-failed.png"), full_page=True)

        page.unroute("**/api/doe/queue")
        error.get_by_role("button", name="Try again").focus()
        page.keyboard.press("Enter")
        page.locator(".doe-result").wait_for()
        page.wait_for_function("() => Boolean(document.activeElement?.closest('.doe-result'))", timeout=5_000)
        _payload, jobs = nightly_cad.load_queue(existing)
        assert existing.read_bytes() != before and len(jobs) == 1
        # The browser logs the handled 409 and the routed 500.
        assert [error for error in session.errors if "409" not in error and "500" not in error] == []
        session.close()
        browser.close()


def test_doe_hostile_registry_text_and_phone_width(studio_url: str, screenshot_dir: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/doe", viewport={"width": 400, "height": 800})
        page = session.page
        row = page.locator(".check-row", has=page.locator("input[value=guzheng]"))
        row.wait_for()
        assert HOSTILE_FAMILY in row.inner_text()
        session.pick("ocarina")
        page.fill("textarea[name=models]", SUBSCRIPTION)
        page.locator(".budget-summary").wait_for()
        page.wait_for_load_state("networkidle")
        assert page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0
        assert session.dialogs == []
        assert page.evaluate("() => window.__pwned") is None
        assert page.locator("main img").count() == 0
        assert not [url for url in session.requests if url.rstrip("/").endswith("/x")]
        page.screenshot(path=str(screenshot_dir / "f5-doe-400px.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()
