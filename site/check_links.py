#!/usr/bin/env python3
"""Check published HTML links and fragments locally, without network requests."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids = set()
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            self.ids.add(attrs['id'])
        if tag in {'a', 'link', 'script', 'img'}:
            href = attrs.get('href') if tag in {'a', 'link'} else attrs.get('src')
            if href:
                self.links.append(href)


def check_links(site):
    site = site.resolve()
    pages = {p.resolve(): Links(p.read_text(encoding='utf-8')) for p in site.rglob('*.html')}
    errors = []
    for source, parsed in pages.items():
        for href in parsed.links:
            url = urlsplit(href)
            if url.scheme or url.netloc:
                continue
            path = unquote(url.path)
            # Project Pages root-relative links use the deployment prefix.
            if path.startswith('/makerbench-hwe/'):
                path = path[len('/makerbench-hwe/'):]
                target = site / path
            elif path.startswith('/'):
                target = site / path.lstrip('/')
            else:
                target = source.parent / path if path else source
            target = target.resolve()
            if target.is_dir():
                target /= 'index.html'
            if not target.is_relative_to(site) or not target.is_file():
                errors.append(f'{source.relative_to(site)}: missing file {href}')
            elif url.fragment and target in pages and unquote(url.fragment) not in pages[target].ids:
                errors.append(f'{source.relative_to(site)}: missing fragment {href}')
    return sorted(set(errors))


if __name__ == '__main__':
    errors = check_links(Path(__file__).resolve().parent)
    print('\n'.join(errors) if errors else 'All internal links and fragments resolve.')
    raise SystemExit(bool(errors))
