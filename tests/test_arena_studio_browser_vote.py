"""Real-browser checks for the rebuilt blind vote stage (Playwright, opt-in).

Covers the Phase 2 verification plan for voting: nothing that names an entrant
reaches the page, its storage, its URLs, its console or any API response before
the vote is saved; keyboard-only voting, flags, skip and undo; the 24-frame
turntable with zero WebGL; 3D orbit with a context-loss fallback where WebGL
exists; and phone width. Same opt-in rules as tests/test_arena_studio_browser.py.
"""

from __future__ import annotations

import json
import math
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
trimesh = pytest.importorskip("trimesh")

from PIL import Image, ImageDraw  # noqa: E402 - after the optional-dependency guards

from makerbench import cli_arena  # noqa: E402 - after the optional-dependency guards
from makerbench.arena_studio import create_studio_app  # noqa: E402 - after the optional-dependency guards

RUN_ID = "vote-fixture"
# Every string that would identify an entrant before the vote.
IDENTITY = ("model-a", "model-b", "t-1", "t-2")
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
    """Stand-in renderer: 24 real PNG frames of a turning bar, blind-aliased the
    same way cli_arena._stage_turntable_frames aliases real renders."""
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


@pytest.fixture
def vote_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    run_dir = tmp_path / "runs" / "code_cad_arena" / RUN_ID
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    trials = []
    for trial_id, model_id, color, pass_rate in (
        ("t-1", "model-a", (176, 126, 64), 1.0),
        ("t-2", "model-b", (64, 136, 176), 0.5),
    ):
        png = artifacts / f"{trial_id}.png"
        Image.new("RGB", (320, 320), color).save(png)
        stl = artifacts / f"{trial_id}.stl"
        trimesh.creation.box(extents=(10, 10, 10)).export(stl)
        trials.append(
            {
                "trial_id": trial_id,
                "model_id": model_id,
                "instrument_id": "ocarina",
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
        "started_at": "2026-09-14T09:30:00Z",
        "config": {"model_ids": ["model-a", "model-b"], "instruments": ["ocarina"]},
        "trials": trials,
    }
    (run_dir / "run_log.json").write_text(json.dumps(run_log), encoding="utf-8")
    (tmp_path / "registry.json").write_text(json.dumps({"instruments": []}), encoding="utf-8")
    monkeypatch.setattr(cli_arena, "_stage_turntable_frames", _fake_turntable_frames)
    return tmp_path


@pytest.fixture
def studio_url(vote_repo: Path):
    app = create_studio_app(registry_path=vote_repo / "registry.json", repo_root=vote_repo)
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
        self.console: list[str] = []
        self.errors: list[str] = []
        self.api_bodies: list[str] = []
        self.requests: list[str] = []
        self.page.on("request", lambda request: self.requests.append(request.url))
        self.page.on("console", self._on_console)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.on("response", self._on_response)
        self.page.goto(url)

    def _on_console(self, message):
        self.console.append(message.text)
        if message.type == "error":
            self.errors.append(message.text)

    def _on_response(self, response):
        if "/api/" in response.url:
            try:
                self.api_bodies.append(response.text())
            except Exception:
                pass

    def wait_for_stage(self):
        self.page.locator(".stage").wait_for()
        self.page.locator(".plate").nth(1).wait_for()

    def close(self):
        self.context.close()


def _visible_page_state(page) -> str:
    """Everything a person or extension could read off the page without devtools
    network access: markup, every attribute, title, URL, loaded resource URLs, storage."""
    return "\n".join(
        [
            page.content(),
            page.evaluate(
                "() => [...document.querySelectorAll('*')].flatMap(el => [...el.attributes].map(a => a.value)).join('\\n')"
            ),
            page.title(),
            page.url,
            page.evaluate("() => performance.getEntriesByType('resource').map(r => r.name).join('\\n')"),
            page.evaluate("() => JSON.stringify({...localStorage}) + JSON.stringify({...sessionStorage})"),
        ]
    )


def _blind_votes(vote_repo: Path) -> list[dict]:
    path = vote_repo / "runs" / "code_cad_arena" / RUN_ID / "votes.blind.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("mode", sorted(WEBGL_MODES))
def test_vote_stage_reveals_nothing_until_the_vote_is_saved(
    studio_url: str, vote_repo: Path, screenshot_dir: Path, mode: str
):
    with sync_playwright() as playwright:
        browser = _launch(playwright, mode)
        session = Session(browser, f"{studio_url}/#/vote/{RUN_ID}", viewport={"width": 1440, "height": 1000})
        page = session.page
        session.wait_for_stage()
        page.wait_for_timeout(700)  # let frames preload and the turntable turn

        before = _visible_page_state(page)
        for identity in IDENTITY:
            assert identity not in before, f"{identity!r} is on the page before the vote"
            assert all(identity not in body for body in session.api_bodies), (
                f"{identity!r} reached the client in an API response before the vote"
            )
            assert all(identity not in line for line in session.console)
        assert page.title() == "Blind voting: Arena Studio"
        page.screenshot(path=str(screenshot_dir / f"f2-vote-stage-{mode}-light.png"), full_page=True)

        page.locator(".vote-bar button", has_text="A is better").click()
        reveal = page.locator(".reveal")
        reveal.wait_for()
        revealed = reveal.inner_text()
        assert "model-a" in revealed and "model-b" in revealed
        assert "Your last vote, revealed" in revealed
        assert _blind_votes(vote_repo)[-1]["winner"] == "left"
        page.screenshot(path=str(screenshot_dir / f"f2-vote-revealed-{mode}-light.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()


def test_keyboard_only_voting_flags_skip_undo_and_help(studio_url: str, vote_repo: Path, screenshot_dir: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/vote/{RUN_ID}", viewport={"width": 1440, "height": 1000})
        page = session.page
        session.wait_for_stage()
        stage = page.locator(".stage")

        first_pair = stage.get_attribute("data-pair-id")
        page.keyboard.press("s")
        page.wait_for_function(
            "(pair) => document.querySelector('.stage')?.dataset.pairId && document.querySelector('.stage').dataset.pairId !== pair",
            arg=first_pair,
            polling=100,
        )

        left_boxes = page.locator(".plate").nth(0).locator("input[type=checkbox]")
        right_boxes = page.locator(".plate").nth(1).locator("input[type=checkbox]")
        page.keyboard.press("1")
        page.keyboard.press("Shift+Digit2")
        assert left_boxes.nth(0).is_checked()
        assert right_boxes.nth(1).is_checked()

        page.keyboard.press("?")
        assert page.locator("dialog.shortcut-help").evaluate("d => d.open")
        page.keyboard.press("a")  # ignored while the help dialog is open
        page.keyboard.press("Escape")
        assert not page.locator("dialog.shortcut-help").evaluate("d => d.open")
        assert _blind_votes(vote_repo) == []

        page.keyboard.press("a")
        page.locator(".reveal").wait_for()
        vote = _blind_votes(vote_repo)[-1]
        assert vote["winner"] == "left"
        assert vote["flags"] == {"left": ["missing_critical_components"], "right": ["misaligned_assembly"]}
        assert not left_boxes.nth(0).is_checked()  # flags reset for the next pair

        page.keyboard.press("u")
        page.get_by_text("Vote undone.").wait_for()
        assert _blind_votes(vote_repo)[-1].get("retracts") is True
        assert page.locator(".reveal").count() == 0

        # Undo removes its own button; focus must return to the vote controls, not
        # fall back to <body> where a keyboard user loses their place.
        on_vote_a = "() => document.activeElement.matches('.vote-bar button.vote[aria-keyshortcuts=\"A L\"]')"
        page.wait_for_function(on_vote_a, polling=100, timeout=10_000)
        page.wait_for_function(
            "() => document.querySelector('.vote-bar button.vote').getAttribute('aria-disabled') === 'false'",
            polling=100,
            timeout=10_000,
        )

        # Keyboard focus is always visible on the vote controls.
        page.keyboard.press("Tab")
        page.keyboard.press("Shift+Tab")
        assert page.evaluate(on_vote_a)
        outline = page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle")
        assert outline != "none"
        page.screenshot(path=str(screenshot_dir / "f2-vote-keyboard-focus.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()


def test_zero_webgl_turntable_turns_and_steps(studio_url: str, screenshot_dir: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/vote/{RUN_ID}", viewport={"width": 1440, "height": 1000})
        page = session.page
        session.wait_for_stage()
        assert page.evaluate("() => document.createElement('canvas').getContext('webgl2')") is None
        orbit = page.locator(".view-toggle button", has_text="3D orbit")
        assert orbit.is_disabled()
        assert "needs WebGL" in page.locator("#viewer-note").inner_text()
        assert page.locator("model-viewer").count() == 0
        # The 3D viewer script loads only on demand in a WebGL browser (#722).
        assert not [url for url in session.requests if "model-viewer" in url]

        turntable = page.locator(".turntable").first
        start = int(turntable.get_attribute("data-frame"))
        page.wait_for_function(
            "(start) => Number(document.querySelector('.turntable').dataset.frame) !== start",
            arg=start,
            polling=100,
            timeout=5000,
        )
        turntable.focus()  # focus pauses rotation
        paused_at = int(turntable.get_attribute("data-frame"))
        page.keyboard.press(".")
        assert int(turntable.get_attribute("data-frame")) == (paused_at + 1) % 24
        page.keyboard.press("ArrowLeft")
        page.keyboard.press("ArrowLeft")
        assert int(turntable.get_attribute("data-frame")) == (paused_at - 1) % 24
        assert "/vote_pages/blind/" in turntable.locator("img").get_attribute("src")
        assert session.errors == []
        session.close()
        browser.close()


def test_3d_orbit_falls_back_to_the_turntable_on_context_loss(studio_url: str, screenshot_dir: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "webgl")
        session = Session(browser, f"{studio_url}/#/vote/{RUN_ID}", viewport={"width": 1440, "height": 1000})
        page = session.page
        session.wait_for_stage()
        if not page.evaluate("() => !!document.createElement('canvas').getContext('webgl2')"):
            session.close()
            browser.close()
            pytest.skip("no WebGL2 in this browser environment")
        orbit = page.locator(".view-toggle button", has_text="3D orbit")
        if orbit.is_disabled():
            session.close()
            browser.close()
            pytest.skip("fixture meshes produced no GLB for 3D orbit")
        page.keyboard.press("v")
        page.locator("model-viewer").nth(1).wait_for(state="attached")
        page.wait_for_timeout(500)
        page.screenshot(path=str(screenshot_dir / "f2-vote-3d-orbit.png"), full_page=True)
        page.locator("model-viewer").first.evaluate(
            "el => el.dispatchEvent(new CustomEvent('error', {detail: {type: 'webglcontextlost'}}))"
        )
        page.locator(".turntable").first.wait_for()
        assert "stopped working" in page.locator("#viewer-note").inner_text()
        assert page.locator("model-viewer").count() == 0
        session.close()
        browser.close()


def test_vote_stage_dark_theme_and_phone_width(studio_url: str, screenshot_dir: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(
            browser,
            f"{studio_url}/#/vote/{RUN_ID}",
            viewport={"width": 1440, "height": 1000},
            color_scheme="dark",
        )
        session.wait_for_stage()
        session.page.wait_for_timeout(500)
        session.page.screenshot(path=str(screenshot_dir / "f2-vote-stage-dark.png"), full_page=True)
        assert session.errors == []
        session.close()

        phone = Session(browser, f"{studio_url}/#/vote/{RUN_ID}", viewport={"width": 400, "height": 860})
        page = phone.page
        phone.wait_for_stage()
        overflow = page.evaluate(
            "() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth"
        )
        assert overflow <= 0
        first = page.locator(".plate").nth(0).bounding_box()
        second = page.locator(".plate").nth(1).bounding_box()
        assert second["y"] >= first["y"] + first["height"] - 1  # plates stack vertically
        page.screenshot(path=str(screenshot_dir / "f2-vote-stage-400px.png"), full_page=True)
        assert phone.errors == []
        phone.close()
        browser.close()
