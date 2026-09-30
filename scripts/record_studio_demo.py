#!/usr/bin/env python3
"""record_studio_demo.py: scripted ~60 s Arena Studio walkthrough, recorded to a GIF.

Starts Arena Studio (no --allow-live, so nothing can launch a model) on a throwaway
copy of the repo layout that holds only the run directories you pass in, drives it
with Playwright, records the page, and converts the video to a GIF with ffmpeg.

The walkthrough (about 60 s): the Runs list, two run detail panels, then the DoE
matrix in "Vary one axis" mode: vary the model, pick an instrument, name two
entrants, and read the preview's held values and $0 subscription estimates. The results
step is the Agreement analytics screen, whose "People versus the objective checks" table
shows each entrant's objective pass rate and trial count. It never opens the voting or
morning-review screens. Only run_log.json and preview PNGs are staged (no vote or judge
files), so the human-Elo columns read "Unknown" and the script aborts if any human
rating appears; no preference number can be recorded.

Requirements (not installed by MakerBench): `pip install playwright`, a Chromium
that Playwright can launch, and `ffmpeg`. Nothing is sent over the network beyond
127.0.0.1 and no provider CLI is invoked.

Usage:
    python3 scripts/record_studio_demo.py RUN_DIR [RUN_DIR ...] --out demo.gif
        [--width 800] [--fps 8] [--keep-video PATH]
"""
from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VIEWPORT = {"width": 1280, "height": 720}
CAPTION_JS = """
(text) => {
  let el = document.getElementById('demo-caption');
  if (!el) {
    el = document.createElement('div');
    el.id = 'demo-caption';
    el.setAttribute('role', 'presentation');
    el.style.cssText = 'position:fixed;left:16px;right:16px;bottom:16px;z-index:99999;' +
      'padding:10px 14px;border-radius:8px;background:rgba(20,24,28,.92);color:#fff;' +
      'font:600 18px system-ui,sans-serif;text-align:center;pointer-events:none';
    document.body.appendChild(el);
  }
  el.textContent = text;
}
"""

# (caption, seconds to hold after the step). Sums to roughly 60 s of holds plus typing.
STEPS_HOLD = {
    "runs": 4.0,
    "run_a": 4.0,
    "results": 10.0,
    "doe": 3.0,
    "mode": 4.0,
    "axis": 4.0,
    "instrument": 3.0,
    "entrants": 3.0,
    "preview": 10.0,
}


def stage_repo(run_dirs: list[Path], work: Path) -> Path:
    """Copy the registry and the given runs into a scratch repo root."""

    (work / "tasks" / "code_cad_arena").mkdir(parents=True)
    shutil.copyfile(ROOT / "tasks" / "code_cad_arena" / "registry.json", work / "tasks" / "code_cad_arena" / "registry.json")
    runs = work / "runs" / "code_cad_arena"
    runs.mkdir(parents=True)
    for run_dir in run_dirs:
        if not (run_dir / "run_log.json").is_file():
            raise FileNotFoundError(f"no run_log.json in {run_dir}")
        target = runs / run_dir.name
        target.mkdir()
        shutil.copyfile(run_dir / "run_log.json", target / "run_log.json")
        for preview in sorted(run_dir.glob("render/*/preview.png")):  # explicit allowlist
            dest = target / preview.relative_to(run_dir)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(preview, dest)
    return work


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_server(repo: Path):
    import uvicorn

    from makerbench.arena_studio import create_studio_app

    app = create_studio_app(registry_path=repo / "tasks" / "code_cad_arena" / "registry.json", repo_root=repo)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("Studio did not start")
        time.sleep(0.05)
    return server, thread, f"http://127.0.0.1:{port}"


def record_walkthrough(url: str, run_names: list[str], video_dir: Path) -> Path:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport=VIEWPORT, record_video_dir=str(video_dir), record_video_size=VIEWPORT)
        page = context.new_page()

        def say(text: str, hold: float) -> None:
            page.evaluate(CAPTION_JS, text)
            page.wait_for_timeout(int(hold * 1000))

        page.goto(f"{url}/#/runs")
        page.get_by_role("link", name=run_names[0]).wait_for()
        say("Arena Studio: every run on disk, listed locally", STEPS_HOLD["runs"])
        page.get_by_role("link", name=run_names[0]).click()
        say(f"Open a run: its entrants, instruments and trials ({run_names[0]})", STEPS_HOLD["run_a"])
        page.get_by_role("link", name="Agreement analytics").first.click()
        table = page.locator("table").first
        table.wait_for()
        page.mouse.move(700, 400)
        page.mouse.wheel(0, 340)
        page.wait_for_timeout(600)
        try:
            page.get_by_text("No human votes yet").first.wait_for(timeout=10000)
        except Exception as error:
            raise RuntimeError("a human rating may be visible; refusing to record preference data") from error
        say("Results: each entrant's objective pass rate over its trials (no votes involved)", STEPS_HOLD["results"])
        page.get_by_role("link", name="DoE matrix").click()
        page.locator("select[name=experiment_mode]").wait_for()
        say("DoE matrix: design an experiment; nothing runs from this page", STEPS_HOLD["doe"])
        page.select_option("select[name=experiment_mode]", "matchup")
        say("Experiment mode: Vary one axis", STEPS_HOLD["mode"])
        page.select_option("select[name=varied_axis]", "models")
        say("Choose the axis to vary: the model. Everything else is held", STEPS_HOLD["axis"])
        page.locator("input[name=instrument][value=ocarina]").check()
        say("Hold one instrument: the ocarina", STEPS_HOLD["instrument"])
        page.fill("textarea[name=models]", "")
        page.type("textarea[name=models]", "claude-code-opus-5.5, claude-code-sonnet-5.5", delay=45)
        say("Name the two entrants to compare", STEPS_HOLD["entrants"])
        preview = page.locator(".doe-preview")
        preview.wait_for()
        preview.scroll_into_view_if_needed()
        say("The preview lists the held values and the cost: $0, subscription CLIs", STEPS_HOLD["preview"])
        video = page.video
        context.close()
        path = Path(video.path())
        browser.close()
    return path


def to_gif(video: Path, out: Path, *, width: int, fps: int) -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required to build the GIF")
    palette = out.with_suffix(".palette.png")
    filters = f"fps={fps},scale={width}:-1:flags=lanczos"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf", f"{filters},palettegen=stats_mode=diff", str(palette)], check=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(palette), "-lavfi", f"{filters}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4", str(out)],
        check=True,
    )
    palette.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--width", type=int, default=800)
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument("--keep-video", type=Path, default=None)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="mb-studio-demo-") as tmp:
        work = Path(tmp)
        repo = stage_repo([p.resolve() for p in args.run_dirs], work / "repo")
        server, thread, url = _start_server(repo)
        try:
            video = record_walkthrough(url, [p.name for p in args.run_dirs], work / "video")
        finally:
            server.should_exit = True
            thread.join(timeout=10)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        to_gif(video, args.out, width=args.width, fps=args.fps)
        if args.keep_video:
            shutil.copyfile(video, args.keep_video)
    print(f"wrote {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
