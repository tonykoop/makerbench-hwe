"""Public-only runtime fetches, licence boundary and render-first comparisons."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('space_explore', ROOT / 'spaces/makerbench_hwe/tabs/explore.py')
explore = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(explore)


@pytest.fixture
def snapshot():
    return json.loads((ROOT / explore.SNAPSHOT).read_text(encoding='utf-8'))


def test_public_snapshot_projects_regraded_trials_without_excluded_check_text(snapshot):
    cases = explore.project_snapshot(snapshot)
    assert len(cases) == 4
    for case, original in zip(cases, snapshot['cases']):
        assert len(case['rows']) == len(original['rows'])
        for row, source in zip(case['rows'], original['rows']):
            for trial, recorded in zip(row['trials'], source.get('trials') or [source]):
                assert trial['checks'] == {key: value for key, value in recorded.get('gates', {}).items()
                                           if key in dict(explore.CHECKS)}
        html = explore.gallery_html(cases, case['id'])
        assert html.index('<img') < html.index('<summary>Objective checks')
        assert '<article' in html and 'Read the public study' in html
    text = json.dumps(cases).lower()
    for excluded in ('min_wall', 'borderline', 'estimator', 'objective_pass_rate', 'failed_checks'):
        assert excluded not in text
    # The repeat runs were regraded; preserve each remaining check, not old aggregates.
    assert len(cases[0]['rows'][0]['trials']) == 3


def test_render_paths_are_the_existing_licensed_subset(snapshot):
    spec = importlib.util.spec_from_file_location('studio_builder', ROOT / 'scripts/build_studio_demo_space.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    for case in explore.project_snapshot(snapshot):
        for row in case['rows']:
            for trial in row['trials']:
                if trial['image']:
                    path = trial['image'].removeprefix(explore.RAW)
                    assert builder.PUBLIC_PNG.fullmatch(path)
                    assert Path(path).suffix == '.png'
                    assert (ROOT / path).is_file()
    assert (ROOT / explore.DJEMBE_IMAGE).is_file()
    study = (ROOT / 'docs/showcase/djembe/CASE_STUDY.md').read_text()
    assert 'Only our own renders are published' in study


@pytest.mark.parametrize('path', ['../private/oracles/a.png', 'private/submissions/a.png',
                                  'https://example.com/render.png', 'docs/showcase/strings/reference.png',
                                  'docs/showcase/kora/assets/reference-photo-900px.jpg',
                                  'docs/showcase/strings/gallery/img/../../secret.png'])
def test_unapproved_and_protected_asset_paths_are_unreachable(snapshot, path):
    image = snapshot['cases'][0]['rows'][0]['trials'][0]['image']
    snapshot['assets'][image.rsplit('/', 1)[-1]] = path
    with pytest.raises(ValueError):
        explore.project_snapshot(snapshot)


@pytest.mark.parametrize('path', ['private/oracles/data.json', 'private/submissions/data.json',
                                  '/etc/passwd', 'https://example.com', '../tasks/official.json'])
def test_only_fixed_public_documents_can_be_requested(monkeypatch, path):
    monkeypatch.setattr(explore, 'build_opener', lambda *a: pytest.fail('network reached'))
    with pytest.raises(ValueError):
        explore.fetch_json(path)


def test_fetch_bounds_strict_utf8_timeout_and_no_redirects(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            assert limit == 2_000_001
            return b'{}'

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == explore.RAW + explore.SNAPSHOT
            assert timeout == 8
            return Response()

    def opener(handler):
        assert handler.redirect_request(None, None, 302, '', {}, 'http://127.0.0.1/') is None
        return Opener()

    monkeypatch.setattr(explore, 'build_opener', opener)
    assert explore.fetch_json(explore.SNAPSHOT) == {}
    monkeypatch.setattr(Response, 'read', lambda *a: b'x' * 2_000_001)
    with pytest.raises(ValueError):
        explore.fetch_json(explore.SNAPSHOT)
    monkeypatch.setattr(Response, 'read', lambda *a: b'\xff')
    with pytest.raises(ValueError):
        explore.fetch_json(explore.SNAPSHOT)


def test_untrusted_fields_are_not_rendered_and_html_is_escaped(snapshot):
    snapshot['cases'][0]['rows'][0]['label'] = '<script>alert(1)</script>'
    snapshot['cases'][0]['note'] = 'estimator borderline min_wall PRIVATE_SENTINEL'
    snapshot['cases'][0]['rows'][0]['source_code'] = 'PRIVATE_SENTINEL'
    cases = explore.project_snapshot(snapshot)
    html = explore.gallery_html(cases, cases[0]['id'], 0, 1)
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert 'PRIVATE_SENTINEL' not in json.dumps(cases)
    for excluded in ('estimator', 'borderline', 'min_wall'):
        assert excluded not in html


def test_djembe_uses_consistent_recorded_checks_and_no_total_score():
    source = json.loads((ROOT / explore.DJEMBE).read_text())
    case = explore.project_djembe(source)
    html = explore.gallery_html([case], 'djembe', 0, 0)
    assert html.count('<article') == 1
    assert 'Compile / render: Pass' in html
    assert all(excluded not in html for excluded in ('min_wall', 'borderline', 'estimator', '83%'))
    changed = copy.deepcopy(source)
    changed['rows'][0]['objective_pass_rate'] = 0.5
    assert not explore.project_djembe(changed)['rows'][0]['trials'][0]['checks']


def test_gradio_callbacks_load_compare_and_recover_offline(monkeypatch, snapshot):
    gr = pytest.importorskip('gradio')
    with gr.Blocks() as demo:
        explore.render()
    load = next(fn.fn for fn in demo.fns.values() if fn.fn.__name__ == 'load_cases')
    djembe = json.loads((ROOT / explore.DJEMBE).read_text())
    monkeypatch.setattr(explore, 'fetch_json', lambda path: snapshot if path == explore.SNAPSHOT else djembe)
    cases, studies, a, b, html = load()
    assert len(cases) == 5 and len(studies['choices']) == 5
    assert a['value'] == 0 and b['value'] == 1
    assert html.count('<article') == 2
    choose = next(fn.fn for fn in demo.fns.values() if fn.fn.__name__ == 'choose')
    a, b, html = choose(cases, 'strings-gallery')
    assert len(a['choices']) == 18
    assert explore.gallery_html(cases, 'strings-gallery', 2, 4).count('<article') == 2
    monkeypatch.setattr(explore, 'fetch_json', lambda path: (_ for _ in ()).throw(OSError('offline')))
    assert load()[-1] == explore.OFFLINE
    assert load()[0] == []
    assert explore.gallery_html([], None) == explore.EMPTY


def test_browser_render_first_selection_and_offline_retry(monkeypatch, snapshot, tmp_path):
    import os
    import socket

    gr = pytest.importorskip('gradio')
    playwright = pytest.importorskip('playwright.sync_api')
    djembe = json.loads((ROOT / explore.DJEMBE).read_text())
    offline = False

    def fetch(path):
        if offline:
            raise OSError('offline')
        return snapshot if path == explore.SNAPSHOT else djembe

    monkeypatch.setattr(explore, 'fetch_json', fetch)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with gr.Blocks(analytics_enabled=False) as demo:
        explore.render()
    demo.launch(server_name='127.0.0.1', server_port=port, prevent_thread_lock=True, quiet=True)
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(args=os.environ.get('ARENA_STUDIO_BROWSER_ARGS', '').split())
            for width in (1440, 390):
                page = browser.new_page(viewport={'width': width, 'height': 1000})
                # Images failing to load must not hide the checks or break navigation.
                page.route('**/raw.githubusercontent.com/**', lambda route: route.abort())
                page.goto(f'http://127.0.0.1:{port}', wait_until='domcontentloaded')
                page.get_by_role('button', name='Load showcase', exact=True).click()
                page.get_by_role('heading', name='Comparing AI models', exact=True).wait_for()
                assert page.locator('article').count() == 2
                page.locator('article summary').first.click()
                assert page.get_by_text('Compile / render: Pass', exact=True).first.is_visible()
                for label, value in [('Study', 'String-instrument gallery'), ('Right design', 'Design 5')]:
                    page.get_by_role('listbox', name=label, exact=True).click()
                    page.get_by_role('option', name=value, exact=True).click()
                page.locator('article h3').filter(has_text='Design 5').wait_for()
                assert page.locator('article').count() == 2
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                text = page.locator('body').inner_text().lower()
                assert all(word not in text for word in ('min_wall', 'borderline', 'estimator'))
                page.screenshot(path=str(tmp_path / f'explore-{width}.png'), full_page=True)
                page.close()
            offline = True
            page = browser.new_page()
            page.goto(f'http://127.0.0.1:{port}', wait_until='domcontentloaded')
            page.get_by_role('button', name='Load showcase', exact=True).click()
            page.get_by_role('status').filter(has_text='Showcase unavailable').wait_for()
            offline = False
            page.get_by_role('button', name='Load showcase', exact=True).click()
            page.get_by_role('heading', name='Comparing AI models', exact=True).wait_for()
            browser.close()
    finally:
        demo.close()
