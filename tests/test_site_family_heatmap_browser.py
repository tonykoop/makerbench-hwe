"""Model × family heatmap: public cell parity, selection and readable structure."""

import copy
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

playwright = pytest.importorskip('playwright.sync_api')
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def heatmap_page():
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(ROOT / 'site')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f'http://127.0.0.1:{server.server_port}/charts.html', wait_until='networkidle')
            page.wait_for_selector('.family-heatmap-table')
            yield page
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


def _rows(page):
    return page.locator('.family-heatmap-table tbody tr').evaluate_all('''rows => rows.map(row => ({
      id: row.dataset.modelRowId,
      cells: Array.from(row.querySelectorAll('.heatmap-cell')).map(cell => ({
        family: cell.dataset.familyId, score: cell.hasAttribute('data-score') ? Number(cell.dataset.score) : null
      }))
    }))''')


@pytest.mark.parametrize('width', [1440, 390])
@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('track', ['blind', 'perception'])
def test_real_heatmap_values_selection_structure_and_contrast(heatmap_page, width, theme, track):
    page = heatmap_page
    page.set_viewport_size({'width': width, 'height': 1000})
    page.locator('.heatmap-show-all input').uncheck()
    if page.locator('html').get_attribute('data-theme') != theme:
        page.locator('#theme-toggle').click()
    page.locator(f'#track-toggle button[data-track="{track}"]').click()
    payload = json.loads((ROOT / 'site/data/leaderboard.json').read_text())
    models = [m for m in payload['models'] if m.get('tracks', {}).get(track, {}).get('has_data')]
    active = [f['id'] for f in payload['task_families'] if any(
        (m['tracks'][track]['families'].get(f['id']) or {}).get('mean_score') is not None for m in models)]
    assert page.locator('.heatmap-family-heading').evaluate_all('nodes => nodes.map(n => n.dataset.familyId)') == active
    names = {f['id']: f['title'] for f in payload['task_families']}
    assert page.locator('.heatmap-family-heading').evaluate_all(
        'nodes => nodes.map(n => [n.title, n.getAttribute("aria-label")])'
    ) == [[names[f], names[f]] for f in active]
    assert f"{len(payload['task_families']) - len(active)} empty families hidden" in page.locator('.heatmap-summary').inner_text()
    selected = _rows(page)
    assert len(selected) == min(12, len(models))
    model_by_id = {m['row_id']: m for m in models}
    selected_coverage = [model_by_id[r['id']]['tracks'][track]['n_families_scored'] for r in selected]
    assert selected_coverage == sorted([m['tracks'][track]['n_families_scored'] for m in models], reverse=True)[:12]
    page.locator('.heatmap-show-all input').check()
    all_rows = _rows(page)
    assert len(all_rows) == len(models)
    assert all_rows[:12] == selected
    assert {r['id'] for r in all_rows} == set(model_by_id)
    disclosures = page.locator('.family-heatmap-table tbody th').evaluate_all(
        'nodes => nodes.map(n => ({text:n.innerText, title:n.title}))')
    for row, disclosure in zip(all_rows, disclosures):
        if model_by_id[row['id']]['reasoning_level'] is None:
            assert 'Unspecified' not in disclosure['text']
            assert 'Unspecified' not in disclosure['title']
    for row in all_rows:
        values = model_by_id[row['id']]['tracks'][track]['families']
        assert row['cells'] == [dict(family=f, score=(values.get(f) or {}).get('mean_score')) for f in active]
    result = page.evaluate('''width => {
      const root = document.querySelector('#family-heatmap');
      const wrap = root.querySelector('.heatmap-scroll');
      const luminance = color => {
        const rgb = color.match(/[\\d.]+/g).slice(0,3).map(Number).map(v => v/255)
          .map(v => v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4);
        return .2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2];
      };
      const ratio = (a,b) => (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
      const card = root.closest('.card');
      const background = luminance(getComputedStyle(card).backgroundColor);
      return {
        contrast: Array.from(root.querySelectorAll('[data-score], .heatmap-scale-score')).every(cell => {
          const css = getComputedStyle(cell);
          return ratio(luminance(css.color),luminance(css.backgroundColor)) >= 4.5;
        }),
        labelContrast: Array.from(card.querySelectorAll('.sub, .chart-note, th, small, caption, .heatmap-missing-key')).every(el =>
          ratio(luminance(getComputedStyle(el).color),background) >= 4.5),
        fits: document.documentElement.scrollWidth <= innerWidth+1 &&
          wrap.getBoundingClientRect().right <= innerWidth,
        cellSize: Array.from(root.querySelectorAll('.heatmap-cell')).every(cell =>
          cell.getBoundingClientRect().width >= (width === 1440 ? 44 : 64) && cell.getBoundingClientRect().height >= 44),
        headers: Array.from(root.querySelectorAll('th')).every(th => th.scrollWidth <= th.clientWidth+1),
        familyColumnsFit: width !== 1440 || (wrap.scrollWidth <= wrap.clientWidth+1 &&
          Array.from(root.querySelectorAll('.heatmap-family-heading')).every(th => {
            const rect = th.getBoundingClientRect(), frame = wrap.getBoundingClientRect();
            const label = root.querySelector('.heatmap-model-heading').getBoundingClientRect();
            return rect.left >= label.right && rect.right <= frame.right;
          })),
        scrolls: (width === 1440 || wrap.scrollWidth > wrap.clientWidth) && wrap.scrollHeight > wrap.clientHeight,
      };
    }''', width)
    assert all(result.values()), (width, theme, track, result)
    page.locator('.heatmap-scroll').evaluate('(el) => {el.scrollLeft=220; el.scrollTop=180;}')
    assert page.evaluate('''() => {
      const wrap = document.querySelector('.heatmap-scroll').getBoundingClientRect();
      const row = document.querySelector('tbody .heatmap-model-heading').getBoundingClientRect();
      const column = document.querySelector('.heatmap-family-heading').getBoundingClientRect();
      return Math.abs(row.left-wrap.left) <= 5 && Math.abs(column.top-wrap.top) <= 5;
    }''')
    page.locator('.heatmap-scroll').evaluate('(el) => {el.scrollLeft=0; el.scrollTop=0;}')
    cell = page.locator('.heatmap-cell[data-score]').first
    cell.focus()
    tooltip = page.locator('#family-heatmap-tooltip')
    assert tooltip.is_visible()
    assert tooltip.inner_text() == cell.get_attribute('aria-label')
    cell.press('Escape')
    assert tooltip.is_hidden()
    cell.hover()
    assert tooltip.is_visible()
    cell.click()
    assert tooltip.is_visible()
    assert tooltip.evaluate('el => {const r=el.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight;}')
    page.locator('.heatmap-show-all input').uncheck()


