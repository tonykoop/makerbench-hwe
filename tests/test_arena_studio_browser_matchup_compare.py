"""Side-by-side matchup compare with one shared camera (#974), in Chromium."""
import json
import os
import socket
import threading
import time
from pathlib import Path

import pytest

REQUIRED = os.environ.get("ARENA_STUDIO_REQUIRE_BROWSER") == "1"
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if REQUIRED:
        raise
    pytest.skip("Playwright is not installed", allow_module_level=True)

uvicorn = pytest.importorskip("uvicorn")
trimesh = pytest.importorskip("trimesh")
from makerbench.arena_studio import create_studio_app  # noqa: E402
from test_arena_studio_matchup_view import make_matchup_repo  # noqa: E402

GPU_OFF = ["--disable-3d-apis", "--disable-webgl", "--disable-webgl2"]


def _serve(tmp_path, meshes: bool):
    run = make_matchup_repo(tmp_path)
    if meshes:
        payload = json.loads((run / "run_log.json").read_text())
        for n, trial in enumerate(payload["trials"]):
            stl = run / f"mesh-{n}.stl"
            # Two different solids, so the shared camera is visibly shared.
            shape = trimesh.creation.box(extents=(10, 10, 10)) if n == 0 else trimesh.creation.cylinder(radius=6, height=24)
            shape.export(stl)
            trial["result"]["artifacts"]["stl_path"] = str(stl)
        (run / "run_log.json").write_text(json.dumps(payload))
    app = create_studio_app(registry_path=tmp_path / "registry.json", repo_root=tmp_path)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("Studio fixture did not start")
        time.sleep(0.05)
    return server, thread, f"http://127.0.0.1:{port}/#/runs/matchup-fixture"


@pytest.fixture
def with_meshes(tmp_path):
    server, thread, url = _serve(tmp_path, True)
    yield url
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def without_meshes(tmp_path):
    server, thread, url = _serve(tmp_path, False)
    yield url
    server.should_exit = True
    thread.join(timeout=10)


def _launch(playwright, args):
    try:
        return playwright.chromium.launch(args=[*args, *os.environ.get("ARENA_STUDIO_BROWSER_ARGS", "").split()])
    except Exception:
        if REQUIRED:
            raise
        pytest.skip("Chromium is unavailable")


def _open_compare(page, url):
    page.goto(url)
    section = page.locator(".matchup-results")
    section.wait_for()
    launch = section.get_by_role("button", name="Compare side by side")
    assert launch.get_attribute("aria-disabled") == "true"
    for trial in ("fixture-0", "fixture-1"):
        section.locator(f"[data-compare-trial='{trial}']").check()
    assert launch.get_attribute("aria-disabled") == "false"
    launch.click()
    compare = page.locator(".matchup-compare")
    compare.wait_for()
    page.wait_for_function("() => document.activeElement?.id === 'matchup-compare-title'")
    return compare


def _assert_aligned_chips(compare):
    table = compare.locator("table.compare-checks")
    assert table.locator("thead th").all_inner_texts() == ["Check", "stub-a", "stub-b"]
    rows = {row.get_attribute("data-check"): row for row in table.locator("tbody tr").all()}
    assert list(rows) == ["renders", "watertight", "nonzero_volume", "body_count", "fits_envelope", "min_wall"]
    assert rows["min_wall"].locator(".check-chip").all_inner_texts() == ["Fail", "Pass"]
    assert rows["min_wall"].get_attribute("data-differs") == "true"
    assert rows["renders"].locator(".check-chip").all_inner_texts() == ["Pass", "Pass"]
    # One row per check: each entrant's chip for a check shares the row's top.
    for row in rows.values():
        tops = [chip.bounding_box()["y"] for chip in row.locator(".check-chip").all()]
        assert len(tops) == 2 and abs(tops[0] - tops[1]) < 1


_CAMERAS = """() => [...document.querySelectorAll('.matchup-compare model-viewer')].map(el => {
  const o = el.getCameraOrbit(), t = el.getCameraTarget();
  return [o.theta, o.phi, o.radius, t.x, t.y, t.z, el.getFieldOfView()];
})"""


def _cameras(page):
    return page.evaluate(_CAMERAS)


def _assert_same(cameras):
    assert len(cameras) == 2
    for a, b in zip(*cameras):
        assert abs(a - b) <= 1e-4 * max(1, abs(a)), cameras


def _settle(page):
    """Wait until the shared camera stops moving (damping after input)."""
    last = None
    for _ in range(40):
        page.wait_for_timeout(150)
        now = _cameras(page)
        if now == last:
            return now
        last = now
    return last


def _drag(page, locator, dx, dy):
    locator.scroll_into_view_if_needed()
    box = locator.bounding_box()
    # Off-centre: a drag that starts on a hotspot marker (the min-wall marker
    # sits mid-model) belongs to the marker, by model-viewer's design.
    x, y = box["x"] + box["width"] * 0.3, box["y"] + box["height"] * 0.3
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx, y + dy, steps=8)
    page.mouse.up()


