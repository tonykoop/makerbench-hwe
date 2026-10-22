"""Real-browser checks for the Control Room New Run launcher (#1114, Playwright, opt-in).

- Presets fill the form; the entrant grid toggles and disabled cells say why.
- The estimate follows the grid (per-entrant and totals) and never launches anything.
- Add to tonight with queue writes off saves a draft and leaves the queue bytes alone;
  with them on it appends one job to a fixture queue.
- Run now stays disabled without --allow-live.
- 1440 px and 390 px, light and dark, with no horizontal scroll.

Same opt-in rules as tests/test_arena_studio_browser.py.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from datetime import date, timedelta
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

from test_control_room_newrun import build_newrun_repo  # noqa: E402

from makerbench import nightly_cad  # noqa: E402
from makerbench.arena_studio import create_studio_app  # noqa: E402 - after the optional-dependency guards
from makerbench.arena_studio import doe  # noqa: E402


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _estimator(model_id, *, telemetry_store, backend):
    if model_id.startswith("anthropic/"):
        return {"cost_usd": 0.4, "cost_source": "telemetry", "duration_s": 1200.0}
    return {"cost_usd": 0.0, "cost_source": "subscription_zero_marginal", "duration_s": 1800.0}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(doe, "estimate_model_cost_and_time", _estimator)
    monkeypatch.setattr(doe, "backend_availability", lambda backend: {"status": "available", "reason": "ok"})
    return build_newrun_repo(tmp_path)


def _serve(repo: Path, **kwargs):
    app = create_studio_app(registry_path=repo / "registry.json", repo_root=repo, **kwargs)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("Studio test server did not start")
        time.sleep(0.05)
    return server, thread, f"http://127.0.0.1:{port}"


@pytest.fixture
def studio(repo: Path, request):
    server, thread, url = _serve(repo, **getattr(request, "param", {}))
    yield url
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def screenshot_dir(tmp_path: Path) -> Path:
    target = Path(os.environ.get("ARENA_STUDIO_SCREENSHOTS") or tmp_path / "screens")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _launch(playwright):
    try:
        return playwright.chromium.launch(args=["--disable-webgl", "--disable-webgl2", *os.environ.get("ARENA_STUDIO_BROWSER_ARGS", "").split()])
    except Exception as error:
        if REQUIRE_BROWSER:
            raise
        pytest.skip(f"chromium is not available: {error}")


def _open(browser, url: str, **context_args):
    context = browser.new_context(**{"viewport": {"width": 1440, "height": 1100}, **context_args})
    page = context.new_page()
    problems: list[str] = []
    requests: list[str] = []
    page.on("console", lambda msg: problems.append(msg.text) if msg.type == "error" and "404" not in msg.text else None)
    page.on("pageerror", lambda error: problems.append(str(error)))
    page.on("request", lambda req: requests.append(f"{req.method} {req.url}"))
    page.goto(url + "/#/newrun")
    page.locator(".newrun-step").first.wait_for()
    return context, page, problems, requests


def _cell(page, pick: str):
    return page.locator(f'.newrun-cell[data-pick="{pick}"]')


def _spend(page) -> str:
    return page.get_by_test_id("spend").inner_text()


def test_grid_presets_and_estimate(studio: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context, page, problems, requests = _open(browser, studio)
        page.get_by_role("button", name="Overnight matchup").click()
        page.wait_for_function("() => document.querySelector('[data-testid=spend]').textContent.includes('$0.40')")
        summary = page.get_by_test_id("newrun-summary")
        assert "4 designs" in summary.inner_text()
        assert "about $0.40 of $2.00" in _spend(page)
        assert "Claude 2 · Codex 1" in summary.inner_text()
        # SolidWorks and Fusion share the seat: 1800 + 1800 s serial.
        assert "1 h 0 min" in summary.inner_text()

        assert _cell(page, "freecad:fable").is_disabled()
        assert "only reachable through OpenRouter" in _cell(page, "freecad:fable").get_attribute("title")
        assert all(_cell(page, f"cadam-cli:{m}").is_disabled() for m in ("fable", "opus", "sonnet", "sol"))

        _cell(page, "cadam:sonnet").click()
        page.wait_for_function("() => document.querySelector('[data-testid=spend]').textContent.includes('$0.80')")
        assert _cell(page, "cadam:sonnet").get_attribute("aria-pressed") == "true"
        assert page.get_by_role("button", name="Overnight matchup").get_attribute("aria-pressed") == "false"

        page.get_by_label("Variants to sweep (optional, comma-separated)").fill("14, 16")
        page.get_by_label("Variants to sweep (optional, comma-separated)").press("Tab")
        page.wait_for_function("() => document.querySelector('[data-testid=newrun-summary]').textContent.includes('10 designs')")

        page.locator(".newrun-ref img").wait_for()
        page.get_by_role("button", name="Kora").click()
        assert page.locator("#newrun-brief").input_value() == "Calabash harp."
        # The kora has no reference image on disk: no broken image, just the drop zone.
        page.wait_for_function("() => !document.querySelector('.newrun-ref img')")
        assert page.get_by_role("button", name="Run now").is_disabled()
        assert not [r for r in requests if "/api/competitions" in r or "/run-now" in r]
        assert problems == []
        context.close()
        browser.close()


def test_add_to_tonight_with_queue_writes_off_saves_a_draft(studio: str, repo: Path):
    queue = repo / "runs" / "nightly-cad-queue.json"
    before = queue.read_bytes()
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context, page, problems, _ = _open(browser, studio)
        page.get_by_role("button", name="Overnight matchup").click()
        assert "Queue writes are off" in page.locator(".newrun-lights").inner_text()
        page.get_by_role("button", name="Add to tonight").click()
        dialog = page.locator("dialog.review-dialog")
        dialog.wait_for()
        assert "Saved as a draft" in dialog.inner_text()
        assert "nightly queue was not changed" in dialog.inner_text()
        assert queue.read_bytes() == before
        drafts = list((repo / "runs" / "control-room" / "drafts").glob("*.json"))
        assert len(drafts) == 1
        jobs = json.loads(drafts[0].read_text())["jobs"]
        assert [e["entrant_id"] for e in jobs[0]["entrants"]] == ["cadam-fable", "freecad-opus", "solidworks-sol", "fusion-sonnet"]
        assert problems == []
        context.close()
        browser.close()


@pytest.mark.parametrize("studio", [{"control_room_queue_writes": True}], indirect=True)
def test_queue_writes_on_stage_what_the_runner_cannot_apply_and_queue_the_rest(studio: str, repo: Path):
    queue = repo / "runs" / "nightly-cad-queue.json"
    before = queue.read_bytes()
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context, page, problems, _ = _open(browser, studio)
        page.get_by_role("button", name="Assembly build").click()
        gaps = page.get_by_test_id("runner-gaps")
        gaps.wait_for()
        assert "wood-assembly mode" in gaps.inner_text() and "6 turns" not in gaps.inner_text()
        assert "staged, not queued" in gaps.inner_text()
        page.get_by_role("button", name="Add to tonight").click()
        dialog = page.locator("dialog.review-dialog")
        dialog.locator("h2", has_text="Staged, not queued").wait_for()
        assert "was not queued" in dialog.inner_text()
        assert queue.read_bytes() == before
        dialog.get_by_role("button", name="Done").click()

        # The same night with only what the runner applies: one turn, the registry brief, single part.
        page.get_by_role("button", name="Overnight matchup").click()
        page.locator('input[type="range"]').fill("1")
        page.wait_for_function("() => !document.querySelector('[data-testid=runner-gaps]')")
        page.get_by_role("button", name="Add to tonight").click()
        dialog.locator("h2", has_text="Added to tonight").wait_for()
        _payload, jobs = nightly_cad.load_queue(queue)
        assert [job.instrument_id for job in jobs] == ["kora", "sambuca"]
        assert problems == []
        context.close()
        browser.close()


@pytest.mark.parametrize("studio", [{"allow_live": True}], indirect=True)
def test_run_now_shows_a_launch_refusal(studio: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context, page, problems, _ = _open(browser, studio)
        page.get_by_role("button", name="Overnight matchup").click()
        for pick in ("cadam:fable", "solidworks:sol", "fusion:sonnet"):
            _cell(page, pick).click()
        _cell(page, "freecad:sol").click()
        page.locator('input[type="range"]').fill("1")  # Run now gives each entrant one attempt
        page.get_by_role("button", name="Run now").click()
        # The fixture's reference image is not approved, so the real launcher refuses (no process starts).
        page.locator(".review-toast", has_text="Not started").wait_for()
        assert page.locator("dialog.review-dialog[open]").count() == 0
        assert problems == []
        context.close()
        browser.close()


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [1440, 390])
def test_layout_screens(studio: str, screenshot_dir: Path, color_scheme: str, width: int):
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context, page, problems, _ = _open(browser, studio, viewport={"width": width, "height": 1100}, color_scheme=color_scheme)
        page.get_by_role("button", name="Overnight matchup").click()
        page.wait_for_function("() => document.querySelector('[data-testid=spend]').textContent.includes('$')")
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(screenshot_dir / f"newrun-{width}-{color_scheme}.png"), full_page=True)
        assert problems == []
        context.close()
        browser.close()


@pytest.mark.parametrize("studio", [{"allow_live": True}], indirect=True)
def test_run_now_is_disabled_for_settings_it_cannot_carry(studio: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context, page, problems, _ = _open(browser, studio)
        page.get_by_role("button", name="Overnight matchup").click()
        for pick in ("cadam:fable", "solidworks:sol", "fusion:sonnet"):
            _cell(page, pick).click()
        _cell(page, "freecad:sol").click()
        page.wait_for_function("() => document.querySelector('[data-testid=spend]').textContent.includes('$')")
        # Five turns (the preset) is itself something Run now cannot carry.
        page.get_by_test_id("run-now-hint").wait_for()
        assert "5 turns" in page.get_by_test_id("run-now-hint").inner_text()
        assert page.get_by_role("button", name="Run now").is_disabled()
        page.locator('input[type="range"]').fill("1")
        page.wait_for_function("() => !document.querySelector('[data-testid=run-now-hint]')")
        assert page.get_by_role("button", name="Run now").is_enabled()
        page.locator("#newrun-brief").fill("Boat body, but taller.")
        hint = page.get_by_test_id("run-now-hint")
        hint.wait_for()
        assert "use Add to tonight for custom settings" in hint.inner_text() and "edited brief" in hint.inner_text()
        assert page.get_by_role("button", name="Run now").is_disabled()

LIBRARY_PAGE = (Path(__file__).parent / "fixtures" / "control_room" / "library.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("studio", [{"library_fetch": lambda: LIBRARY_PAGE}], indirect=True)
def test_library_picker_refreshes_filters_and_searches(studio: str, screenshot_dir: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context, page, problems, _ = _open(browser, studio)
        assert "No library snapshot yet" in page.get_by_test_id("library-status").inner_text()
        page.get_by_role("button", name="Refresh from the library").click()
        page.locator("[data-testid=library-status]", has_text="From the instrument library, refreshed").wait_for()
        cards = page.locator(".newrun-instrument")
        assert cards.count() == 5
        assert "Strings · harp-lute · 3 variants" in page.locator('[data-instrument="kora"]').inner_text()
        assert "Arena task" in page.locator('[data-instrument="kora"]').inner_text()
        assert "No arena task" in page.locator('[data-instrument="bansuri"]').inner_text()
        assert "Glass Harp <Starter>" in page.locator('[data-instrument="glass-harp"]').inner_text()

        page.get_by_label("CAD", exact=True).select_option("none")
        page.wait_for_function("() => document.querySelectorAll('.newrun-instrument').length === 4")
        assert page.locator('[data-instrument="handpan"]').count() == 0
        page.get_by_label("Search").fill("flute")
        page.wait_for_function("() => document.querySelectorAll('.newrun-instrument').length === 1")
        page.locator('[data-instrument="bansuri"]').click()
        assert page.locator('[data-instrument="bansuri"]').get_attribute("aria-pressed") == "true"
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(screenshot_dir / "newrun-library-1440-light.png"), full_page=True)
        assert problems == []
        context.close()
        browser.close()


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
@pytest.mark.parametrize("studio", [{"library_fetch": lambda: LIBRARY_PAGE}], indirect=True)
def test_library_picker_phone(studio: str, screenshot_dir: Path, color_scheme: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context, page, problems, _ = _open(browser, studio, viewport={"width": 390, "height": 1100}, color_scheme=color_scheme)
        page.get_by_role("button", name="Refresh from the library").click()
        page.locator("[data-testid=library-status]", has_text="refreshed").wait_for()
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(screenshot_dir / f"newrun-library-390-{color_scheme}.png"), full_page=True)
        assert problems == []
        context.close()
        browser.close()


@pytest.mark.parametrize("live", ["solidworks", "fusion"])
@pytest.mark.parametrize("studio", [{"allow_live": True}], indirect=True)
def test_run_now_is_disabled_when_a_live_cad_cell_is_ticked(studio: str, live: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context, page, problems, _ = _open(browser, studio)
        page.get_by_role("button", name="Overnight matchup").click()
        for pick in ("cadam:fable", "solidworks:sol", "fusion:sonnet"):
            _cell(page, pick).click()
        _cell(page, "freecad:sol").click()
        page.locator('input[type="range"]').fill("1")
        page.wait_for_function("() => !document.querySelector('[data-testid=run-now-hint]')")
        assert page.get_by_role("button", name="Run now").is_enabled()
        _cell(page, f"{live}:opus").click()
        hint = page.get_by_test_id("run-now-hint")
        hint.wait_for()
        assert "Live CAD runs are scheduled through Add to tonight (one CAD seat, supervised)" in hint.inner_text()
        assert page.get_by_role("button", name="Run now").is_disabled()
        assert problems == []
        context.close()
        browser.close()

def test_variant_chips_come_from_the_instrument_repo(repo: Path, tmp_path: Path, screenshot_dir: Path):
    from test_control_room_variants import build_instruments_root

    root = build_instruments_root(tmp_path / "instruments")
    server, thread, url = _serve(repo, instruments_root=root)
    try:
        with sync_playwright() as playwright:
            browser = _launch(playwright)
            context, page, problems, _ = _open(browser, url)
            page.locator('[data-instrument="kora"]').click()
            page.locator("[data-testid=variant-source]", has_text="From family-spec.csv").wait_for()
            chips = page.locator(".newrun-variant")
            assert chips.count() == 2
            assert "root prototype" in chips.first.get_attribute("title")
            chips.nth(0).click()
            chips.nth(1).click()
            page.get_by_role("button", name="Overnight matchup").click()  # resets the form...
            page.locator('[data-instrument="kora"]').click()  # ...and back to kora, with no variants picked
            page.locator(".newrun-variant").first.wait_for()
            page.locator(".newrun-variant").nth(0).click()
            page.locator(".newrun-variant").nth(1).click()
            page.wait_for_function("() => document.querySelector('[data-testid=newrun-summary]').textContent.includes('8 designs')")
            assert "KORA-21-ROOT, KORA-13-STUDY" in page.get_by_test_id("newrun-summary").inner_text()
            page.screenshot(path=str(screenshot_dir / "newrun-variants-1440-light.png"), full_page=True)

            page.locator('[data-instrument="sambuca"]').click()  # no repo: back to free text
            page.locator("[data-testid=variant-source]", has_text="No local repo found").wait_for()
            assert page.get_by_label("Variants to sweep (optional, comma-separated)").is_visible()
            assert problems == []
            context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_brief_and_references_fill_from_the_instrument_repo(repo: Path, tmp_path: Path, screenshot_dir: Path):
    from test_control_room_variants import build_instruments_root

    root = build_instruments_root(tmp_path / "instruments")
    server, thread, url = _serve(repo, instruments_root=root)
    try:
        with sync_playwright() as playwright:
            browser = _launch(playwright)
            context, page, problems, _ = _open(browser, url)
            page.locator('[data-instrument="kora"]').click()
            page.get_by_test_id("brief-filled").wait_for()
            brief = page.locator("#newrun-brief")
            assert brief.input_value().startswith("Calabash harp.")
            assert "bowl diameter in 20.3" in brief.input_value()
            refs = page.locator(".newrun-ref", has_text="From the repo")
            assert refs.count() == 2
            page.wait_for_function("() => [...document.querySelectorAll('.newrun-ref img')].every(img => img.complete && img.naturalWidth > 0)")
            provenance = page.get_by_test_id("brief-provenance")
            provenance.locator("summary").click()
            assert "capstone-manifest.json › engineering.bowl_diameter_in" in provenance.inner_text()
            assert "data/string-schedule.csv" in provenance.inner_text()
            assert "price" not in provenance.inner_text().lower()

            brief.fill("Kora with a deeper bowl.")
            page.locator(".newrun-variant").first.click()  # a new suggestion arrives for the variant...
            page.wait_for_timeout(500)
            assert brief.input_value() == "Kora with a deeper bowl."  # ...but never overwrites an edit
            page.screenshot(path=str(screenshot_dir / "newrun-brief-1440-light.png"), full_page=True)

            page.get_by_role("button", name="Add to tonight").click()
            page.locator("dialog.review-dialog", has_text="Saved as a draft").wait_for()
            plan = json.loads(next((repo / "runs" / "control-room" / "plans").glob("*.json")).read_text())
            assert plan["brief"] == "Kora with a deeper bowl." and plan["reference_images"] == ["reference-1.png", "reference-2.png"]

            assert problems == []
            context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize("width", [1440, 390])
def test_fill_the_gaps_orders_nights_and_queues_a_draft(repo: Path, tmp_path: Path, screenshot_dir: Path, width: int):
    from test_control_room_variants import build_instruments_root

    root = build_instruments_root(tmp_path / "instruments")
    server, thread, url = _serve(repo, instruments_root=root, library_fetch=lambda: LIBRARY_PAGE)
    try:
        with sync_playwright() as playwright:
            browser = _launch(playwright)
            context, page, problems, _ = _open(browser, url, viewport={"width": width, "height": 1100})
            assert page.get_by_role("button", name="Fill the gaps").is_disabled()  # needs the library
            page.get_by_role("button", name="Refresh from the library").click()
            page.locator("[data-testid=library-status]", has_text="refreshed").wait_for()
            page.get_by_role("button", name="Fill the gaps").click()
            assert "Explorers complete 0 of 5" in page.get_by_test_id("gaps-progress").inner_text()
            nights = page.locator(".newrun-nights li")
            assert nights.evaluate_all("rows => rows.map(r => r.dataset.night)") == ["kora", "cajon", "bansuri", "glass-harp"]
            page.get_by_role("button", name="Move Cajon Box Drum earlier").click()
            page.get_by_role("button", name="Remove Glass Harp <Starter>").click()
            page.wait_for_function("() => document.querySelectorAll('.newrun-nights li').length === 3")
            assert nights.evaluate_all("rows => rows.map(r => r.dataset.night)") == ["cajon", "kora", "bansuri"]
            page.get_by_label("Gap family").select_option("string")
            page.wait_for_function("() => document.querySelectorAll('.newrun-nights li').length === 1")
            page.get_by_label("Gap family").select_option("all")
            later = (date.today() + timedelta(days=3)).isoformat()
            page.get_by_label("First night").fill(later)
            page.get_by_test_id("gaps-start-note").wait_for()  # the runner has no start date: staged, not queued
            assert f"#1 · {later}" in nights.first.inner_text()
            assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(screenshot_dir / f"newrun-gaps-{width}-light.png"), full_page=True)

            page.get_by_role("button", name="Queue 4 nights").click()
            dialog = page.locator("dialog.review-dialog")
            dialog.locator("h2", has_text="Saved as a draft").wait_for()
            assert "Skipped:" in dialog.inner_text() and "Planned order: kora." in dialog.inner_text()
            assert "no night is booked to a date" in dialog.inner_text()
            assert problems == []
            context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_a_slow_brief_suggestion_never_overwrites_what_was_typed(repo: Path, tmp_path: Path):
    from test_control_room_variants import build_instruments_root

    root = build_instruments_root(tmp_path / "instruments")
    server, thread, url = _serve(repo, instruments_root=root)
    try:
        with sync_playwright() as playwright:
            browser = _launch(playwright)
            context, page, problems, _ = _open(browser, url)
            held = []
            page.route("**/api/newrun/instrument/kora/brief*", lambda route: held.append(route))
            page.locator('[data-instrument="kora"]').click()
            page.wait_for_function("() => true")
            brief = page.locator("#newrun-brief")
            brief.fill("Kora typed while the suggestion loaded.")
            page.wait_for_timeout(200)
            assert held, "the brief request was not held"
            held[0].continue_()
            page.get_by_test_id("brief-filled").wait_for()
            assert brief.input_value() == "Kora typed while the suggestion loaded."
            assert page.locator(".newrun-ref", has_text="From the repo").count() == 2  # the images still arrive
            assert problems == []
            context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_a_mixed_fill_gaps_batch_shows_what_queued_and_what_was_staged(tmp_path: Path):
    from test_control_room_fill_gaps import link_assembly_to_cajon
    from test_control_room_variants import build_instruments_root

    (tmp_path / "repo").mkdir()
    repo = build_newrun_repo(tmp_path / "repo")
    link_assembly_to_cajon(repo)
    root = build_instruments_root(tmp_path / "instruments")
    server, thread, url = _serve(repo, instruments_root=root, library_fetch=lambda: LIBRARY_PAGE, control_room_queue_writes=True)
    try:
        with sync_playwright() as playwright:
            browser = _launch(playwright)
            context, page, problems, _ = _open(browser, url)
            page.get_by_role("button", name="Refresh from the library").click()
            page.locator("[data-testid=library-status]", has_text="refreshed").wait_for()
            page.get_by_role("button", name="Fill the gaps").click()
            nights = page.locator(".newrun-nights li")
            for name in ("Bansuri", "Glass Harp <Starter>"):
                page.get_by_role("button", name=f"Remove {name}", exact=False).first.click()
            page.wait_for_function("() => document.querySelectorAll('.newrun-nights li').length === 2")
            assert nights.evaluate_all("rows => rows.map(r => r.dataset.night)") == ["kora", "tongue_drum_wood_assembly"]
            page.get_by_role("button", name="Queue 2 nights").click()
            dialog = page.locator("dialog.review-dialog")
            dialog.locator("h2", has_text="Some nights queued, some staged").wait_for()
            assert "Queued in this order: kora." in dialog.get_by_test_id("gaps-order").inner_text()
            staged = dialog.get_by_test_id("gaps-staged").inner_text()
            assert "tongue_drum_wood_assembly: the wood-assembly mode" in staged and "runs/control-room/" in staged
            assert problems == []
            context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
