"""Real-browser checks for the rebuilt Launch & preflight screen (Playwright, opt-in).

Covers the Phase 2 verification plan for this screen. The reference-image
gatekeeper blocks a launch until the exact image is approved. A dry run starts
and its log streams over SSE. A live launch on a server without --allow-live
explains the 403. Hostile registry, queue and preflight strings render as inert
text. Also checked: zero-WebGL, dark theme and phone width. Same opt-in rules
as tests/test_arena_studio_browser.py.
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

from makerbench.arena_studio import create_studio_app  # noqa: E402 - after the optional-dependency guards
from makerbench.arena_studio.service import ArenaStudioService  # noqa: E402 - after the optional-dependency guards

# Strings that would run script, open a dialog or fetch "x" if Studio ever
# rendered server data as markup.
HOSTILE_NAME = '"><img src=x onerror="window.__pwned=1">'
HOSTILE_FAMILY = '`${alert(1)}`"><img src=x onerror=alert(1)>'
HOSTILE_JOB = '</code><img src=x onerror="window.__pwned=2">`'
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
def launch_repo(tmp_path: Path) -> Path:
    registry = tmp_path / "registry.json"
    instruments = [
        {"id": "ocarina", "display_name": "Ocarina", "family": "woodwind", "task_kind": "single_part_vessel", "envelope_mm": [140, 90, 70]},
        {"id": "kora", "display_name": "Kora", "family": "strings", "task_kind": "harp_lute", "envelope_mm": [1200, 350, 250]},
        {"id": "hostile-task", "display_name": HOSTILE_NAME, "family": HOSTILE_FAMILY, "task_kind": "single_part_vessel", "envelope_mm": [10, 10, 10]},
    ]
    registry.write_text(
        json.dumps({"schema": "makerbench-code-cad-arena-registry-v1", "instruments": instruments}),
        encoding="utf-8",
    )
    for task_id, color in (("ocarina", (176, 126, 64)), ("kora", (64, 136, 176))):
        image = tmp_path / "tasks" / task_id / "reference.png"
        image.parent.mkdir(parents=True)
        Image.new("RGB", (480, 320), color).save(image)
    ArenaStudioService(registry_path=registry, repo_root=tmp_path).set_task_approval("ocarina", True)
    return tmp_path


@pytest.fixture
def preflight_files(tmp_path: Path) -> dict[str, Path]:
    folder = tmp_path / "nightly"
    folder.mkdir()
    secrets = folder / "nightly.env"
    secrets.write_text(
        "CADAM_USER_ID=fake-user\nCADAM_ACCESS_TOKEN=sk-FAKE-never-shown\nSUPABASE_SERVICE_ROLE_KEY=changeme\n",
        encoding="utf-8",
    )
    queue = folder / "nightly-cad-queue.json"
    queue.write_text(
        json.dumps(
            {"schema": "makerbench-nightly-cad-queue-v1", "jobs": [{"job_id": HOSTILE_JOB, "status": "queued"}]}
        ),
        encoding="utf-8",
    )
    return {"secrets": secrets, "queue": queue, "output_root": folder}


@pytest.fixture
def studio_url(launch_repo: Path):
    app = create_studio_app(registry_path=launch_repo / "registry.json", repo_root=launch_repo)
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

    def row(self, task_id: str):
        return self.page.locator("table.catalog tbody tr").filter(
            has=self.page.locator("code.task-id").get_by_text(task_id, exact=True)
        )

    def close(self):
        self.context.close()


@pytest.mark.parametrize("mode", sorted(WEBGL_MODES))
@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_launch_screen_renders_cleanly(studio_url: str, screenshot_dir: Path, mode: str, color_scheme: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright, mode)
        session = Session(
            browser, f"{studio_url}/#/launch", viewport={"width": 1440, "height": 1000}, color_scheme=color_scheme
        )
        page = session.page
        ocarina = session.row("ocarina")
        ocarina.wait_for()
        ocarina.locator("input[type=checkbox]").check()
        ocarina.get_by_role("button").click()
        page.wait_for_function("() => document.querySelector('.reference-image')?.naturalWidth > 0")
        ocarina.locator(".gate", has_text="Approved").wait_for()

        page.fill(".launch-form textarea[name=models]", "claude-code-opus-5, openrouter-example-model")
        page.locator(".cost", has_text="$0 subscription").wait_for()
        page.locator(".cost", has_text="Cost unknown").wait_for()
        assert page.locator(".blockers").count() == 0
        assert page.locator(".launch-form button[type=submit]").get_attribute("aria-disabled") == "false"

        page.screenshot(path=str(screenshot_dir / f"f3-launch-{mode}-{color_scheme}.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()


def test_gatekeeper_blocks_launch_until_approved_then_the_dry_run_log_streams(
    studio_url: str, launch_repo: Path, screenshot_dir: Path
):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/launch", viewport={"width": 1440, "height": 1000})
        page = session.page
        kora = session.row("kora")
        kora.wait_for()
        kora.locator("input[type=checkbox]").check()
        page.fill(".launch-form textarea[name=models]", "stub-a, stub-b")
        page.fill(".launch-form input[name=run_id]", "ui-dry-run")

        page.locator(".blockers li", has_text="Approve the reference image for kora").wait_for()
        submit = page.locator(".launch-form button[type=submit]")
        assert submit.get_attribute("aria-disabled") == "true"
        # aria-disabled keeps the button focusable; force the click to prove it still sends nothing.
        submit.click(force=True)
        page.wait_for_load_state("networkidle")
        assert not [url for url in session.requests if url.endswith("/api/competitions/launch")]
        run_dir = launch_repo / "runs" / "code_cad_arena" / "ui-dry-run"
        assert not run_dir.exists()

        kora.get_by_role("button").click()
        page.wait_for_function("() => document.querySelector('.reference-image')?.naturalWidth > 0")
        page.locator(".reference-panel").get_by_role("button", name="Approve this image").click()
        kora.locator(".gate", has_text="Approved").wait_for()
        page.locator(".blockers").wait_for(state="detached")

        submit.click()
        page.get_by_text("Started ui-dry-run as a dry run.").wait_for()
        page.wait_for_function(
            "() => (document.querySelector('pre.log')?.textContent || '').includes('ARENA PROCESS START ui-dry-run')",
            timeout=20_000,
        )
        page.wait_for_function(
            """() => {
              const row = [...document.querySelectorAll('table.jobs tbody tr')]
                .find((tr) => tr.textContent.includes('ui-dry-run'));
              return row && !row.textContent.includes('Running');
            }""",
            timeout=60_000,
        )
        launch = json.loads((run_dir / "studio_launch.json").read_text(encoding="utf-8"))
        assert "--stub" in launch["command"]
        page.locator(".connection", has_text="Run finished").wait_for()
        assert "ARENA PROCESS START ui-dry-run" in page.locator("pre.log").inner_text()

        page.screenshot(path=str(screenshot_dir / "f3-launch-dry-run-log.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()


def test_live_launch_on_a_dry_run_server_explains_allow_live(studio_url: str, launch_repo: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/launch", viewport={"width": 1440, "height": 1000})
        page = session.page
        session.row("ocarina").wait_for()
        session.row("ocarina").locator("input[type=checkbox]").check()
        page.select_option(".launch-form select[name=context_tier]", "blind")
        page.fill(".launch-form textarea[name=models]", "stub-a")
        page.fill(".launch-form input[name=run_id]", "ui-live-refused")
        page.locator(".launch-form input[name=live]").check()
        page.locator(".launch-form button[type=submit]", has_text="Start live run").click()

        status = page.locator(".launch-form .panel-status.is-error")
        status.wait_for()
        assert "--allow-live" in status.inner_text()
        assert not (launch_repo / "runs" / "code_cad_arena" / "ui-live-refused").exists()
        session.close()
        browser.close()


def test_hostile_registry_queue_and_preflight_strings_render_as_inert_text(
    studio_url: str, preflight_files: dict[str, Path], screenshot_dir: Path
):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/launch", viewport={"width": 1440, "height": 1000})
        page = session.page
        hostile = session.row("hostile-task")
        hostile.wait_for()
        assert HOSTILE_NAME in hostile.inner_text()
        assert HOSTILE_FAMILY in hostile.inner_text()

        hostile.get_by_role("button").click()
        command = page.locator(".reference-panel .command")
        command.wait_for()
        assert HOSTILE_FAMILY in command.inner_text()

        for name in ("secrets", "queue", "output_root"):
            page.fill(f".preflight-form input[name={name}]", str(preflight_files[name]))
        page.locator(".preflight-form button[type=submit]").click()
        page.locator(".verdict").wait_for()
        result = page.locator(".preflight-result")
        assert HOSTILE_JOB in result.inner_text()
        assert "sk-FAKE-never-shown" not in page.content()

        page.wait_for_load_state("networkidle")
        assert session.dialogs == []
        assert page.evaluate("() => window.__pwned") is None
        assert page.locator("main img").count() == 0
        assert not [url for url in session.requests if url.rstrip("/").endswith("/x")]
        page.screenshot(path=str(screenshot_dir / "f3-launch-hostile-and-preflight.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()


def test_launch_screen_at_phone_width(studio_url: str, screenshot_dir: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright, "zero-webgl")
        session = Session(browser, f"{studio_url}/#/launch", viewport={"width": 400, "height": 800})
        page = session.page
        session.row("ocarina").wait_for()
        overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        assert overflow <= 0
        page.screenshot(path=str(screenshot_dir / "f3-launch-400px.png"), full_page=True)
        assert session.errors == []
        session.close()
        browser.close()
