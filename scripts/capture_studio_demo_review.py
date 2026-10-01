#!/usr/bin/env python3
"""Capture the read-only public demo on loopback for Tony's visual review."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit

import uvicorn
from playwright.sync_api import sync_playwright

from makerbench.arena_studio.demo import create_demo_app

ROOT = Path(__file__).resolve().parents[1]
CASES = (("post3-models", "Comparing AI models", 2),
         ("post3-backends", "Comparing CAD tools", 3),
         ("strings-gallery", "String-instrument gallery", 18),
         ("kora", "Kora: text brief and reference photo", 6))


def fingerprint(root):
    snapshot = json.loads((root / "makerbench/arena_studio/data/showcase.json").read_text())
    paths = {root / "makerbench/arena_studio/demo.py", root / "makerbench/arena_studio/data/showcase.json"}
    paths.update(path for path in (root / "makerbench/arena_studio/static").rglob("*") if path.is_file())
    paths.update(root / relative for relative in snapshot["assets"].values())
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def capture(out: Path, root: Path = ROOT):
    if out.exists():
        raise ValueError("Use a fresh output directory; existing screenshots are never reused")
    before = fingerprint(root)
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    dirty = bool(subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"]))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_demo_app(), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    out.mkdir(parents=True)
    shots = []
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() >= deadline or not thread.is_alive():
                raise RuntimeError("Loopback demo server did not start")
            time.sleep(.05)
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch()
            browser_version = browser.version
            try:
                for width, height in ((1366, 900), (390, 844)):
                    for case, title, rows in CASES:
                        page = browser.new_page(viewport={"width": width, "height": height},
                                                device_scale_factor=1, color_scheme="light",
                                                reduced_motion="reduce")
                        errors = []
                        requests = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.on("request", lambda request: requests.append(request.url))
                        page.goto(f"http://127.0.0.1:{port}/#/runs/{case}")
                        page.get_by_role("heading", name=title, exact=True).wait_for()
                        page.wait_for_function("document.querySelector('.server-status').textContent.startsWith('Server ')")
                        page.wait_for_function("Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)")
                        page.evaluate("document.fonts.ready")
                        if page.locator(".matchup-entrant").count() != rows:
                            raise RuntimeError("Unexpected demo row count")
                        if not page.evaluate("document.documentElement.scrollWidth <= innerWidth"):
                            raise RuntimeError("Demo overflows the viewport")
                        if errors or not requests or any(urlsplit(url).netloc != f"127.0.0.1:{port}" for url in requests):
                            raise RuntimeError("Demo had a browser error or made a non-loopback request")
                        filename = f"{case}-{width}.png"
                        page.screenshot(path=str(out / filename), full_page=True)
                        shots.append({"file": filename, "case": case, "viewport": {"width": width, "height": height},
                                      "full_page": True, "sha256": hashlib.sha256((out / filename).read_bytes()).hexdigest(),
                                      "loaded_images": page.locator("img").count(), "browser_errors": 0,
                                      "non_loopback_requests": 0})
                        page.close()
            finally:
                browser.close()
        if fingerprint(root) != before:
            raise RuntimeError("Demo inputs changed during capture")
        manifest = {"schema": "makerbench-studio-visual-review-v1", "capture_base_head": head,
                    "source_tree_dirty": dirty, "playwright": importlib.metadata.version("playwright"),
                    "chromium": browser_version, "runtime_sha256": before, "screenshots": shots,
                    "publication": "Pending Tony's visual approval; local capture only"}
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return manifest
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest = capture(args.out)
    print(f"Captured {len(manifest['screenshots'])} desktop/phone views locally; publication awaits Tony")


if __name__ == "__main__":
    main()
