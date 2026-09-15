"""Real-browser checks for the rebuilt Agreement analytics and Compare screens
(Playwright, opt-in).

Covers the Phase 2 verification plan for these screens:
- Elo with bootstrap intervals, the provisional marker below five games (Q5)
  and unrated ghosts.
- The Human Elo vs pass-rate scatter, with keyboard-readable points and a
  table twin.
- The small-sample caveat, outliers and the per-family filter.
- Export winners confirms the exact target paths before writing anything.
- Compare shows two runs, warns on the same run twice, and keeps one side
  when the other fails.
- Hostile entrant names stay inert; zero-WebGL, dark theme and phone width.

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

from makerbench.arena_studio import create_studio_app  # noqa: E402 - after the optional-dependency guards

RUN_A = "round-a"
RUN_B = "round-b"
RUN_EMPTY = "round-empty"
HOSTILE = '<img src=x onerror="window.__pwned=1">'
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


def _write_run(root: Path, run_id: str, pass_rates: dict[str, float], votes: list[tuple], *, ghosts=(), extra_trials=()):
    run_dir = root / "runs" / "code_cad_arena" / run_id
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    trials = []
    for instrument in ("ocarina", "kora"):
        for index, (model, rate) in enumerate(pass_rates.items()):
            scad = artifacts / f"{instrument}-{index}.scad"
            scad.write_text(f"// entrant {index} for {instrument}\ncube(10);\n", encoding="utf-8")
            trials.append(
                {
                    "trial_id": f"{instrument}-{index}",
                    "model_id": model,
                    "instrument_id": instrument,
                    "seed": 0,
                    "rep": 0,
                    "status": "completed",
                    "result": {"objective": {"objective_pass_rate": rate}, "artifacts": {"scad_path": str(scad)}},
                }
            )
    trials.extend(extra_trials)
    run_log = {
        "started_at": "2026-09-14T09:30:00Z",
        "config": {"model_ids": [*pass_rates, *ghosts], "instruments": ["ocarina", "kora"]},
        "trials": trials,
    }
    (run_dir / "run_log.json").write_text(json.dumps(run_log), encoding="utf-8")
    if votes:
        lines = [
            json.dumps(
                {
                    "pair_id": f"{run_id}-p{index}",
                    "winner": winner,
                    "voter_id": "tony",
                    "instrument_id": instrument,
                    "seed": 0,
                    "reveal": {"left": {"model_id": left}, "right": {"model_id": right}},
                }
            )
            for index, (left, right, winner, instrument) in enumerate(votes)
        ]
        (run_dir / "votes.revealed.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        # The blind log a real voting session writes alongside, so run summaries count the votes.
        blind = [
            json.dumps({"pair_id": f"{run_id}-p{index}", "winner": winner, "voter_id": "tony"})
            for index, (_left, _right, winner, _instrument) in enumerate(votes)
        ]
        (run_dir / "votes.blind.jsonl").write_text("\n".join(blind) + "\n", encoding="utf-8")
    return run_dir


@pytest.fixture
def analytics_repo(tmp_path: Path) -> Path:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {"instruments": [{"id": "ocarina", "family": "woodwind"}, {"id": "kora", "family": "strings"}]}
        ),
        encoding="utf-8",
    )
    # People rank model-a first and model-c last; the checks rank model-c first,
    # so model-c is an outlier. model-a plays five games (not provisional).
    _write_run(
        tmp_path,
        RUN_A,
        {"model-a": 0.5, "model-b": 0.25, "model-c": 1.0, HOSTILE: 0.0},
        [
            ("model-a", "model-b", "left", "ocarina"),
            ("model-a", "model-c", "left", "kora"),
            ("model-b", "model-c", "left", "ocarina"),
            (HOSTILE, "model-c", "left", "ocarina"),
            ("model-a", HOSTILE, "draw", "kora"),
            ("model-b", "model-a", "right", "ocarina"),
            ("model-a", "model-b", "left", "kora"),
        ],
        ghosts=("model-ghost",),
        extra_trials=(
            {
                "trial_id": "escape-0",
                "model_id": "model-a",
                "instrument_id": "../escape",
                "status": "completed",
                "result": {"objective": {"objective_pass_rate": 0.5}},
            },
        ),
    )
    _write_run(tmp_path, RUN_B, {"model-a": 1.0, "model-d": 0.0}, [("model-a", "model-d", "left", "ocarina")])
    _write_run(tmp_path, RUN_EMPTY, {"model-a": 1.0, "model-b": 0.0}, [])
    return tmp_path


@pytest.fixture
def studio_url(analytics_repo: Path):
    app = create_studio_app(registry_path=analytics_repo / "registry.json", repo_root=analytics_repo)
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

    def assert_inert(self):
        self.page.wait_for_load_state("networkidle")
        assert self.dialogs == []
        assert self.page.evaluate("() => window.__pwned") is None
        assert not [url for url in self.requests if url.rstrip("/").endswith("/x")]

    def close(self):
        self.context.close()


@pytest.mark.parametrize("mode", sorted(WEBGL_MODES))
@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_analytics_screen_renders_cleanly(studio_url: str, screenshot_dir: Path, mode: str, color_scheme: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright, mode)
        session = Session(
            browser,
            f"{studio_url}/#/analytics/{RUN_A}",
            viewport={"width": 1440, "height": 1000},
            color_scheme=color_scheme,
        )
        page = session.page
        elo_rows = page.locator(".elo-section table.leaderboard tbody tr")
        elo_rows.first.wait_for()
        assert elo_rows.count() == 4
        assert page.locator(".elo-section .ci-bar").count() == 4
        # model-a has five games; the other three are provisional (Q5).
        assert page.locator(".elo-section .badge-provisional").count() == 3
        provisional_free = elo_rows.filter(has_text="model-a")
        assert provisional_free.locator(".badge-provisional").count() == 0
        assert "model-ghost" in page.locator(".elo-section").inner_text()
        assert HOSTILE in page.locator(".elo-section").inner_text()

        page.locator(".agreement-section .agreement-headline").wait_for()
        assert "ρ = " in page.locator(".agreement-section .agreement-headline").inner_text()
        assert "not meaningful" in page.locator(".agreement-section .caveat").inner_text()
        assert page.locator(".scatter .point").count() == 4
        assert page.locator("table.rankings tbody tr").count() == 4
        assert "model-c" in page.locator(".outliers").inner_text()

        session.assert_inert()
        page.screenshot(path=str(screenshot_dir / f"f4-analytics-{mode}-{color_scheme}.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()


def test_scatter_points_are_keyboard_readable(studio_url: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/analytics/{RUN_A}", viewport={"width": 1440, "height": 1000})
        page = session.page
        points = page.locator(".scatter .point")
        points.first.wait_for()
        readout = page.locator(".scatter-readout")
        assert "Focus or hover" in readout.inner_text()
        points.first.focus()
        first = readout.inner_text()
        assert ": Elo " in first and "pass rate" in first
        page.keyboard.press("Tab")
        assert page.evaluate("() => document.activeElement.classList.contains('point')")
        assert readout.inner_text() != first
        assert page.evaluate("() => getComputedStyle(document.activeElement).strokeWidth") == "3px"
        session.close()
        browser.close()


def test_export_winners_confirms_target_paths_before_writing(
    studio_url: str, analytics_repo: Path, screenshot_dir: Path
):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/analytics/{RUN_A}", viewport={"width": 1440, "height": 1000})
        page = session.page
        export = page.get_by_role("button", name="Export winners…")
        page.wait_for_function(
            "() => [...document.querySelectorAll('.export-section button')].some((b) => b.getAttribute('aria-disabled') === 'false')"
        )
        export.click()
        confirm = page.locator(".confirm")
        confirm.wait_for()
        text = confirm.inner_text()
        assert "instruments/ocarina/winner.scad" in text
        assert "instruments/kora/winner.scad" in text
        assert "../escape" in text
        assert page.evaluate("() => document.activeElement.id") == "export-confirm-title"
        assert not (analytics_repo / "instruments").exists()

        confirm.get_by_role("button", name="Cancel").click()
        confirm.wait_for(state="detached")
        assert not (analytics_repo / "instruments").exists()

        export.click()
        page.locator(".confirm").get_by_role("button", name="Overwrite 2 files").click()
        page.get_by_text("Exported 2 winners.").wait_for()
        for instrument in ("ocarina", "kora"):
            assert (analytics_repo / "instruments" / instrument / "winner.scad").read_text(encoding="utf-8").startswith("// entrant")
        assert not (analytics_repo / "escape").exists()
        assert "unsafe instrument id" in page.locator(".export-result").inner_text()

        page.screenshot(path=str(screenshot_dir / "f4-analytics-export-result.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()


def test_export_focus_returns_on_cancel_and_a_failed_export_offers_retry(
    studio_url: str, analytics_repo: Path, screenshot_dir: Path
):
    """Claude UI review #779: keyboard focus never falls to <body> around the export confirm."""
    # Focus moves in a Preact effect after render, so wait for it rather than read it once.
    on_export = "() => (document.activeElement?.textContent || '').trim() === 'Export winners…'"
    in_confirm = "() => document.activeElement?.id === 'export-confirm-title'"
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/analytics/{RUN_A}", viewport={"width": 1440, "height": 1000})
        page = session.page
        page.wait_for_function(
            "() => [...document.querySelectorAll('.export-section button')].some((b) => b.getAttribute('aria-disabled') === 'false')"
        )
        page.get_by_role("button", name="Export winners…").focus()

        # Cancel by keyboard: focus goes back to the button that opened the confirm.
        page.keyboard.press("Enter")
        confirm = page.locator(".confirm")
        confirm.wait_for()
        page.wait_for_function(in_confirm, timeout=5_000)
        confirm.get_by_role("button", name="Cancel").focus()
        page.keyboard.press("Enter")
        confirm.wait_for(state="detached")
        page.wait_for_function(on_export, timeout=5_000)

        # Escape cancels too, with the same focus rule.
        page.keyboard.press("Enter")
        confirm.wait_for()
        page.wait_for_function(in_confirm, timeout=5_000)
        page.keyboard.press("Escape")
        confirm.wait_for(state="detached")
        page.wait_for_function(on_export, timeout=5_000)

        # A failed export is announced, takes focus, and offers a retry; nothing is written.
        page.route(
            "**/export-winners",
            lambda route: route.fulfill(status=500, content_type="application/json", body=json.dumps({"detail": "disk full"})),
        )
        page.keyboard.press("Enter")
        confirm.get_by_role("button", name="Overwrite 2 files").focus()
        page.keyboard.press("Enter")
        error = page.locator(".export-error")
        error.get_by_text("disk full").wait_for()
        page.wait_for_function("() => Boolean(document.activeElement?.closest('.export-error'))", timeout=5_000)
        assert not (analytics_repo / "instruments").exists()
        page.screenshot(path=str(screenshot_dir / "f4-analytics-export-failed.png"), full_page=True)

        page.unroute("**/export-winners")
        error.get_by_role("button", name="Try again").focus()
        page.keyboard.press("Enter")
        page.get_by_text("Exported 2 winners.").wait_for()
        page.wait_for_function("() => document.activeElement && document.activeElement !== document.body", timeout=5_000)
        assert (analytics_repo / "instruments" / "ocarina" / "winner.scad").exists()
        # The only console error is the browser logging the deliberately failed request.
        assert [error for error in session.errors if "500" not in error] == []
        session.close()
        browser.close()


