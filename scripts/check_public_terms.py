#!/usr/bin/env python3
"""Check public text against hash-only private-term fingerprints.

The denylist (``scripts/private_term_hashes.txt``) holds SHA-256 hex digests
only, one per line; blank lines and ``#`` comments are allowed. A private term
is fingerprinted by lowercasing it, splitting it into alphanumeric tokens and
joining them with ``-``, e.g. a two-word term "Foo Bar" becomes ``foo-bar``
(``python scripts/check_public_terms.py --fingerprint`` reads a term from stdin
and prints its hash without echoing it).

Public text is normalized the same way (HTML entities and JSON ``\\uXXXX``
escapes are decoded first) and every run of 1..MAX_NGRAM consecutive tokens is
hashed in hyphen-joined form (``foo-bar``). CamelCase tokens are additionally
split, so ``FooBar``, ``foo_bar``,
``**Foo** bar`` and ``"foo-bar"`` all hit the ``foo-bar`` fingerprint, while
substrings such as ``foobarbaz`` do not. (Concatenating separate words is
deliberately not done: it turns ordinary prose like "select a" into hits.)

Scope: text files under docs/ and site/, every README* in the repo, and the
top-level public Markdown/CITATION files. Diagnostics name file and line only
and never echo matched text.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DENYLIST = ROOT / "scripts" / "private_term_hashes.txt"
MAX_NGRAM = 4
HASH_LINE = re.compile(r"[0-9a-f]{64}")
TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
JSON_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
HTML_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
PUBLIC_DIRS = ("docs", "site")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
TEXT_SUFFIXES = {".md", ".markdown", ".rst", ".txt", ".html", ".htm", ".json",
                 ".js", ".mjs", ".css", ".svg", ".xml", ".csv", ".py", ".yaml",
                 ".yml", ".toml", ".cff"}


def load_hashes(path: Path) -> frozenset[str]:
    """Load the denylist, rejecting anything that is not a hash or comment."""
    hashes = []
    for line in path.read_text(encoding="ascii").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if not HASH_LINE.fullmatch(value):
            raise ValueError("Denylist lines must be lowercase SHA-256 hashes or # comments")
        hashes.append(value)
    if not hashes:
        raise ValueError("Denylist contains no hashes")
    return frozenset(hashes)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def fingerprint(term: str) -> str:
    """Return the denylist entry for a plaintext term."""
    return _digest("-".join(t.lower() for t in TOKEN.findall(term)))


def _normalize(text: str) -> str:
    text = JSON_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)
    return html.unescape(text)


def _strip_tags(text: str) -> str:
    # Keep line numbers stable: a tag becomes a space plus the newlines it spanned.
    return HTML_TAG.sub(lambda m: " " + "\n" * m.group().count("\n"), text)


def matched_lines(text: str, hashes: frozenset[str]) -> list[int]:
    """Return 1-based line numbers where a denied fingerprint starts.

    Text is scanned as-is (so terms inside tag attributes are seen) and with
    HTML tags removed (so ``<b>foo</b> bar`` reads as ``foo bar``).
    """
    lines = set(_matched_lines(_normalize(text), hashes))
    if "<" in text:
        lines.update(_matched_lines(_normalize(_strip_tags(text)), hashes))
    return sorted(lines)


def _matched_lines(text: str, hashes: frozenset[str]) -> list[int]:
    matches = list(TOKEN.finditer(text))
    words = [m.group().lower() for m in matches]
    positions = set()

    def hit(start: int) -> None:
        positions.add(text.count("\n", 0, start) + 1)

    for index, match in enumerate(matches):
        window = words[index:index + MAX_NGRAM]
        candidates = {"-".join(window[:size]) for size in range(1, len(window) + 1)}
        if any(_digest(value) in hashes for value in candidates):
            hit(match.start())
            continue
        parts = [p.lower() for p in CAMEL.findall(match.group())]
        if len(parts) > 1 and any(
            _digest("-".join(parts[i:j])) in hashes
            for i in range(len(parts))
            for j in range(i + 1, min(i + MAX_NGRAM, len(parts)) + 1)
        ):
            hit(match.start())
    return sorted(positions)


def _walk(directory: Path):
    for path in sorted(directory.rglob("*")):
        if any(part in SKIP_DIRS for part in path.relative_to(directory).parts):
            continue
        if path.is_file():
            yield path


def public_paths(root: Path) -> list[Path]:
    """docs/ and site/ text, every README*, and top-level public Markdown."""
    paths = set()
    for name in PUBLIC_DIRS:
        directory = root / name
        if directory.is_dir():
            paths.update(p for p in _walk(directory) if p.suffix.lower() in TEXT_SUFFIXES)
    paths.update(p for p in _walk(root) if p.name.upper().startswith("README"))
    for pattern in ("*.md", "*.cff"):
        paths.update(p for p in root.glob(pattern) if p.is_file())
    return sorted(paths)


def check(root: Path, hashes: frozenset[str]) -> list[str]:
    problems = []
    for path in public_paths(root):
        text = path.read_bytes().decode("utf-8", errors="replace")
        for line in matched_lines(text, hashes):
            problems.append(f"{path.relative_to(root)}:{line}: forbidden term fingerprint")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check public text for private-term fingerprints.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--denylist", type=Path, default=DENYLIST)
    parser.add_argument("--fingerprint", action="store_true",
                        help="read a term from stdin and print its denylist hash")
    args = parser.parse_args(argv)
    if args.fingerprint:
        print(fingerprint(sys.stdin.read()))
        return 0
    try:
        problems = check(args.root, load_hashes(args.denylist))
    except (OSError, UnicodeError, ValueError):
        # An error may include private text or paths supplied by content. Do not echo it.
        print("Public term check failed: unreadable public text or invalid hash denylist")
        return 1
    if problems:
        print("\n".join(problems))
        return 1
    print("Public term fingerprint check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
