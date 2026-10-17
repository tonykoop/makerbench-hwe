"""Real-browser checks for the Control Room Morning Review (#1105, Playwright, opt-in).

- Ranking: ▲▼ buttons, drag and drop, and reset to the grader's order.
- Notes persist across a reload (local draft) and after Save (morning-review.json).
- Save writes the review schema; revision needs a note; the nightly queue is untouched
  while queue writes are off.
- A failing entrant is flagged and explained; hostile run text stays inert.
- The assembly night shows its parts and cut list.
- "Open in <app>" asks the server, which opens only the recorded native file.
- The no-JS fallback lists designs and links. 1440 px and 390 px, light and dark.

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
pytest.importorskip("trimesh")

from control_room_fixture import DRUM_RUN, SAMBUCA_RUN, build_control_room_repo  # noqa: E402

from makerbench.arena_studio import create_studio_app  # noqa: E402 - after the optional-dependency guards

CADAM = "sambuca__seed0__rep0__cadam-fable"
FREECAD = "sambuca__seed0__rep0__freecad-opus"
SOLIDWORKS = "sambuca__seed0__rep0__solidworks-sol"
FUSION = "sambuca__seed0__rep0__fusion-sonnet"
CLIPPED = """() => [...document.querySelectorAll('.review-body')]
  .filter(body => body.scrollWidth > body.clientWidth + 1)
  .map(body => body.closest('.review-card').dataset.trial)"""
WEBGL_MODES = {"webgl": [], "zero-webgl": ["--disable-3d-apis", "--disable-webgl", "--disable-webgl2"]}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def review_repo(tmp_path: Path) -> Path:
    repo = build_control_room_repo(tmp_path)
    (repo / "runs" / "nightly-cad-queue.json").write_text(
        json.dumps({"schema": "makerbench-nightly-cad-queue-v1", "jobs": []}), encoding="utf-8"
    )
    return repo


@pytest.fixture
def opened() -> list[Path]:
    return []


@pytest.fixture
def studio_url(review_repo: Path, opened: list[Path]):
    app = create_studio_app(registry_path=review_repo / "registry.json", repo_root=review_repo, native_opener=opened.append)
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


def _launch(playwright, mode: str = "zero-webgl"):
    try:
        return playwright.chromium.launch(args=[*WEBGL_MODES[mode], *os.environ.get("ARENA_STUDIO_BROWSER_ARGS", "").split()])
    except Exception as error:
        if REQUIRE_BROWSER:
            raise
        pytest.skip(f"chromium is not available: {error}")


def _open(browser, url: str, **context_args):
    context = browser.new_context(**{"viewport": {"width": 1440, "height": 1000}, **context_args})
    page = context.new_page()
    problems: list[str] = []
    page.on("console", lambda msg: problems.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda error: problems.append(str(error)))
    page.goto(url)
    page.locator(".review-card").first.wait_for()
    return context, page, problems


def _order(page) -> list[str]:
    return page.locator(".review-card").evaluate_all("cards => cards.map(card => card.dataset.trial)")


def _expect_order(page, expected: list[str]) -> None:
    try:
        page.wait_for_function(
            "want => JSON.stringify([...document.querySelectorAll('.review-card')].map(c => c.dataset.trial)) === want",
            arg=json.dumps(expected, separators=(",", ":")), timeout=5000,
        )
    except Exception:
        assert _order(page) == expected
        raise


def _card(page, trial_id: str):
    return page.locator(f'.review-card[data-trial="{trial_id}"]')


def test_ranking_buttons_drag_and_reset(studio_url: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        # Tall enough that every card is on screen: a drag that has to scroll starts elsewhere.
        context, page, problems = _open(browser, f"{studio_url}/#/review/{SAMBUCA_RUN}", viewport={"width": 1440, "height": 2400})
        # Grader order: every-check passes first, the error row last.
        _expect_order(page, [CADAM, FREECAD, SOLIDWORKS, FUSION])
        assert _card(page, CADAM).get_by_role("button", name="Move CADAM up").is_disabled()

        _card(page, SOLIDWORKS).get_by_role("button", name="Move SolidWorks up").click()
        _expect_order(page, [CADAM, SOLIDWORKS, FREECAD, FUSION])
        # Keyboard: the ▼ button works from the keyboard too.
        _card(page, CADAM).get_by_role("button", name="Move CADAM down").focus()
        page.keyboard.press("Enter")
        _expect_order(page, [SOLIDWORKS, CADAM, FREECAD, FUSION])

        _card(page, FUSION).locator(".review-rank-n").drag_to(_card(page, SOLIDWORKS).locator(".review-rank-n"))
        _expect_order(page, [FUSION, SOLIDWORKS, CADAM, FREECAD])
        assert "Fusion › SolidWorks › CADAM › FreeCAD" in page.get_by_test_id("tray").inner_text()

        page.get_by_role("button", name="Reset ranking").click()
        _expect_order(page, [CADAM, FREECAD, SOLIDWORKS, FUSION])
        assert problems == []
        context.close()
        browser.close()


def test_failing_entrant_is_flagged_and_explained(studio_url: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context, page, problems = _open(browser, f"{studio_url}/#/review/{SAMBUCA_RUN}")
        summary = page.get_by_test_id("review-summary").inner_text()
        assert "4 designs" in summary and "2 pass every check" in summary and "$0.31" in summary and "15 turns" in summary
        solid = _card(page, SOLIDWORKS)
        assert solid.locator(".review-fail-flag").inner_text().lower() == "fails a check"
        assert solid.locator('.check-chip[data-state="fail"]').inner_text().startswith("fits_envelope")
        assert solid.locator(".review-fail-note").inner_text() == "The neck measures 1412 mm; the envelope allows 1300 mm."
        assert solid.locator(".review-turn").count() == 6
        assert "14 parameters" in solid.locator(".review-stats").inner_text()
        assert "subscription" in solid.locator(".review-stats").inner_text()
        assert _card(page, CADAM).locator(".review-fail-flag").count() == 0
        assert "$0.31" in _card(page, CADAM).locator(".review-stats").inner_text()
        assert _card(page, CADAM).locator('.check-chip[data-state="pass"]').count() == 5
        fusion = _card(page, FUSION)
        assert "No graded design" in fusion.locator(".review-fail-note").inner_text()
        assert '<img src=x onerror="window.__pwned=1">' in fusion.locator(".review-fail-note").inner_text()
        assert page.evaluate("() => window.__pwned") is None
        assert "Sambuca from the museum photo" == page.locator("h1").inner_text()
        page.locator(".review-brief img").wait_for()
        assert problems == []
        context.close()
        browser.close()


def test_notes_persist_and_save_writes_the_review_schema(studio_url: str, review_repo: Path):
    run_dir = review_repo / "runs" / "nightly" / SAMBUCA_RUN
    queue = review_repo / "runs" / "nightly-cad-queue.json"
    queue_before = queue.read_bytes()
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context, page, problems = _open(browser, f"{studio_url}/#/review/{SAMBUCA_RUN}")
        solid = _card(page, SOLIDWORKS)
        solid.get_by_role("button", name="Revise with my notes").click()
        assert "Write a note first" in page.locator(".review-toast").inner_text()
        assert solid.get_by_role("button", name="Revise with my notes").get_attribute("aria-pressed") == "false"

        solid.locator("textarea").fill("Neck is too tall; bring it under 1300 mm.")
        _card(page, CADAM).locator("textarea").fill("Pegs too small.")
        solid.get_by_role("button", name="Revise with my notes").click()
        assert solid.get_by_role("button", name="Marked for revision").get_attribute("aria-pressed") == "true"
        _card(page, SOLIDWORKS).get_by_role("button", name="Move SolidWorks up").click()
        _expect_order(page, [CADAM, SOLIDWORKS, FREECAD, FUSION])

        # Reload before saving: the draft keeps ranking, notes and the request.
        page.reload()
        page.locator(".review-card").first.wait_for()
        _expect_order(page, [CADAM, SOLIDWORKS, FREECAD, FUSION])
        assert _card(page, SOLIDWORKS).locator("textarea").input_value() == "Neck is too tall; bring it under 1300 mm."
        assert "2 notes" in page.get_by_test_id("tray").inner_text()
        assert not (run_dir / "morning-review.json").exists()

        page.get_by_role("button", name="Save review").click()
        dialog = page.get_by_test_id("save-dialog")
        dialog.wait_for()
        assert "1 revision request saved for the revision runner" in dialog.inner_text()
        assert "#1116" in dialog.inner_text() and "tonight" not in dialog.inner_text()
        assert "Queue writes are off" in dialog.inner_text()
        assert not (review_repo / "runs" / "control-room").exists()
        saved = json.loads((run_dir / "morning-review.json").read_text(encoding="utf-8"))
        assert saved["schema"] == "makerbench-control-room-morning-review-v1"
        assert set(saved) >= {"run_id", "night", "instrument_id", "reviewer", "saved_utc", "grader_order", "ranking",
                              "notes", "revision_requests", "next_night_jobs", "morning_vote", "queued"}
        assert saved["ranking"] == [CADAM, SOLIDWORKS, FREECAD, FUSION]
        assert saved["notes"] == {CADAM: "Pegs too small.", SOLIDWORKS: "Neck is too tall; bring it under 1300 mm."}
        assert saved["revision_requests"] == [{
            "trial_id": SOLIDWORKS, "entrant": "solidworks-sol", "from_turn": 6, "extra_turns": 3,
            "feedback": "Neck is too tall; bring it under 1300 mm.",
        }]
        assert saved["next_night_jobs"][0]["revision_of"]["trial_id"] == SOLIDWORKS
        assert saved["morning_vote"]["pair_files"] == ["morning-vote/pair-000.html"]
        assert saved["queued"] is False
        assert queue.read_bytes() == queue_before
        dialog.get_by_role("button", name="Done").click()

        # A fresh browser (no draft) restores the saved review from the run folder.
        context.close()
        context, page, problems2 = _open(browser, f"{studio_url}/#/review/{SAMBUCA_RUN}")
        _expect_order(page, [CADAM, SOLIDWORKS, FREECAD, FUSION])
        assert _card(page, CADAM).locator("textarea").input_value() == "Pegs too small."
        assert _card(page, SOLIDWORKS).locator(".review-revise").get_attribute("aria-pressed") == "true"
        assert problems == [] and problems2 == []
        context.close()
        browser.close()


def test_open_in_app_and_assembly_night(studio_url: str, opened: list[Path]):
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context, page, problems = _open(browser, f"{studio_url}/#/review/{SAMBUCA_RUN}")
        _card(page, FREECAD).get_by_role("button", name="Open sambuca.FCStd in FreeCAD").click()
        page.locator(".review-toast", has_text="Opening sambuca.FCStd in FreeCAD.").wait_for()
        assert [path.name for path in opened] == ["sambuca.FCStd"]
        assert _card(page, FUSION).get_by_text("No native file recorded.").count() == 1

        page.locator(".review-night", has_text="Tongue drum").click()
        page.locator("h1", has_text="Tongue drum v2, wood assembly").wait_for()
        assembly = page.locator(".review-assembly").first
        assembly.locator("summary").click()
        assert "Parts (6) and cut list (6 rows)" in assembly.locator("summary").inner_text()
        assert assembly.locator("tbody tr").count() == 6
        assert "padauk" in assembly.inner_text()
        assert problems == []
        context.close()
        browser.close()


def test_no_js_fallback_lists_designs_and_links(studio_url: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        context = browser.new_context(java_script_enabled=False)
        page = context.new_page()
        page.goto(studio_url + "/")
        page.get_by_role("link", name="Morning Review list").click()
        page.get_by_role("link", name="Sambuca from the museum photo").click()
        items = page.locator("main > ol > li")
        assert items.count() == 4
        assert "fails a check" in items.nth(2).inner_text()
        assert page.get_by_role("link", name="sambuca.SLDPRT").count() == 1
        response = page.request.get(studio_url + page.get_by_role("link", name="3D mesh (GLB)").first.get_attribute("href"))
        assert response.status == 200 and response.headers["content-type"] == "model/gltf-binary"
        context.close()
        browser.close()


@pytest.mark.parametrize("mode", sorted(WEBGL_MODES))
@pytest.mark.parametrize("color_scheme", ["light", "dark"])
@pytest.mark.parametrize("width", [1440, 390])
def test_layout_screens(studio_url: str, screenshot_dir: Path, mode: str, color_scheme: str, width: int):
    with sync_playwright() as playwright:
        browser = _launch(playwright, mode)
        context, page, problems = _open(
            browser, f"{studio_url}/#/review/{SAMBUCA_RUN}", viewport={"width": width, "height": 1000}, color_scheme=color_scheme
        )
        page.wait_for_load_state("networkidle")
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        assert page.locator(".review-tray").is_visible()
        assert page.evaluate(CLIPPED) == []
        page.screenshot(path=str(screenshot_dir / f"review-{width}-{color_scheme}-{mode}.png"), full_page=True)
        page.goto(f"{studio_url}/#/review/{DRUM_RUN}")
        page.locator("h1", has_text="Tongue drum").wait_for()
        page.locator(".review-assembly summary").first.click()
        page.wait_for_load_state("networkidle")
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        # Cards clip their overflow, so check that nothing inside a card is wider than it.
        assert page.evaluate(CLIPPED) == []
        page.screenshot(path=str(screenshot_dir / f"review-assembly-{width}-{color_scheme}-{mode}.png"), full_page=True)
        # model-viewer may log GPU noise under zero-WebGL; the page itself must not error.
        assert [p for p in problems if "WebGL" not in p and "GPU" not in p] == []
        context.close()
        browser.close()


def test_promote_to_library_confirms_then_commits_a_local_branch(tmp_path: Path, screenshot_dir: Path):
    from test_control_room_promote import _git, build_scratch_instrument

    repo = build_control_room_repo(tmp_path / "studio")
    instrument, origin = build_scratch_instrument(tmp_path / "instruments")
    app = create_studio_app(registry_path=repo / "registry.json", repo_root=repo, instruments_root=tmp_path / "instruments")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    try:
        with sync_playwright() as playwright:
            browser = _launch(playwright)
            context, page, problems = _open(browser, f"http://127.0.0.1:{port}/#/review/{SAMBUCA_RUN}")
            _card(page, FREECAD).get_by_role("button", name="Promote to library").click()
            dialog = page.get_by_test_id("promote-dialog")
            dialog.get_by_role("heading", name="Promote FreeCAD's design?").wait_for()
            assert "Scores and the other entrants' designs stay in MakerBench" in dialog.inner_text()
            dialog.get_by_role("button", name="Cancel").click()
            assert _git(instrument, "branch", "--list", "makerbench/*") == ""

            _card(page, FREECAD).get_by_role("button", name="Promote to library").click()
            dialog.get_by_role("button", name="Promote").click()
            page.get_by_test_id("promoted").wait_for()
            assert "not pushed" in page.get_by_test_id("promoted").inner_text()
            assert "freecad-opus" in _git(instrument, "branch", "--list", "makerbench/*")
            assert _git(origin, "branch", "--list", "makerbench/*") == ""
            page.screenshot(path=str(screenshot_dir / "review-promoted-1440-light.png"), full_page=False)
            assert problems == []
            context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
