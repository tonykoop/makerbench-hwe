#!/usr/bin/env python3
"""Check public text against hash-only private-term fingerprints.

The denylist (``scripts/private_term_hashes.txt``) holds SHA-256 hex digests
only, one per line; blank lines and ``#`` comments are allowed. A private term
is fingerprinted by lowercasing it, splitting it into alphanumeric tokens and
joining them with ``-``, e.g. a two-word term "Foo Bar" becomes ``foo-bar``
(``python scripts/check_public_terms.py --fingerprint`` reads a term from stdin
and prints its hash without echoing it).

Public text is normalized before tokenizing:

* JSON string escapes are decoded (``\\uXXXX`` to the character; ``\\n``,
  ``\\t`` and other control escapes to a separator) and HTML entities are
  unescaped;
* the text is scanned three ways and the hits are unioned: as-is (so terms in
  tag attributes are seen), with HTML tags turned into separators, and with
  inline HTML tags and Markdown emphasis markers (``* _ ~ ` ``) removed so a
  word split by formatting (``**Fo**o``, ``Fo<b>o</b>``) is rejoined.

Each view is tokenized case-insensitively into two streams, whole alphanumeric
tokens and CamelCase-split parts, and every run of 1..MAX_NGRAM consecutive
stream items is hashed in hyphen-joined form. So ``Foo Bar``, ``foo_bar``,
``FooBar``, ``FooBar Baz``, ``"foo-bar"`` and ``<b>foo</b>&nbsp;bar`` hit the
``foo-bar`` / ``foo-bar-baz`` fingerprints, while substrings such as
``foobarbaz`` do not. (Separate words are never concatenated: that turns
ordinary prose like "select a" into hits.)

Scope: every non-binary file under docs/ and site/, every README* in the repo,
and the top-level public Markdown/CITATION files. private/, git submodules,
VCS/cache/virtualenv/node_modules directories are pruned before traversal and
never opened. Diagnostics name file and line only and never echo matched text.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DENYLIST = ROOT / "scripts" / "private_term_hashes.txt"
MAX_NGRAM = 4
HASH_LINE = re.compile(r"[0-9a-f]{64}")
TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
JSON_ESCAPE = re.compile(r"\\(u[0-9a-fA-F]{4}|[nrtbf\"\\/])")
HTML_TAG = re.compile(r"</?[A-Za-z][^<>]*>")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
INLINE_TAG = re.compile(
    r"</?(?:b|i|em|strong|span|a|code|mark|sup|sub|u|s|small)(?=[\s/>])[^<>]*>", re.IGNORECASE)
MD_LINK = re.compile(r"!?\[([^\[\]\n]*)\]\([^()\n]*\)")
EMPHASIS = re.compile(r"[*_~`]+")
PUBLIC_DIRS = ("docs", "site")
PRIVATE_PREFIXES = ("private",)
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox",
             ".mypy_cache", ".pytest_cache", ".ruff_cache"}
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp",
                   ".pdf", ".pyc", ".stl", ".step", ".stp", ".glb", ".gltf",
                   ".zip", ".gz", ".tar", ".woff", ".woff2", ".ttf", ".otf",
                   ".mp4", ".webm", ".mov", ".mp3", ".wav"}
_ESCAPES = {"n": " ", "r": " ", "t": " ", "b": " ", "f": " ", '"': '"', "\\": "\\", "/": "/"}


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


def _decode(text: str) -> str:
    def escape(match: re.Match) -> str:
        code = match.group(1)
        if code[0] == "u":
            char = chr(int(code[1:], 16))
            return char if char.isprintable() else " "
        return _ESCAPES[code]

    return html.unescape(JSON_ESCAPE.sub(escape, text))


def _replace_keeping_lines(pattern: re.Pattern, text: str, filler: str) -> str:
    return pattern.sub(lambda m: filler + "\n" * m.group().count("\n"), text)


def views(text: str) -> list[str]:
    """Normalized views of ``text``; all keep the original line numbering.

    Raw (decoded) text; tags/comments as separators; and a "joined" view where
    HTML comments, inline tags (b, span, a, ...) and Markdown emphasis are
    removed so formatting inside a word rejoins it, while block tags (p, br,
    div, li, td, headings, ...) still separate words. Markdown links and
    images are reduced to their text in the last two views.
    """
    base = _decode(text)
    links = MD_LINK.sub(lambda m: m.group(1), base)
    spaced = _replace_keeping_lines(HTML_TAG, _replace_keeping_lines(HTML_COMMENT, links, " "), " ")
    joined = _replace_keeping_lines(HTML_COMMENT, links, "")
    joined = _replace_keeping_lines(INLINE_TAG, joined, "")
    joined = _replace_keeping_lines(HTML_TAG, joined, " ")
    joined = _replace_keeping_lines(EMPHASIS, joined, "")
    result = [base]
    for view in (spaced, joined):
        if view not in result:
            result.append(view)
    return result


def _tokens(text: str) -> list[tuple[int, list[list[str]]]]:
    """Each token with its readings: whole, plus CamelCase parts if any."""
    tokens = []
    for match in TOKEN.finditer(text):
        token = match.group()
        readings = [[token.lower()]]
        parts = CAMEL.findall(token)
        if len(parts) > 1 and "".join(parts) == token:
            readings.append([part.lower() for part in parts])
        tokens.append((match.start(), readings))
    return tokens


def _grams_from(tokens, index: int) -> set[str]:
    """Every <=MAX_NGRAM-word window starting in token ``index``.

    Each token may be read whole or as its CamelCase parts, independently,
    so mixed forms such as ``FooBar BazQux`` yield ``foobar-baz-qux``.
    """
    grams: set[str] = set()

    def extend(words: list[str], nxt: int) -> None:
        grams.add("-".join(words))
        if len(words) >= MAX_NGRAM or nxt >= len(tokens):
            return
        for reading in tokens[nxt][1]:
            for size in range(1, len(reading) + 1):
                window = words + reading[:size]
                if len(window) > MAX_NGRAM:
                    break
                if size < len(reading):
                    grams.add("-".join(window))  # a window may end mid-token
                else:
                    extend(window, nxt + 1)

    for reading in tokens[index][1]:
        for offset in range(len(reading)):  # a window may start mid-token
            tail = reading[offset:]
            for size in range(1, min(len(tail), MAX_NGRAM) + 1):
                if size < len(tail):
                    grams.add("-".join(tail[:size]))
                else:
                    extend(tail, index + 1)
    return grams


def _view_lines(text: str, hashes: frozenset[str]) -> set[int]:
    # Collect each distinct n-gram once (public JSON repeats a lot), then hash.
    grams: dict[str, list[int]] = {}
    tokens = _tokens(text)
    for index, (start, _) in enumerate(tokens):
        for gram in _grams_from(tokens, index):
            grams.setdefault(gram, []).append(start)
    return {
        text.count("\n", 0, start) + 1
        for gram, starts in grams.items() if _digest(gram) in hashes
        for start in starts
    }


def matched_lines(text: str, hashes: frozenset[str]) -> list[int]:
    """Return 1-based line numbers where a denied fingerprint starts."""
    lines: set[int] = set()
    for view in views(text):
        lines |= _view_lines(view, hashes)
    return sorted(lines)


def _submodule_paths(root: Path) -> set[str]:
    gitmodules = root / ".gitmodules"
    if not gitmodules.is_file():
        return set()
    try:
        out = subprocess.run(
            ["git", "config", "-f", str(gitmodules), "--get-regexp", r"^submodule\..*\.path$"],
            capture_output=True, text=True, check=False, timeout=30,
        )
        if out.returncode in (0, 1):  # 1 = no path entries
            return {line.split(" ", 1)[1].strip().strip("/")
                    for line in out.stdout.splitlines() if " " in line}
    except (OSError, subprocess.SubprocessError):
        pass
    paths = set()  # fallback: minimal parse, unquoting like git does
    for line in gitmodules.read_text(encoding="utf-8", errors="replace").splitlines():
        key, _, value = line.partition("=")
        value = value.split(" #")[0].split(" ;")[0].strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        if key.strip().lower() == "path" and value.strip():
            paths.add(value.strip().strip("/"))
    return paths


def excluded_prefixes(root: Path) -> set[str]:
    """private/ plus every git submodule path, as root-relative POSIX paths."""
    return set(PRIVATE_PREFIXES) | _submodule_paths(root)


def _walk(root: Path, top: Path, excluded: set[str]):
    """Yield files under ``top``, pruning excluded trees before entering them."""
    for dirpath, dirnames, filenames in os.walk(top):
        rel = Path(dirpath).relative_to(root).as_posix()
        rel = "" if rel == "." else rel + "/"
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".venv")
            and (rel + d) not in excluded
        )
        for name in sorted(filenames):
            yield Path(dirpath) / name


def public_paths(root: Path) -> list[Path]:
    """docs/ and site/ files, every README*, and top-level public Markdown."""
    excluded = excluded_prefixes(root)
    paths = set()
    for name in PUBLIC_DIRS:
        directory = root / name
        if directory.is_dir() and not any(name == e or name.startswith(e + "/")
                                          for e in excluded):
            paths.update(p for p in _walk(root, directory, excluded)
                         if p.suffix.lower() not in BINARY_SUFFIXES)
    paths.update(p for p in _walk(root, root, excluded) if p.name.upper().startswith("README"))
    for pattern in ("*.md", "*.cff"):
        paths.update(p for p in root.glob(pattern) if p.is_file())
    return sorted(paths)


def check(root: Path, hashes: frozenset[str]) -> list[str]:
    problems = []
    for path in public_paths(root):
        data = path.read_bytes()
        if b"\0" in data:
            continue  # binary payload; not prose
        for line in matched_lines(data.decode("utf-8", errors="replace"), hashes):
            problems.append(f"{path.relative_to(root).as_posix()}:{line}: forbidden term fingerprint")
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
