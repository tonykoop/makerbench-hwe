"""Full-flow real-browser journey through every rebuilt Arena Studio screen (F7).

One Studio server, one repo holding data for every screen, one browser session
walking the rail in the order a person would on a real evening:

  Runs -> Blind voting (vote by keyboard) -> Agreement analytics -> Launch (a
  zero-token dry run) -> Compare runs -> DoE matrix -> Nightly cockpit ->
  Morning review (vote by keyboard) -> Runs again.

The per-screen browser files (F1-F6) prove each screen in depth; this file
proves they compose: the vote cast on one screen shows up in analytics, the run
launched on one screen shows up on Runs, the rail keeps the chosen run and
moves focus, no model identity reaches either blind screen before its vote, and
the whole journey logs zero console errors.

Opt-in like the other browser files: skipped unless Playwright and Chromium are
installed; the `studio-browser` CI job sets ARENA_STUDIO_REQUIRE_BROWSER=1.
Set ARENA_STUDIO_SCREENSHOTS=<dir> to keep the screenshot set.
"""

from __future__ import annotations

import json
import math
import os
import re
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
trimesh = pytest.importorskip("trimesh")

from PIL import Image, ImageDraw  # noqa: E402 - after the optional-dependency guards

from makerbench import cli_arena  # noqa: E402 - after the optional-dependency guards
from makerbench.arena_studio import create_studio_app  # noqa: E402 - after the optional-dependency guards
from makerbench.arena_studio.service import ArenaStudioService  # noqa: E402 - after the optional-dependency guards

RUN_ID = "e2e-round"
RUN_B = "e2e-round-b"
DRY_RUN = "e2e-dry-run"
JOB = "sambuca-night"
# The blind run and the morning bundle use different identities, so each blind
# screen's "nothing before the vote" check can't be satisfied by the other's reveal.
RUN_IDENTITY = ("model-a", "model-b", "t-1", "t-2")
MORNING_IDENTITY = ("model-m1", "model-m2", "m-1", "m-2")
SUBSCRIPTION = "claude-code-opus-5, codex-gpt-5.6"
# The rail, in order, exactly as main.js registers it.
RAIL = [
    ("runs", "Runs", True),
    ("vote", "Blind voting", True),
    ("nightly", "Nightly cockpit", False),
    ("morning", "Morning review", False),
    ("launch", "Launch", False),
    ("analytics", "Agreement analytics", True),
    ("compare", "Compare runs", False),
    ("doe", "DoE matrix", False),
]
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


def _fake_turntable_frames(stl_path, pair_hint, side, vote_pages, frames=24, **_ignored):
    """Stand-in renderer: 24 real PNG frames, blind-aliased like cli_arena's renders."""
    blind = vote_pages / "blind"
    blind.mkdir(parents=True, exist_ok=True)
    rel = []
    for index in range(frames):
        image = Image.new("RGB", (240, 240), (42, 47, 51))
        draw = ImageDraw.Draw(image)
        angle = 2 * math.pi * index / frames
        draw.line([(120, 120), (120 + 90 * math.cos(angle), 120 + 90 * math.sin(angle))], fill=(232, 234, 236), width=12)
        name = f"{pair_hint}-{side}-f{index:02d}.png"
        image.save(blind / name)
        rel.append(f"blind/{name}")
    return tuple(rel)


def _votable_run(run_dir: Path, instrument: str, identity: tuple[str, str, str, str]) -> Path:
    model_a, model_b, trial_a, trial_b = identity
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    trials = []
    for trial_id, model_id, color, pass_rate in (
        (trial_a, model_a, (176, 126, 64), 1.0),
        (trial_b, model_b, (64, 136, 176), 0.5),
    ):
        png = artifacts / f"{trial_id}.png"
        Image.new("RGB", (320, 320), color).save(png)
        stl = artifacts / f"{trial_id}.stl"
        trimesh.creation.box(extents=(10, 10, 10)).export(stl)
        trials.append(
            {
                "trial_id": trial_id,
                "model_id": model_id,
                "instrument_id": instrument,
                "seed": 0,
                "rep": 0,
                "status": "completed",
                "result": {
                    "render_ok": True,
                    "artifacts": {"png_path": str(png), "stl_path": str(stl)},
                    "objective": {"objective_pass_rate": pass_rate, "passed": pass_rate == 1.0},
                },
            }
        )
    run_log = {
        "started_at": "2026-09-14T21:30:00Z",
        "config": {"model_ids": [model_a, model_b], "instruments": [instrument]},
        "trials": trials,
    }
    (run_dir / "run_log.json").write_text(json.dumps(run_log), encoding="utf-8")
    return run_dir


