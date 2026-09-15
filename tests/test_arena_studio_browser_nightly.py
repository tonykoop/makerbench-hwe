"""Real-browser checks for the rebuilt Nightly cockpit and Morning review screens
(Playwright, opt-in).

Covers the Phase 2 verification plan for these screens:
- The cockpit is read-only (no mutating request, queue bytes unchanged).
- It flags a stalled running job and shows the replayed budget and the
  ACTIVE/ABSENT lease with heartbeat age.
- Hostile queue text stays inert.
- Morning review lists only votable bundles and votes through the same blind
  stage, with nothing that names an entrant before the vote. It never loads
  the cockpit or the run roster, and morning votes have no undo.
- Missing queue, zero-WebGL, dark theme and phone width.

Same opt-in rules as tests/test_arena_studio_browser.py.
"""

from __future__ import annotations

import json
import math
import os
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
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

JOB = "sambuca-night"
IDENTITY = ("model-a", "model-b", "t-1", "t-2")
HOSTILE_INSTRUMENT = '"><img src=x onerror="window.__pwned=1">'
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


def _morning_run(runs: Path) -> Path:
    run_dir = runs / "morning_run"
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    trials = []
    for trial_id, model_id, color in (("t-1", "model-a", (176, 126, 64)), ("t-2", "model-b", (64, 136, 176))):
        png = artifacts / f"{trial_id}.png"
        Image.new("RGB", (320, 320), color).save(png)
        stl = artifacts / f"{trial_id}.stl"
        trimesh.creation.box(extents=(10, 10, 10)).export(stl)
        trials.append(
            {
                "trial_id": trial_id,
                "model_id": model_id,
                "instrument_id": "sambuca",
                "seed": 0,
                "rep": 0,
                "status": "completed",
                "result": {"render_ok": True, "artifacts": {"png_path": str(png), "stl_path": str(stl)}},
            }
        )
    (run_dir / "run_log.json").write_text(
        json.dumps({"started_at": "2026-09-14T02:00:00Z", "config": {"model_ids": ["model-a", "model-b"]}, "trials": trials}),
        encoding="utf-8",
    )
    (run_dir / "morning-summary.json").write_text(
        json.dumps(
            {
                "schema": "makerbench-nightly-cad-morning-v1",
                "run_id": run_dir.name,
                "votable": True,
                "valid_candidate_count": 2,
                "failed_candidate_count": 0,
                "cost_usd": 0.42,
            }
        ),
        encoding="utf-8",
    )
    return run_dir


@pytest.fixture
def nightly_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runs = tmp_path / "runs"
    runs.mkdir()
    morning = _morning_run(runs)
    kora_run = runs / "kora_run"
    kora_run.mkdir()
    (kora_run / "nightly-state.json").write_text(
        json.dumps({"budget": {"spent_usd": 1.5, "charges": [{"entrant_id": "codex-openscad", "cost_usd": 1.5}]}}),
        encoding="utf-8",
    )
    entrants = [
        {"entrant_id": "cadam-fable-image", "kind": "cadam", "model_id": "anthropic/claude-fable-5"},
        {"entrant_id": "codex-openscad", "kind": "arena", "model_id": "codex-gpt-5.6-sol"},
    ]
    jobs = [
        {"job_id": JOB, "instrument_id": "sambuca", "reference_image": "tasks/sambuca/reference.png", "budget_usd": 5.0,
         "status": "votable", "run_id": morning.name, "run_dir": str(morning), "entrants": entrants},
        {"job_id": "kora-night", "instrument_id": "kora", "reference_image": "tasks/kora/reference.png", "budget_usd": 5.0,
         "status": "running", "run_id": kora_run.name, "run_dir": str(kora_run), "entrants": entrants},
        {"job_id": "erhu-night", "instrument_id": HOSTILE_INSTRUMENT, "reference_image": "tasks/erhu/reference.png",
         "budget_usd": 2.0, "status": "queued", "entrants": entrants},
    ]
    (runs / "nightly-cad-queue.json").write_text(
        json.dumps({"schema": "makerbench-nightly-cad-queue-v1", "jobs": jobs}), encoding="utf-8"
    )
    (tmp_path / "registry.json").write_text(json.dumps({"instruments": []}), encoding="utf-8")
    monkeypatch.setattr(cli_arena, "_stage_turntable_frames", _fake_turntable_frames)
    return tmp_path


def _serve(repo: Path):
    app = create_studio_app(registry_path=repo / "registry.json", repo_root=repo)
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
def studio_url(nightly_repo: Path):
    server, thread, url = _serve(nightly_repo)
    yield url
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
        self.requests: list[tuple[str, str]] = []
        self.api_bodies: list[str] = []
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
        if "/api/" in response.url:
            try:
                self.api_bodies.append(response.text())
            except Exception:
                pass

    def close(self):
        self.context.close()


