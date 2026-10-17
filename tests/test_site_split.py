"""Summary navigation, static destination parity and fail-closed link checking."""
import importlib.util
import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'site'


def test_internal_links_and_checker_negative_control(tmp_path):
    spec = importlib.util.spec_from_file_location('site_links', SITE / 'check_links.py')
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    assert checker.check_links(SITE) == []
    (tmp_path / 'index.html').write_text('<a href="missing.html">File</a><a href="#missing">Fragment</a>')
    errors = checker.check_links(tmp_path)
    assert len(errors) == 2
    assert any('missing file' in e for e in errors)
    assert any('missing fragment' in e for e in errors)


@pytest.mark.parametrize('javascript', [False, True])
@pytest.mark.parametrize('width', [1440, 390])
def test_summary_and_destinations(javascript, width):
    playwright = pytest.importorskip('playwright.sync_api')

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(SITE)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(args=os.environ.get('ARENA_STUDIO_BROWSER_ARGS', '').split())
            page = browser.new_page(java_script_enabled=javascript, viewport={'width': width, 'height': 1000})
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            base = f'http://127.0.0.1:{server.server_port}/'
            page.goto(base, wait_until='networkidle')
            assert page.locator('#leaderboard-teaser tbody tr').count() == 10
            assert page.locator('#lb-container').count() == 0
            if width == 1440 and (SITE / 'charts.html').exists():
                assert page.evaluate('document.documentElement.scrollHeight') < 8000
            data = json.loads((SITE / 'data/leaderboard.json').read_text())
            matchups = json.loads((SITE / 'data/matchups.json').read_text())['matchups']
            for name, selector, count in (
                ('tasks', '#task-grid .task', len(data['task_families'])),
                ('matchups', '#matchups article.arena-card', len(matchups)),
                ('landscape', '#lscape-container', 1),
                ('arena', '#arena-runs', 1),
            ):
                page.goto(base + '#' + name)
                page.locator(f'#{name} a[href="{name}.html"]').click()
                assert page.url == base + name + '.html'
                assert page.locator(selector).count() == count
                assert page.locator(selector).first.is_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.goto(base + 'matchups.html')
            assert page.locator('#matchups tbody tr').count() == sum(len(m['entrants']) for m in matchups)
            if javascript:
                page.goto(base + 'tasks.html', wait_until='networkidle')
                page.locator('#theme-toggle').click()
                assert page.locator('#task-grid .task').count() == len(data['task_families'])
                page.goto(base + 'landscape.html', wait_until='networkidle')
                assert page.locator('#lscape-controls input, #lscape-controls select, #lscape-controls button').count() > 0
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
