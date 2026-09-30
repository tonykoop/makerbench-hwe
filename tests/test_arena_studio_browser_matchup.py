"""Real Studio matchup renders, gates and honest timing in Chromium (#842)."""
import os
import socket
import threading
import time

import pytest

REQUIRED = os.environ.get('ARENA_STUDIO_REQUIRE_BROWSER') == '1'
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if REQUIRED:
        raise
    pytest.skip('Playwright is not installed', allow_module_level=True)

uvicorn = pytest.importorskip('uvicorn')
from makerbench.arena_studio import create_studio_app  # noqa: E402
from test_arena_studio_matchup_view import make_matchup_repo  # noqa: E402


@pytest.fixture
def matchup_url(tmp_path):
    make_matchup_repo(tmp_path)
    app = create_studio_app(registry_path=tmp_path/'registry.json', repo_root=tmp_path)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError('Studio fixture did not start')
        time.sleep(.05)
    try:
        yield f'http://127.0.0.1:{port}/#/runs/matchup-fixture'
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [1600, 390])
def test_matchup_side_by_side_renders_gates_and_recorded_time(matchup_url, tmp_path, theme, width):
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=['--disable-webgl', '--disable-webgl2'])
        except Exception:
            if REQUIRED:
                raise
            pytest.skip('Chromium is unavailable')
        context = browser.new_context(viewport={'width': width, 'height': 1000}, color_scheme=theme)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda exc: errors.append(str(exc)))
        page.goto(matchup_url)
        section = page.locator('.matchup-results')
        section.wait_for(state='visible')
        assert 'Varied: models' in section.inner_text()
        assert 'seeds: 0' in section.inner_text()
        cards = section.locator('.matchup-entrant')
        assert cards.count() == 2
        assert cards.locator('h4').all_text_contents() == ['stub-a', 'stub-b']
        assert '2.50 s' in cards.nth(0).inner_text()
        assert 'min_wall: Fail' in cards.nth(0).inner_text()
        assert '83.33%' in cards.nth(0).inner_text()
        assert 'Not recorded' in cards.nth(1).inner_text()
        page.wait_for_function("Array.from(document.querySelectorAll('.matchup-render')).every(i => i.complete && i.naturalWidth > 0)")
        assert section.locator('.matchup-render').count() == 2
        if width == 1600:
            left, right = cards.nth(0).bounding_box(), cards.nth(1).bounding_box()
            assert right['x'] > left['x']
            assert abs(left['y'] - right['y']) < 2
        else:
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        assert not errors
        destination = os.environ.get('ARENA_STUDIO_SCREENSHOTS')
        if destination:
            from pathlib import Path
            output = Path(destination)
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output/f'matchup-{theme}-{width}.png'), full_page=True, animations='disabled')
        context.close()
        browser.close()