def _visible_page_state(page) -> str:
    return "\n".join(
        [
            page.content(),
            page.evaluate("() => [...document.querySelectorAll('*')].flatMap(el => [...el.attributes].map(a => a.value)).join('\\n')"),
            page.title(),
            page.url,
            page.evaluate("() => performance.getEntriesByType('resource').map(r => r.name).join('\\n')"),
            page.evaluate("() => JSON.stringify({...localStorage}) + JSON.stringify({...sessionStorage})"),
        ]
    )


@pytest.mark.parametrize("mode", sorted(WEBGL_MODES))
@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_nightly_cockpit_is_read_only_and_flags_stalls(
    studio_url: str, nightly_repo: Path, screenshot_dir: Path, mode: str, color_scheme: str
):
    queue_path = nightly_repo / "runs" / "nightly-cad-queue.json"
    before = queue_path.read_bytes()
    with sync_playwright() as playwright:
        browser = _launch(playwright, mode)
        session = Session(
            browser, f"{studio_url}/#/nightly", viewport={"width": 1440, "height": 1000}, color_scheme=color_scheme
        )
        page = session.page
        rows = page.locator("table.nightly-table tbody tr")
        rows.first.wait_for()
        assert rows.count() == 3
        assert "No lease: no nightly run is active" in page.locator(".lease-status").inner_text()
        kora = page.locator("tr[data-job=kora-night]")
        assert kora.locator(".badge-stalled").count() == 1
        assert "stalled" in kora.locator(".job-notes").inner_text()
        assert "$1.50 of $5.00 spent" in kora.inner_text()
        assert page.locator("tr[data-job=sambuca-night] .badge-stalled").count() == 0
        assert HOSTILE_INSTRUMENT in page.locator("tr[data-job=erhu-night]").inner_text()

        page.wait_for_load_state("networkidle")
        assert session.dialogs == []
        assert page.evaluate("() => window.__pwned") is None
        assert not [url for _method, url in session.requests if url.rstrip("/").endswith("/x")]
        assert [request for request in session.requests if request[0] != "GET"] == []
        assert queue_path.read_bytes() == before
        assert not (nightly_repo / "runs" / ".nightly-cad.lock").exists()
        page.screenshot(path=str(screenshot_dir / f"f6-nightly-{mode}-{color_scheme}.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()


def test_nightly_cockpit_active_lease_with_heartbeat(studio_url: str, nightly_repo: Path, screenshot_dir: Path):
    heartbeat = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    (nightly_repo / "runs" / ".nightly-cad.lock").write_text(
        json.dumps({"pid": os.getpid(), "heartbeat_utc": heartbeat}), encoding="utf-8"
    )
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/nightly", viewport={"width": 1440, "height": 1000})
        page = session.page
        page.locator(".lease-status", has_text="A nightly run holds the lease").wait_for()
        lease = page.locator(".lease-panel").inner_text()
        assert str(os.getpid()) in lease
        assert "2 min ago" in lease
        assert page.locator(".badge-stalled").count() == 0
        assert "refreshing every 10 s" in page.locator(".refresh").inner_text()
        page.screenshot(path=str(screenshot_dir / "f6-nightly-active-lease.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()


def test_missing_queue_is_explained_on_both_screens(tmp_path: Path, screenshot_dir: Path):
    (tmp_path / "registry.json").write_text(json.dumps({"instruments": []}), encoding="utf-8")
    server, thread, url = _serve(tmp_path)
    try:
        with sync_playwright() as playwright:
            browser = _launch(playwright, "zero-webgl")
            session = Session(browser, f"{url}/#/nightly", viewport={"width": 1440, "height": 1000})
            page = session.page
            page.get_by_text("No nightly queue yet").wait_for()
            page.goto(f"{url}/#/morning")
            page.get_by_text("No nightly queue yet").wait_for()
            session.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize("mode", sorted(WEBGL_MODES))
def test_morning_review_votes_blind_without_undo(
    studio_url: str, nightly_repo: Path, screenshot_dir: Path, mode: str
):
    with sync_playwright() as playwright:
        browser = _launch(playwright, mode)
        session = Session(browser, f"{studio_url}/#/morning", viewport={"width": 1440, "height": 1000})
        page = session.page
        bundle_rows = page.locator("table.bundles tbody tr")
        bundle_rows.first.wait_for()
        assert bundle_rows.count() == 1
        assert "kora-night" not in page.locator("table.bundles").inner_text()
        page.screenshot(path=str(screenshot_dir / f"f6-morning-bundles-{mode}.png"), full_page=True)

        page.get_by_role("link", name="Review blind").click()
        page.locator(".stage").wait_for()
        page.locator(".plate").nth(1).wait_for()
        state = _visible_page_state(page)
        for secret in IDENTITY:
            assert secret not in state, f"{secret} visible before the vote"
            assert not [body for body in session.api_bodies if secret in body], f"{secret} in an API response before the vote"
        urls = [url for _method, url in session.requests]
        assert not [url for url in urls if "/api/runs" in url or "/api/nightly/queue" in url]
        assert page.locator(".vote-bar button").filter(has_text="Undo").count() == 0
        page.screenshot(path=str(screenshot_dir / f"f6-morning-stage-{mode}.png"), full_page=True)

        page.keyboard.press("a")
        page.locator(".reveal").wait_for()
        revealed = page.locator(".reveal").inner_text()
        assert "model-a" in revealed and "model-b" in revealed
        votes_path = nightly_repo / "runs" / "morning_run" / "votes.blind.jsonl"
        votes = [json.loads(line) for line in votes_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(votes) == 1
        assert page.get_by_role("button", name="Undo").count() == 0
        page.keyboard.press("u")
        page.wait_for_load_state("networkidle")
        votes = [json.loads(line) for line in votes_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(votes) == 1 and not votes[-1].get("retracts")
        assert not (nightly_repo / "runs" / "morning_run" / "morning-vote").exists()
        page.screenshot(path=str(screenshot_dir / f"f6-morning-revealed-{mode}.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()


def _votes(run_dir: Path) -> list[dict]:
    path = run_dir / "votes.blind.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _wait_for_stage(page) -> None:
    page.locator(".stage").wait_for()
    page.locator(".plate").nth(1).wait_for()
    page.wait_for_load_state("networkidle")


def test_vote_and_morning_never_double_bind_the_shared_key_handler(studio_url: str, nightly_repo: Path):
    """Claude UI review #781: both screens mount the same VoteStage, whose shortcut handler
    lives on document. Moving between them in the SPA must leave exactly one live handler:
    one key press, one POST, to the screen on view."""
    arena_run = _morning_run(nightly_repo / "runs" / "code_cad_arena")
    morning_run = nightly_repo / "runs" / "morning_run"
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/vote/{arena_run.name}", viewport={"width": 1440, "height": 1000})
        page = session.page
        _wait_for_stage(page)

        # Vote -> Morning by hash change (no reload), then one key press.
        page.evaluate("(hash) => { window.location.hash = hash; }", f"#/morning/{JOB}")
        page.wait_for_function("() => document.title === 'Morning review: Arena Studio'")
        _wait_for_stage(page)
        mark = len(session.requests)
        page.keyboard.press("b")
        page.locator(".reveal").wait_for()
        page.wait_for_load_state("networkidle")
        posts = [url for method, url in session.requests[mark:] if method == "POST"]
        assert len(posts) == 1 and posts[0].endswith(f"/api/morning/{JOB}/vote"), posts
        assert len(_votes(morning_run)) == 1 and _votes(arena_run) == []

        # Morning -> Vote, then one key press.
        page.evaluate("(hash) => { window.location.hash = hash; }", f"#/vote/{arena_run.name}")
        page.wait_for_function("() => document.title === 'Blind voting: Arena Studio'")
        _wait_for_stage(page)
        mark = len(session.requests)
        page.keyboard.press("a")
        page.locator(".reveal").wait_for()
        page.wait_for_load_state("networkidle")
        posts = [url for method, url in session.requests[mark:] if method == "POST"]
        assert len(posts) == 1 and posts[0].endswith(f"/api/runs/{arena_run.name}/vote"), posts
        assert len(_votes(arena_run)) == 1 and len(_votes(morning_run)) == 1
        assert session.errors == []
        session.close()
        browser.close()


def test_focus_lands_on_the_done_state_after_the_last_morning_vote(studio_url: str, screenshot_dir: Path):
    """Claude UI review #781: voting the bundle's last pair unmounts the stage; keyboard
    focus must move to the "voted every pair" state instead of <body>."""
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/morning/{JOB}", viewport={"width": 1440, "height": 1000})
        page = session.page
        _wait_for_stage(page)
        for _ in range(80):
            page.keyboard.press("Tab")
            if page.evaluate("() => Boolean(document.activeElement?.closest('.vote-bar')) && (document.activeElement.textContent || '').includes('Draw')"):
                break
        else:
            raise AssertionError("Tab never reached the Draw button")
        page.keyboard.press("Enter")
        page.locator(".vote-done").wait_for()
        page.wait_for_function("() => Boolean(document.activeElement?.closest('.vote-done'))", timeout=5_000)
        page.locator(".reveal").wait_for()
        assert page.evaluate("() => document.activeElement !== document.body")
        page.screenshot(path=str(screenshot_dir / "f6-morning-done-focus.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()


def test_nightly_and_morning_at_phone_width(studio_url: str, screenshot_dir: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/nightly", viewport={"width": 400, "height": 800})
        page = session.page
        overflow = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        page.locator("table.nightly-table tbody tr").first.wait_for()
        assert page.evaluate(overflow) <= 0
        page.screenshot(path=str(screenshot_dir / "f6-nightly-400px.png"), full_page=True)
        page.goto(f"{studio_url}/#/morning/{JOB}")
        page.locator(".stage").wait_for()
        assert page.evaluate(overflow) <= 0
        page.screenshot(path=str(screenshot_dir / "f6-morning-400px.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()
