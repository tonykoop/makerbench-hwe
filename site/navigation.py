"""Single, stdlib-only source for every published page's navigation."""
from html import escape
from pathlib import Path
import re

PRIMARY = (
    ('Leaderboard', 'leaderboard.html'),
    ('Matchups', 'matchups.html'),
    ('Charts', 'charts.html'),
    ('Tasks', 'tasks.html'),
    ('Arena', 'arena.html'),
    ('About', 'index.html#about'),
)
MORE = (
    ('Methodology', 'index.html#methodology'),
    ('Why MakerBench', 'index.html#why'),
    ('Tracks & leagues', 'index.html#tracks-leagues'),
    ('Domains', 'domains.html'),
    ('What MakerBench covers', 'index.html#covers'),
    ('Regressions', 'index.html#delta-dossier'),
    ('Landscape', 'landscape.html'),
    ('Ecosystem', 'index.html#ecosystem'),
    ('Opportunity Matrix', 'opportunity-matrix.html'),
    ('Explorer', 'explorer.html'),
    ('Inspect a Run', 'inspect.html'),
    ('Run library', 'run-library.html'),
    ('Findings', 'index.html#findings'),
    ('Blog', 'blog/'),
    ('Roadmap', 'index.html#roadmap'),
    ('Get started', 'index.html#get-started'),
    ('GitHub', 'https://github.com/tonykoop/makerbench-hwe'),
)
HEADER_RE = re.compile(r'<!-- site-nav:header -->.*?<!-- /site-nav:header -->', re.S)
OLD_HEADER_RE = re.compile(r'<header\b[^>]*class="nav"[^>]*>.*?</header>', re.S)


def render_header(page: Path) -> str:
    prefix = '../' * len(page.parent.parts)

    def destination(href):
        path = href.split('#', 1)[0]
        return path + 'index.html' if path.endswith('/') else path

    # Several homepage fragments share one document; mark only its first link.
    current = next((href for _, href in PRIMARY + MORE
                    if destination(href) == page.as_posix()), None)

    def links(entries):
        return '\n'.join(
            f'        <a href="{escape(href if href.startswith("https:") else prefix + href, quote=True)}"'
            + (' aria-current="page"' if href == current else '')
            + (' id="repo-link" rel="noopener"' if label == 'GitHub' else '')
            + f'>{escape(label)}</a>' for label, href in entries
        )

    return f'''<!-- site-nav:header -->
<header class="site-nav">
  <div class="site-nav-inner">
    <a class="site-nav-brand" href="{prefix}index.html" aria-label="MakerBench home"><span aria-hidden="true">◆</span> MakerBench</a>
    <button class="site-menu-button" id="site-menu-button" type="button" aria-expanded="false" aria-controls="site-navigation" hidden>Menu</button>
    <nav class="site-navigation" id="site-navigation" aria-label="Primary">
{links(PRIMARY)}
      <details class="site-nav-more">
        <summary>More</summary>
        <div class="site-nav-secondary">
{links(MORE)}
        </div>
      </details>
    </nav>
    <button class="site-theme-button" id="theme-toggle" type="button" aria-label="Toggle light / dark" title="Toggle light / dark" hidden>◐</button>
  </div>
  <p class="site-data-status" data-site-data-status role="status" hidden></p>
</header>
<script src="{prefix}assets/navigation.js?v=1023"></script>
<!-- /site-nav:header -->'''


def decorate_html(source: str, page: Path) -> str:
    """Prerender a header, with depth-correct asset and destination URLs."""
    # Theme interaction is owned by navigation.js on every page.
    source = re.sub(r"<script>(.*?)</script>", lambda m: "" if m[1].strip().startswith((
        'document.getElementById("theme-toggle")', "document.getElementById('theme-toggle')",
    )) else m[0], source, flags=re.S)
    source = re.sub(r'(?m)^[ \t]+$', '', source)
    header = render_header(page)
    if HEADER_RE.search(source):
        source = HEADER_RE.sub(lambda _: header, source)
    elif OLD_HEADER_RE.search(source):
        source = OLD_HEADER_RE.sub(lambda _: header, source, count=1)
    else:
        source = re.sub(r'(<body\b[^>]*>)', lambda m: m[1] + '\n' + header, source, count=1)
    prefix = '../' * len(page.parent.parts)
    routes = {f"#{name}": f"{prefix}{name}.html" for name in (
        "matchups", "landscape", "tasks", "arena", "charts", "leaderboard")}
    def footer_links(match):
        footer = match[0]
        for old, new in routes.items():
            footer = re.sub(r'href="(?:index\.html)?' + re.escape(old) + r'"',
                            lambda _: f'href="{new}"', footer)
        return footer
    source = re.sub(r"<footer\b.*?</footer>", footer_links, source, flags=re.S)
    assets = (f'<!-- site-nav:assets -->\n'
              f'<link rel="stylesheet" href="{prefix}assets/navigation.css?v=1023" />\n'
              '<!-- /site-nav:assets -->')
    pattern = re.compile(r'<!-- site-nav:assets -->.*?<!-- /site-nav:assets -->', re.S)
    if pattern.search(source):
        return pattern.sub(lambda _: assets, source)
    return source.replace('</head>', assets + '\n</head>', 1)


def write_static_headers(source_site: Path, output_site: Path, *, skip: set[str] | None = None) -> None:
    """Keep hand-authored root/blog pages current in normal and drift builds."""
    sources = sorted(source_site.glob('*.html')) + sorted((source_site / 'blog').glob('*.html'))
    for source in sources:
        relative = source.relative_to(source_site)
        target = output_site / relative
        if relative.as_posix() in (skip or set()):
            continue
        content = source.read_text(encoding='utf-8')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(decorate_html(content, relative), encoding='utf-8')
