"""Real Chromium Live layout/actions; same opt-in dependencies as Review."""
import os
import socket
import threading
import time
from pathlib import Path

import pytest

if os.environ.get('ARENA_STUDIO_REQUIRE_BROWSER') == '1':
    from playwright.sync_api import sync_playwright
else:
    sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright
import uvicorn

from makerbench.arena_studio import create_studio_app
from test_control_room_live import build_live_repo


def test_live_layout_polling_stop_and_transcript(tmp_path):
    repo = build_live_repo(tmp_path)
    app = create_studio_app(repo_root=repo, registry_path=repo / 'registry.json', control_room_queue_writes=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    assert port != 8080
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        assert time.monotonic() < deadline
        time.sleep(.05)
    target = Path(os.environ.get('ARENA_STUDIO_SCREENSHOTS', tmp_path / 'screens'))
    target.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=['--disable-webgl', '--disable-webgl2'])
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            for width in (1440, 390):
                for theme in ('light', 'dark'):
                    page.set_viewport_size({'width': width, 'height': 1000})
                    page.emulate_media(color_scheme=theme)
                    page.goto(f'http://127.0.0.1:{port}/#/live')
                    page.locator('.live-lane').nth(3).wait_for()
                    assert page.locator('.live-lane[data-state="running"]').count() == 1
                    assert page.locator('canvas').count() == 0
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    page.screenshot(path=str(target / f'live-{width}-{theme}.png'), full_page=True)
            transcript = next((repo / 'runs').glob('*/gen/*/build_transcript.txt'))
            transcript.write_text('fixture: waiting for export\nfixture: poll update')
            page.get_by_text('fixture: poll update', exact=False).wait_for(timeout=10000)
            page.get_by_role('button', name='Open full transcript').nth(2).click()
            assert 'fixture: waiting for export' in page.locator('.live-transcript').inner_text()
            page.get_by_role('button', name='Stop after this entrant', exact=True).click()
            page.get_by_role('button', name='Keep going', exact=True).click()
            assert page.locator('.live-confirm').count() == 0
            page.get_by_role('button', name='Stop after this entrant', exact=True).click()
            page.get_by_role('button', name='Yes, stop after this entrant', exact=True).click()
            page.get_by_role('button', name='Stopping after this entrant…', exact=True).wait_for()
            assert not errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