def test_families_filter_and_a_run_without_votes(studio_url: str, screenshot_dir: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/analytics/{RUN_A}", viewport={"width": 1440, "height": 1000})
        page = session.page
        family = page.locator(".families-section select[name=family]")
        family.wait_for()
        family.select_option("woodwind")
        woodwind = page.locator(".families-section").inner_text()
        assert "in woodwind" in woodwind
        family.select_option("strings")
        page.locator(".families-section .agreement-headline", has_text="in strings").wait_for()
        strings_rows = page.locator(".families-section table.leaderboard tbody tr")
        # Strings (kora) votes involve model-a, model-c and the hostile entrant, never model-b alone.
        assert strings_rows.filter(has_text="model-a").count() == 1

        page.goto(f"{studio_url}/#/analytics/{RUN_EMPTY}")
        page.get_by_text("No human votes yet").wait_for()
        page.get_by_text("No family has votes yet").wait_for()
        page.screenshot(path=str(screenshot_dir / "f4-analytics-no-votes.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()


def test_compare_two_runs_with_independent_failures(studio_url: str, screenshot_dir: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(
            browser, f"{studio_url}/#/compare?a={RUN_A}&b={RUN_B}", viewport={"width": 1440, "height": 1000}
        )
        page = session.page
        columns = page.locator(".compare-column")
        columns.nth(1).locator("table.leaderboard tbody tr").first.wait_for()
        assert columns.nth(0).locator("table.leaderboard tbody tr").count() == 4
        assert columns.nth(1).locator("table.leaderboard tbody tr").count() == 2
        assert "Entrants rated in both runs: model-a." in page.locator(".screen-compare").inner_text()
        session.assert_inert()
        page.screenshot(path=str(screenshot_dir / "f4-compare.png"), full_page=True)
        assert session.errors == []

        page.select_option("select[name=run-b]", RUN_A)
        page.get_by_text("Both sides show the same run.").wait_for()

        page.goto(f"{studio_url}/#/compare?a={RUN_A}&b=no-such-run")
        columns.nth(1).locator(".state-error").first.wait_for()
        columns.nth(0).locator("table.leaderboard tbody tr").first.wait_for()
        assert columns.nth(0).locator("table.leaderboard tbody tr").count() == 4
        session.close()
        browser.close()


def test_analytics_and_compare_at_phone_width(studio_url: str, screenshot_dir: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/analytics/{RUN_A}", viewport={"width": 400, "height": 800})
        page = session.page
        page.locator(".scatter .point").first.wait_for()
        overflow = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        assert page.evaluate(overflow) <= 0
        page.screenshot(path=str(screenshot_dir / "f4-analytics-400px.png"), full_page=True)

        page.goto(f"{studio_url}/#/compare?a={RUN_A}&b={RUN_B}")
        page.locator(".compare-column").nth(1).locator("table.leaderboard tbody tr").first.wait_for()
        assert page.evaluate(overflow) <= 0
        page.screenshot(path=str(screenshot_dir / "f4-compare-400px.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()
