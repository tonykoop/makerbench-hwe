"""Phone menu, keyboard access, no-JS parity, stable anchors and fetch errors."""
import os
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

playwright = pytest.importorskip('playwright.sync_api')
ROOT = Path(__file__).resolve().parents[1]
PRIMARY = ['Leaderboard', 'Matchups', 'Charts', 'Tasks', 'Arena', 'About']


@pytest.fixture(scope='module')
def base():
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(ROOT / 'site')))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/'
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def browser():
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(args=os.environ.get('ARENA_STUDIO_BROWSER_ARGS', '').split())
        yield browser
        browser.close()


@pytest.mark.parametrize('width', [390, 720])
def test_phone_menu_keyboard_focus_escape_and_link(base, browser, width):
    page = browser.new_page(viewport={'width': width, 'height': 1000})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(base, wait_until='networkidle')
    menu = page.get_by_role('button', name='Menu', exact=True)
    nav = page.get_by_role('navigation', name='Primary', exact=True, include_hidden=True)
    assert menu.is_visible()
    assert menu.get_attribute('aria-controls') == nav.get_attribute('id')
    assert menu.get_attribute('aria-expanded') == 'false'
    assert not nav.is_visible()
    menu.focus()
    page.keyboard.press('Enter')
    assert menu.get_attribute('aria-expanded') == 'true'
    assert nav.is_visible()
    assert nav.locator(':scope > a').all_text_contents() == PRIMARY
    assert page.evaluate("document.activeElement.textContent") == 'Leaderboard'
    assert page.locator('.hero').evaluate('node => node.inert')
    nav.locator('summary').focus()
    page.keyboard.press('Enter')
    assert nav.get_by_role('link', name='Methodology', exact=True).is_visible()
    for _ in range(40):
        page.keyboard.press('Tab')
        assert page.evaluate("document.querySelector('.site-nav').contains(document.activeElement)")
    for _ in range(40):
        page.keyboard.press('Shift+Tab')
        assert page.evaluate("document.querySelector('.site-nav').contains(document.activeElement)")
    page.keyboard.press('Escape')
    assert menu.get_attribute('aria-expanded') == 'false'
    assert menu.evaluate('node => node === document.activeElement')
    assert not page.locator('.hero').evaluate('node => node.inert')
    menu.click()
    menu.click()
    assert not nav.is_visible()
    menu.click()
    nav.get_by_role('link', name='Matchups', exact=True).click()
    assert page.url == base + 'matchups.html'
    assert page.locator('#matchups article').count() == 132
    assert not page.get_by_role('navigation', name='Primary', exact=True).is_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    assert not errors
    page.close()


@pytest.mark.parametrize('javascript', [False, True])
@pytest.mark.parametrize('path', ['', 'blog/', 'tasks/vented_plate/', 'opportunity-matrix.html', 'explorer.html'])
def test_same_header_and_more_on_root_blog_and_generated_pages(base, browser, javascript, path):
    page = browser.new_page(java_script_enabled=javascript, viewport={'width': 1440, 'height': 1000})
    page.goto(base + path, wait_until='networkidle')
    nav = page.get_by_role('navigation', name='Primary', exact=True)
    assert nav.locator(':scope > a').all_text_contents() == PRIMARY
    assert not page.get_by_role('button', name='Menu', exact=True).is_visible()
    boxes = nav.locator(':scope > a').evaluate_all('nodes => nodes.map(n => n.getBoundingClientRect().top)')
    assert max(boxes) - min(boxes) < 2
    more = nav.locator('summary')
    more.focus()
    page.keyboard.press('Enter')
    methodology = nav.get_by_role('link', name='Methodology', exact=True)
    assert methodology.is_visible()
    assert methodology.get_attribute('href').endswith('index.html#methodology')
    nav.get_by_role('link', name='Landscape', exact=True).click()
    assert page.url == base + 'landscape.html'
    if javascript:
        page.get_by_role('navigation', name='Primary', exact=True).locator('summary').click()
        page.keyboard.press('Escape')
        assert page.locator('.site-nav-more').get_attribute('open') is None
        theme = page.get_by_role('button', name='Toggle light / dark')
        previous = page.locator('html').get_attribute('data-theme')
        theme.click()
        assert page.locator('html').get_attribute('data-theme') != previous
    page.close()


def test_no_javascript_phone_navigation_is_visible_and_usable(base, browser):
    page = browser.new_page(java_script_enabled=False, viewport={'width': 390, 'height': 1000})
    page.goto(base)
    nav = page.get_by_role('navigation', name='Primary', exact=True)
    assert nav.is_visible()
    assert not page.get_by_role('button', name='Menu', exact=True).is_visible()
    nav.locator('summary').click()
    nav.get_by_role('link', name='Landscape', exact=True).click()
    assert page.url == base + 'landscape.html'
    page.close()


@pytest.mark.parametrize('hash_name', ['landscape', 'methodology', 'about'])
def test_legacy_hash_stays_aligned_after_delayed_hydration(base, browser, hash_name):
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})

    def delay(route):
        time.sleep(0.25)
        route.continue_()
    page.route('**/data/leaderboard.json', delay)
    page.route('**/data/findings.json', delay)
    page.goto(base + '#' + hash_name, wait_until='networkidle')
    page.wait_for_function("""id => {
      const target = document.getElementById(id).getBoundingClientRect();
      const header = document.querySelector('.site-nav').getBoundingClientRect();
      const remaining = document.documentElement.scrollHeight - (scrollY + target.top);
      const expected = Math.max(header.bottom + 12, innerHeight - remaining);
      return Math.abs(target.top - expected) <= 2;
    }""", arg=hash_name)
    if hash_name != 'landscape':
        assert page.locator('#' + hash_name + ' > details').get_attribute('open') is not None
    page.close()


@pytest.mark.parametrize('path, selector', [('', '#leaderboard-teaser tbody tr'), ('leaderboard.html', '#lb-container .lb-primary'), ('tasks.html', '#task-grid .task')])
def test_fetch_failure_is_visible_without_destroying_static_results(base, browser, path, selector):
    page = browser.new_page()
    page.route('**/data/leaderboard.json', lambda route: route.fulfill(status=503, body='unavailable'))
    page.goto(base + path, wait_until='networkidle')
    status = page.get_by_role('status')
    assert status.is_visible()
    assert 'Showing the last published snapshot' in status.inner_text()
    assert page.locator(selector).count() > 0
    assert page.locator(selector).first.is_visible()
    page.close()


def test_phone_menu_does_not_wait_for_chart_library_loading(base, browser):
    page = browser.new_page(viewport={'width': 390, 'height': 1000})
    pending = []
    page.route('**/chart.umd.min.js', lambda route: pending.append(route))
    try:
        page.goto(base + 'landscape.html', wait_until='commit')
        page.locator('script[src*="chart.umd.min.js"]').wait_for(state='attached', timeout=5000)
        menu = page.get_by_role('button', name='Menu', exact=True)
        menu.wait_for(state='visible', timeout=3000)
        assert pending, 'Chart.js must still be blocked for this regression control'
        assert page.evaluate('document.readyState') == 'loading'
        menu.click()
        assert menu.get_attribute('aria-expanded') == 'true'
        page.keyboard.press('Escape')
        assert menu.get_attribute('aria-expanded') == 'false'
        assert menu.evaluate('node => node === document.activeElement')
    finally:
        for route in pending:
            route.abort()
        page.close()