def _voted_run(run_dir: Path) -> Path:
    """A second, already-voted run for Compare: model-a is rated in both runs."""
    run_dir.mkdir(parents=True)
    trials = [
        {"trial_id": f"b-{i}", "model_id": model, "instrument_id": "ocarina", "seed": 0, "rep": 0, "status": "completed",
         "result": {"objective": {"objective_pass_rate": rate}}}
        for i, (model, rate) in enumerate((("model-a", 1.0), ("model-c", 0.25)))
    ]
    (run_dir / "run_log.json").write_text(
        json.dumps({"started_at": "2026-09-13T21:30:00Z", "config": {"model_ids": ["model-a", "model-c"]}, "trials": trials}),
        encoding="utf-8",
    )
    reveal = {"left": {"model_id": "model-a"}, "right": {"model_id": "model-c"}}
    (run_dir / "votes.revealed.jsonl").write_text(
        json.dumps({"pair_id": "b-p0", "winner": "left", "voter_id": "tony", "instrument_id": "ocarina", "seed": 0, "reveal": reveal})
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "votes.blind.jsonl").write_text(
        json.dumps({"pair_id": "b-p0", "winner": "left", "voter_id": "tony"}) + "\n", encoding="utf-8"
    )
    return run_dir


@pytest.fixture
def studio_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "makerbench-code-cad-arena-registry-v1",
                "instruments": [
                    {"id": "ocarina", "display_name": "Ocarina", "family": "woodwind", "task_kind": "single_part_vessel",
                     "envelope_mm": [140, 90, 70]},
                    {"id": "kora", "display_name": "Kora", "family": "strings", "task_kind": "harp_lute",
                     "envelope_mm": [1200, 350, 250]},
                ],
            }
        ),
        encoding="utf-8",
    )
    for task_id, color in (("ocarina", (176, 126, 64)), ("kora", (64, 136, 176))):
        image = tmp_path / "tasks" / task_id / "reference.png"
        image.parent.mkdir(parents=True)
        Image.new("RGB", (480, 320), color).save(image)
    # ocarina's reference is approved; kora's is not, so DoE predicts kora as skipped.
    ArenaStudioService(registry_path=registry, repo_root=tmp_path).set_task_approval("ocarina", True)

    arena = tmp_path / "runs" / "code_cad_arena"
    _votable_run(arena / RUN_ID, "ocarina", RUN_IDENTITY)
    _voted_run(arena / RUN_B)

    runs = tmp_path / "runs"
    morning = _votable_run(runs / "morning_run", "sambuca", MORNING_IDENTITY)
    (morning / "morning-summary.json").write_text(
        json.dumps(
            {"schema": "makerbench-nightly-cad-morning-v1", "run_id": morning.name, "votable": True,
             "valid_candidate_count": 2, "failed_candidate_count": 0, "cost_usd": 0.42}
        ),
        encoding="utf-8",
    )
    entrants = [
        {"entrant_id": "cadam-fable-image", "kind": "cadam", "model_id": "anthropic/claude-fable-5"},
        {"entrant_id": "codex-openscad", "kind": "arena", "model_id": "codex-gpt-5.6-sol"},
    ]
    (runs / "nightly-cad-queue.json").write_text(
        json.dumps(
            {
                "schema": "makerbench-nightly-cad-queue-v1",
                "jobs": [
                    {"job_id": JOB, "instrument_id": "sambuca", "reference_image": "tasks/sambuca/reference.png",
                     "budget_usd": 5.0, "status": "votable", "run_id": morning.name, "run_dir": str(morning),
                     "entrants": entrants},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli_arena, "_stage_turntable_frames", _fake_turntable_frames)
    return tmp_path


@pytest.fixture
def studio_url(studio_repo: Path):
    app = create_studio_app(registry_path=studio_repo / "registry.json", repo_root=studio_repo)
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
        self.requests: list[tuple[str, str]] = []
        self.api_bodies: list[str] = []
        self._mark = 0
        self.page.on("console", lambda message: self.errors.append(message.text) if message.type == "error" else None)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("dialog", self._on_dialog)
        self.page.on("request", lambda request: self.requests.append((request.method, request.url)))
        self.page.on("response", self._on_response)
        self.page.goto(url)

    def _on_dialog(self, dialog):
        self.dialogs.append(dialog.message)
        dialog.dismiss()

    def _on_response(self, response):
        # JSON only: reading an event-stream body here would block until the stream ends.
        if "/api/" in response.url and "json" in (response.headers.get("content-type") or ""):
            try:
                self.api_bodies.append(response.text())
            except Exception:
                pass

    def mark(self) -> None:
        """Start a fresh window of API bodies (one blind screen's own traffic)."""
        self._mark = len(self.api_bodies)

    def bodies_since_mark(self) -> list[str]:
        return self.api_bodies[self._mark :]

    def rail(self, label: str):
        return self.page.get_by_role("navigation", name="Studio screens").get_by_role("link", name=label, exact=True)

    def go(self, label: str) -> None:
        """Keyboard rail navigation: focus the link, press Enter, focus lands on the heading."""
        self.rail(label).focus()
        self.page.keyboard.press("Enter")
        self.page.wait_for_function("(title) => document.title === title", arg=f"{label}: Arena Studio")
        self.page.wait_for_function("() => document.activeElement && document.activeElement.tagName === 'H1'")

    def close(self):
        self.context.close()


def _visible_page_state(page) -> str:
    return "\n".join(
        [
            page.content(),
            page.evaluate(
                "() => [...document.querySelectorAll('*')].flatMap(el => [...el.attributes].map(a => a.value)).join('\\n')"
            ),
            page.title(),
            page.url,
            page.evaluate("() => JSON.stringify({...localStorage}) + JSON.stringify({...sessionStorage})"),
        ]
    )


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _wait_for_frames(page) -> None:
    """Let both turntables finish preloading, so screenshots show real frames."""
    page.wait_for_function(
        "() => !/Loading angles/.test(document.querySelector('.stage')?.innerText || 'Loading angles')", timeout=15_000
    )


def _wait_until_loaded(page) -> None:
    page.wait_for_function(
        "() => !/Loading/.test(document.querySelector('main')?.innerText || 'Loading')", timeout=15_000
    )


def _assert_blind(session: Session, identity: tuple[str, ...], where: str) -> None:
    state = _visible_page_state(session.page)
    for secret in identity:
        assert secret not in state, f"{secret!r} is on the {where} page before the vote"
        assert not [body for body in session.bodies_since_mark() if secret in body], (
            f"{secret!r} reached the client in an API response on {where} before the vote"
        )


@pytest.mark.parametrize("mode", sorted(WEBGL_MODES))
def test_full_evening_journey_through_every_screen(
    studio_url: str, studio_repo: Path, screenshot_dir: Path, mode: str
):
    queue_path = studio_repo / "runs" / "nightly-cad-queue.json"
    queue_before = queue_path.read_bytes()
    shot = lambda name: session.page.screenshot(path=str(screenshot_dir / f"f7-e2e-{name}-{mode}.png"), full_page=True)  # noqa: E731

    with sync_playwright() as playwright:
        browser = _launch(playwright, mode)
        session = Session(browser, f"{studio_url}/#/runs", viewport={"width": 1440, "height": 1000})
        page = session.page
        if mode == "zero-webgl":
            assert page.evaluate("() => document.createElement('canvas').getContext('webgl2')") is None

        # 1. Runs: every screen is on the rail, in order.
        page.wait_for_function("() => document.title === 'Runs: Arena Studio'")
        rail_labels = [text.strip() for text in page.locator("nav.rail a").all_inner_texts()]
        assert rail_labels == [label for _id, label, _takes_run in RAIL]
        page.get_by_role("link", name=RUN_ID, exact=True).click()
        page.get_by_role("heading", name=RUN_ID, level=2).wait_for()
        shot("01-runs")

        # 2. Blind voting: nothing identifies a model until the keyboard vote is saved.
        session.mark()
        page.get_by_role("link", name="Start blind voting").click()
        page.wait_for_function("() => document.title === 'Blind voting: Arena Studio'")
        page.locator(".stage").wait_for()
        page.locator(".plate").nth(1).wait_for()
        _wait_for_frames(page)
        page.wait_for_load_state("networkidle")
        _assert_blind(session, RUN_IDENTITY, "Blind voting")
        shot("02-vote-before")
        page.keyboard.press("a")
        page.locator(".reveal").wait_for()
        revealed = page.locator(".reveal").inner_text()
        assert "model-a" in revealed and "model-b" in revealed
        votes = _jsonl(studio_repo / "runs" / "code_cad_arena" / RUN_ID / "votes.blind.jsonl")
        assert [vote["winner"] for vote in votes] == ["left"]
        shot("03-vote-revealed")

        # 3. Agreement analytics keeps the run from the rail and counts that one vote.
        session.go("Agreement analytics")
        assert page.url.endswith(f"#/analytics/{RUN_ID}")
        elo_rows = page.locator(".elo-section table.leaderboard tbody tr")
        elo_rows.first.wait_for()
        assert elo_rows.count() == 2
        # One game each: both ratings are provisional (Q5, below 5 games).
        assert page.locator(".elo-section .badge-provisional").count() == 2
        shot("04-analytics")

        # 4. Launch: a zero-token dry run for the approved instrument streams its log.
        session.go("Launch")
        ocarina = page.locator("table.catalog tbody tr").filter(
            has=page.locator("code.task-id").get_by_text("ocarina", exact=True)
        )
        ocarina.wait_for()
        ocarina.locator("input[type=checkbox]").check()
        page.fill(".launch-form textarea[name=models]", "stub-a, stub-b")
        page.fill(".launch-form input[name=run_id]", DRY_RUN)
        submit = page.locator(".launch-form button[type=submit]")
        page.wait_for_function(
            "() => document.querySelector('.launch-form button[type=submit]')?.getAttribute('aria-disabled') !== 'true'"
        )
        submit.click()
        page.get_by_text(f"Started {DRY_RUN} as a dry run.").wait_for()
        page.wait_for_function(
            "(run) => (document.querySelector('pre.log')?.textContent || '').includes('ARENA PROCESS START ' + run)",
            arg=DRY_RUN,
            timeout=20_000,
        )
        page.locator(".connection", has_text="Run finished").wait_for(timeout=60_000)
        launch_record = json.loads(
            (studio_repo / "runs" / "code_cad_arena" / DRY_RUN / "studio_launch.json").read_text(encoding="utf-8")
        )
        assert "--stub" in launch_record["command"]
        shot("05-launch-dry-run")

        # 5. Compare the voted run with the older one: model-a is rated in both.
        session.go("Compare runs")
        page.evaluate("(hash) => { window.location.hash = hash; }", f"#/compare?a={RUN_ID}&b={RUN_B}")
        headings = page.locator(".compare-column h2")
        page.wait_for_function("() => document.querySelectorAll('.compare-column h2').length === 2")
        assert sorted(headings.all_inner_texts()) == sorted([RUN_ID, RUN_B])
        # The columns fetch after the hash change, so network idle alone can come too early.
        _wait_until_loaded(page)
        assert "model-a" in page.locator("main").inner_text()
        assert page.locator(".caveat", has_text="same run").count() == 0
        shot("06-compare")

        # 6. DoE: preview a matrix; the unapproved instrument is predicted as skipped. Nothing is written.
        session.go("DoE matrix")
        page.locator("input[name=instrument][value=ocarina]").wait_for()
        for instrument in ("ocarina", "kora"):
            page.locator(f"input[name=instrument][value={instrument}]").check()
        page.fill("textarea[name=models]", SUBSCRIPTION)
        page.fill("input[name=seeds]", "0, 1")
        preview = page.locator(".doe-preview")
        # 2 instruments x 2 entrants x 4 levels x 1 tier x 2 seeds.
        preview.locator(".facts dd.measure").first.filter(has_text="32").wait_for()
        page.locator(".skips li", has_text="kora").filter(has_text="Reference image not approved").wait_for()
        shot("07-doe")

        # 7. Nightly cockpit: read-only view of the queue and its lease.
        session.go("Nightly cockpit")
        rows = page.locator("table.nightly-table tbody tr")
        rows.first.wait_for()
        assert rows.count() == 1
        assert "No lease: no nightly run is active" in page.locator(".lease-status").inner_text()
        shot("08-nightly")

        # 8. Morning review: the finished bundle, voted blind by keyboard.
        session.mark()
        session.go("Morning review")
        bundle_rows = page.locator("table.bundles tbody tr")
        bundle_rows.first.wait_for()
        assert bundle_rows.count() == 1
        page.get_by_role("link", name="Review blind").click()
        page.locator(".stage").wait_for()
        page.locator(".plate").nth(1).wait_for()
        _wait_for_frames(page)
        page.wait_for_load_state("networkidle")
        _assert_blind(session, MORNING_IDENTITY, "Morning review")
        shot("09-morning-before")
        page.keyboard.press("a")
        page.locator(".reveal").wait_for()
        revealed = page.locator(".reveal").inner_text()
        assert "model-m1" in revealed and "model-m2" in revealed
        assert len(_jsonl(studio_repo / "runs" / "morning_run" / "votes.blind.jsonl")) == 1
        shot("10-morning-revealed")

        # 9. Back on Runs: the fixtures are listed, and the dry run launched from the UI is
        # listed exactly when it wrote run_log.json (discovery's rule). A stub dry run only
        # writes one where it can compile; the CI browser runner has no OpenSCAD.
        session.go("Runs")
        for run in (RUN_ID, RUN_B):
            page.get_by_role("link", name=run, exact=True).wait_for()
        page.wait_for_load_state("networkidle")
        dry_run_link = page.get_by_role("link", name=DRY_RUN, exact=True)
        if (studio_repo / "runs" / "code_cad_arena" / DRY_RUN / "run_log.json").exists():
            dry_run_link.wait_for()
        else:
            assert dry_run_link.count() == 0
        shot("11-runs-after")

        # Across the whole evening: nothing hostile fired, the cockpit wrote nothing,
        # and the only writes were the two votes and the one launch.
        page.wait_for_load_state("networkidle")
        assert session.dialogs == []
        assert session.errors == []
        assert queue_path.read_bytes() == queue_before
        assert not (studio_repo / "runs" / ".nightly-cad.lock").exists()
        writes = [(method, re.sub(r"^https?://[^/]+", "", url).split("?")[0]) for method, url in session.requests if method != "GET"]
        assert {method for method, _path in writes} <= {"POST"}
        assert [path for _method, path in writes if path.endswith("/api/competitions/launch")] == ["/api/competitions/launch"]
        assert not [path for _method, path in writes if path.endswith(("/api/doe/queue", "/approve", "/export-winners"))]
        assert len([path for _method, path in writes if path.endswith("/vote")]) == 2, writes
        session.close()
        browser.close()


def test_every_screen_in_dark_theme_renders_without_errors(studio_url: str, screenshot_dir: Path):
    """The dark-theme half of the screenshot set: every rail screen, loaded directly."""
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/runs", viewport={"width": 1440, "height": 1000}, color_scheme="dark")
        page = session.page
        for screen_id, label, takes_run in RAIL:
            if screen_id == "compare":
                hash_route = f"#/compare?a={RUN_ID}&b={RUN_B}"
            else:
                hash_route = f"#/{screen_id}/{RUN_ID}" if takes_run else f"#/{screen_id}"
            page.evaluate("(hash) => { window.location.hash = hash; }", hash_route)
            page.wait_for_function("(title) => document.title === title", arg=f"{label}: Arena Studio")
            page.locator("main h1").wait_for()
            if screen_id == "vote":
                page.locator(".plate").nth(1).wait_for()
                _wait_for_frames(page)
            page.wait_for_load_state("networkidle")
            if screen_id in ("analytics", "compare"):
                _wait_until_loaded(page)
            page.screenshot(path=str(screenshot_dir / f"f7-dark-{screen_id}.png"), full_page=True)
        assert session.dialogs == []
        assert session.errors == []
        session.close()
        browser.close()
