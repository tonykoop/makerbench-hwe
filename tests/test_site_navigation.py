"""Shared navigation, deterministic generation and deletion/encoding controls."""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'site'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


nav = load('shared_site_navigation', SITE / 'navigation.py')
drift = load('navigation_drift', SITE / 'check_data_drift.py')
PRIMARY = ('Leaderboard', 'Matchups', 'Charts', 'Tasks', 'Arena', 'About')


def test_every_published_page_has_the_shared_prerendered_header():
    pages = list(SITE.rglob('*.html'))
    assert len(pages) > 100  # Includes model/task pages, not just the front door.
    for page in pages:
        source = page.read_text(encoding='utf-8')
        assert source.count('<!-- site-nav:header -->') == 1, page
        header = re.search(r'<!-- site-nav:header -->.*?<!-- /site-nav:header -->', source, re.S)[0]
        assert header == nav.render_header(page.relative_to(SITE)), page
        assert nav.decorate_html(source, page.relative_to(SITE)) == source, page
        assert source.count('id="theme-toggle"') == 1, page
        assert 'assets/navigation.js?v=1023' in source
        assert 'assets/navigation.css?v=1023' in source


def test_navigation_has_one_canonical_destination_per_primary_link():
    assert tuple(label for label, _ in nav.PRIMARY) == PRIMARY
    destinations = dict(nav.PRIMARY + nav.MORE)
    for name in ('Leaderboard', 'Matchups', 'Charts', 'Tasks', 'Arena', 'Landscape'):
        assert destinations[name] == name.lower() + '.html'
    assert ('Methodology', 'index.html#methodology') in nav.MORE
    assert 'Methodology' not in dict(nav.PRIMARY)


@pytest.mark.parametrize('page, expected', [
    *((href, label) for label, href in nav.PRIMARY + nav.MORE
      if not href.startswith('https:') and '#' not in href and not href.endswith('/')),
    ('index.html', 'About'),
    ('blog/index.html', 'Blog'),
    ('blog/failure-gallery.html', None),
    ('models/baseline-v0-default-community/index.html', None),
    ('tasks/beam_bending_dfm/index.html', None),
])
def test_navigation_marks_only_the_matching_page_current(page, expected):
    from html.parser import HTMLParser

    class CurrentLinks(HTMLParser):
        def __init__(self):
            super().__init__()
            self.labels = []
            self.current = False

        def handle_starttag(self, tag, attrs):
            if tag == 'a':
                self.current = dict(attrs).get('aria-current') == 'page'

        def handle_data(self, data):
            if self.current:
                self.labels.append(data)

        def handle_endtag(self, tag):
            if tag == 'a':
                self.current = False

    parser = CurrentLinks()
    parser.feed(nav.render_header(Path(page)))
    assert parser.labels == ([expected] if expected else [])


@pytest.mark.parametrize('deleted', ['matchups.html', 'landscape.html', 'tasks.html', 'arena.html', 'charts.html', 'leaderboard.html'])
def test_deleting_a_detail_template_is_drift_even_if_builder_also_omits_it(tmp_path, monkeypatch, deleted):
    site = tmp_path / 'site'
    site.mkdir()
    for name in ('index.html', *drift.REQUIRED_DETAIL_PAGES):
        (site / name).write_text('<html>same</html>', encoding='utf-8')

    def rebuild(repo_root, generated_site):
        shutil.copytree(repo_root / 'site', generated_site)

    monkeypatch.setattr(drift, '_run_build_data', rebuild)
    assert not drift.check_build_data_drift(tmp_path).has_drift
    (site / deleted).unlink()
    report = drift.check_build_data_drift(tmp_path)
    assert report.has_drift
    assert report.missing == (f'site/{deleted}',)


def test_shared_header_staleness_is_reported(tmp_path):
    committed, generated = tmp_path / 'committed', tmp_path / 'generated'
    for directory in (committed, generated):
        directory.mkdir()
        for name in drift.REQUIRED_DETAIL_PAGES:
            (directory / name).write_text('<html>same</html>', encoding='utf-8')
        (directory / 'blog').mkdir()
        (directory / 'blog/index.html').write_text('<html>current header</html>', encoding='utf-8')
    (committed / 'blog/index.html').write_text('<html>stale header</html>', encoding='utf-8')
    assert drift.compare_static_pages(committed, generated).changed == ('site/blog/index.html',)


def test_link_checker_uses_utf8_under_an_ascii_locale(tmp_path):
    (tmp_path / 'index.html').write_text('<h1 id="méthode">Méthode</h1><a href="#m%C3%A9thode">Méthode</a>', encoding='utf-8')
    child = """import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location('links', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert not module.check_links(pathlib.Path(sys.argv[2]))
"""
    env = dict(os.environ, LC_ALL='C', PYTHONUTF8='0', PYTHONCOERCECLOCALE='0')
    result = subprocess.run([sys.executable, '-X', 'utf8=0', '-c', child, str(SITE / 'check_links.py'), str(tmp_path)],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_reusing_a_build_directory_refreshes_authoritative_blog_content(tmp_path):
    source, output = tmp_path / 'source', tmp_path / 'output'
    (source / 'blog').mkdir(parents=True)
    page = source / 'blog/article.html'
    page.write_text('<html><head></head><body><main>First version</main></body></html>', encoding='utf-8')
    nav.write_static_headers(source, output)
    page.write_text('<html><head></head><body><main>Updated article</main></body></html>', encoding='utf-8')
    nav.write_static_headers(source, output)
    rendered = (output / 'blog/article.html').read_text(encoding='utf-8')
    assert 'Updated article' in rendered
    assert 'First version' not in rendered
    assert rendered.count('<!-- site-nav:header -->') == 1
    assert 'href="../leaderboard.html"' in rendered