@pytest.mark.parametrize("theme,width", [("light", 1440), ("dark", 390)])
def test_both_views_share_one_camera_and_chips_align(with_meshes, theme, width):
    with sync_playwright() as playwright:
        browser = _launch(playwright, [])
        context = browser.new_context(viewport={"width": width, "height": 1000}, color_scheme=theme)
        page = context.new_page()
        errors, requests = [], []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("request", lambda request: requests.append(request.url))
        compare = _open_compare(page, with_meshes)
        _assert_aligned_chips(compare)
        # Renders first; nothing 3D loads until asked.
        assert compare.locator("img.compare-render").count() == 2
        assert not [url for url in requests if "model-viewer" in url or "/matchup-mesh/" in url]

        compare.get_by_role("button", name="3D, one camera").click()
        page.wait_for_function("() => document.querySelector('.matchup-compare')?.dataset.synced === 'true'", timeout=30_000)
        viewers = compare.locator("model-viewer")
        assert viewers.count() == 2
        assert viewers.nth(0).evaluate("el => el.autoRotate") is False
        start = _settle(page)
        _assert_same(start)

        # Orbit the left view: the right one follows.
        _drag(page, viewers.nth(0), 90, 0)
        after_left = _settle(page)
        _assert_same(after_left)
        assert abs(after_left[0][0] - start[0][0]) > 0.05, "the drag turned the camera"
        # Tilt the right view: the left one follows.
        _drag(page, viewers.nth(1), 0, -60)
        after_right = _settle(page)
        _assert_same(after_right)
        assert abs(after_right[0][1] - after_left[0][1]) > 0.05
        # Zoom the right view with the wheel.
        viewers.nth(1).hover()
        page.mouse.wheel(0, -250)  # zoom in
        zoomed = _settle(page)
        _assert_same(zoomed)
        assert abs(zoomed[0][2] - after_right[0][2]) > 1e-3

        # The #975 dimension overlay reads each entrant's own mesh.
        overlays = compare.locator(".measure-overlay")
        overlays.nth(1).locator(".measure-row").first.wait_for(timeout=30_000)
        overlays.nth(0).locator(".measure-row").first.wait_for(timeout=30_000)
        sizes = [o.locator(".measure-row[data-metric=bbox] dd").inner_text() for o in overlays.all()]
        assert sizes[0] == "10.0 × 10.0 × 10.0 mm"
        assert sizes[1].startswith("12.0 × 12.0 × 24.0") or sizes[1].endswith("× 24.0 mm"), sizes

        assert page.evaluate("() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth") <= 0
        if width >= 1000:
            left, right = (viewers.nth(i).bounding_box() for i in (0, 1))
            assert right["x"] > left["x"] and abs(left["y"] - right["y"]) < 2, "side by side"
        destination = os.environ.get("ARENA_STUDIO_SCREENSHOTS")
        if destination:
            output = Path(destination)
            output.mkdir(parents=True, exist_ok=True)
            compare.scroll_into_view_if_needed()
            page.screenshot(path=str(output / f"compare-3d-{theme}-{width}.png"), full_page=True)
        compare.get_by_role("button", name="Close compare").click()
        page.wait_for_function("() => (document.activeElement?.textContent || '').trim() === 'Compare side by side'")
        assert page.locator(".matchup-compare").count() == 0
        assert errors == []
        context.close()
        browser.close()


def test_zero_webgl_compares_renders_with_the_same_chips(with_meshes):
    with sync_playwright() as playwright:
        browser = _launch(playwright, GPU_OFF)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors, requests = [], []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("request", lambda request: requests.append(request.url))
        compare = _open_compare(page, with_meshes)
        three_d = compare.get_by_role("button", name="3D, one camera")
        assert three_d.get_attribute("aria-disabled") == "true"
        assert "3D needs WebGL" in compare.inner_text()
        three_d.dispatch_event("click")
        page.wait_for_timeout(300)
        assert compare.locator("model-viewer").count() == 0
        page.wait_for_function("() => [...document.querySelectorAll('.compare-render')].every(i => i.complete && i.naturalWidth > 0)")
        assert compare.locator("img.compare-render").count() == 2
        _assert_aligned_chips(compare)
        assert not [url for url in requests if "model-viewer" in url or "/matchup-mesh/" in url or "/matchup-dimensions/" in url]
        destination = os.environ.get("ARENA_STUDIO_SCREENSHOTS")
        if destination:
            Path(destination).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(destination) / "compare-zero-webgl-1440.png"), full_page=True)
        assert errors == []
        context.close()
        browser.close()


def test_without_meshes_the_renders_sit_side_by_side(without_meshes):
    with sync_playwright() as playwright:
        browser = _launch(playwright, [])
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        compare = _open_compare(page, without_meshes)
        assert compare.get_by_role("button", name="3D, one camera").get_attribute("aria-disabled") == "true"
        assert "needs both trials' meshes" in compare.inner_text()
        assert compare.locator("img.compare-render").count() == 2
        _assert_aligned_chips(compare)
        context.close()
        browser.close()