def test_scroll_caption_follows_breakpoint_without_rerendering(heatmap_page):
    page = heatmap_page
    for width, sideways in ((1440, False), (390, True), (1440, False)):
        page.set_viewport_size({'width': width, 'height': 1000})
        text = page.locator('.heatmap-guidance').inner_text()
        assert 'vertically' in text
        assert ('sideways' in text) is sideways


def _synthetic_payload():
    payload = json.loads((ROOT / 'site/data/leaderboard.json').read_text())
    template = payload['models'][0]
    payload['task_families'] = payload['task_families'][:3]
    f0, f1, f2 = [f['id'] for f in payload['task_families']]
    models = []
    for i in range(15):
        row = copy.deepcopy(template)
        row.update(identifier=f'model-{i:02}', model_family=f'model-{i:02}', row_id=str(i),
                   reasoning_level='low', is_control=False, is_human_baseline=False)
        coverage, score = (2, 0) if i == 0 else (1, 4 if i < 4 else 2)
        if i in (1, 2):
            row['reasoning_level'] = 'high' if i == 1 else 'xhigh'
        for track in ('blind', 'perception'):
            row['tracks'][track] = dict(has_data=True, n_families_scored=coverage, overall_mean=score,
                                        families={f0: dict(mean_score=score), f1: dict(mean_score=0) if i == 0 else None, f2: None})
        models.append(row)
    payload['models'] = list(reversed(models))  # Input ordering must not pick the default rows.
    return payload


@pytest.mark.parametrize('width', [1440, 390])
def test_zero_missing_coverage_ties_and_empty_track(heatmap_page, width):
    page = heatmap_page
    page.set_viewport_size({'width': width, 'height': 1000})
    payload = _synthetic_payload()
    pattern = '**/data/leaderboard.json'
    page.route(pattern, lambda route: route.fulfill(content_type='application/json', body=json.dumps(payload)))
    try:
        page.reload(wait_until='networkidle')
        for track in ('blind', 'perception'):
            page.locator(f'#track-toggle button[data-track="{track}"]').click()
            assert [r['id'] for r in _rows(page)][:4] == ['0', '1', '2', '3']
            assert len(_rows(page)) == 12
            assert page.locator('.heatmap-family-heading').count() == 2
            zero = page.locator('.heatmap-cell[data-score="0"]').first
            assert zero.inner_text() == '0.00'
            assert '0.00/4' in zero.get_attribute('aria-label')
            missing = page.locator('.heatmap-cell-missing').first
            assert missing.get_attribute('data-score') is None
            assert missing.inner_text() == '—'
            assert 'No data' in missing.get_attribute('aria-label')
            assert missing.evaluate('el => getComputedStyle(el).backgroundImage') != 'none'
            page.locator('.heatmap-show-all input').check()
            assert len(_rows(page)) == 15
            page.locator('.heatmap-show-all input').uncheck()
        for row in payload['models']:
            for tr in row['tracks'].values():
                tr['families'] = {key: None for key in tr['families']}
        page.reload(wait_until='networkidle')
        assert page.locator('.heatmap-empty').inner_text() == 'No family scores on this track yet.'
        assert page.locator('.heatmap-family-heading').count() == 0
        assert '3 empty families hidden' in page.locator('.heatmap-summary').inner_text()
    finally:
        page.unroute(pattern)
        page.reload(wait_until='networkidle')
