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
  word split by formatting (``**Fo**o``, ``Fo<b>o</b>``) is rejoined. HTML
  comments are removed there too, even multi-line ones inside a word; an
  offset map keeps every token on its original source line.

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
import bisect
import hashlib
import html
import html.parser
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DENYLIST = ROOT / "scripts" / "private_term_hashes.txt"
MAX_NGRAM = 4
HASH_LINE = re.compile(r"[0-9a-f]{64}")
TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
JSON_ESCAPE = re.compile(r"\\(u[0-9a-fA-F]{4}|[nrtbf\"\\/])")
# Tag attributes may hold quoted ">" (title="a>b"); quoted values are matched
# whole (single-line, so a stray apostrophe in prose cannot swallow text), with
# the plain unquoted form as the fallback when a quote is left unbalanced.
_ATTRS = r"""(?:[^<>"']|"[^"\n]*"|'[^'\n]*')*"""
HTML_TAG = re.compile(rf"</?[A-Za-z]{_ATTRS}>|</?[A-Za-z][^<>]*>")
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
INLINE_TAG = re.compile(
    rf"</?(?:b|i|em|strong|span|a|code|mark|sup|sub|u|s|small)(?=[\s/>])(?:{_ATTRS}>|[^<>]*>)",
    re.IGNORECASE)
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


class _OffsetMap:
    """Text plus a map from its offsets back to 1-based ORIGINAL line numbers.

    ``sub`` deletes or replaces matches outright (newlines inside them are
    dropped, so a word split across lines by a comment or tag rejoins), while
    every surviving character keeps the line it came from: each step records
    piecewise (new offset -> old offset) segments, composed back to the source.
    """

    def __init__(self, text: str):
        self.text = text
        self._source_newlines = [m.start() for m in re.finditer("\n", text)]
        self._steps: list[tuple[list[int], list[int]]] = []

    def sub(self, pattern: re.Pattern, repl: str) -> None:
        parts: list[str] = []
        new_starts: list[int] = []
        old_starts: list[int] = []
        pos = out = 0
        for match in pattern.finditer(self.text):
            new_starts.append(out)
            old_starts.append(pos)
            parts.append(self.text[pos:match.start()])
            out += match.start() - pos
            new_starts.append(out)
            old_starts.append(match.start())  # replacement chars map to the match start
            parts.append(repl)
            out += len(repl)
            pos = match.end()
        if not parts:
            return
        new_starts.append(out)
        old_starts.append(pos)
        parts.append(self.text[pos:])
        self.text = "".join(parts)
        self._steps.append((new_starts, old_starts))

    def line_of(self, offset: int) -> int:
        for new_starts, old_starts in reversed(self._steps):
            i = bisect.bisect_right(new_starts, offset) - 1
            offset = old_starts[i] + (offset - new_starts[i])
        return bisect.bisect_left(self._source_newlines, offset) + 1


INLINE_TAGS = frozenset({"b", "i", "em", "strong", "span", "a", "code", "mark", "sup", "sub", "u", "s",
                         "small"})


class _JoinedParser(html.parser.HTMLParser):
    """Build the "joined" view with a real HTML tokenizer.

    Text outside markup is kept (Markdown emphasis markers removed); comments
    and inline tags vanish, so formatting inside a word rejoins it; block tags,
    declarations and processing instructions become a space. CDATA payloads
    and script/style bodies are treated as ordinary text and markup. Quoted attribute
    values (multi-line, containing ``>``, unquoted values with apostrophes) are
    tokenized by the parser, not by regexes. Each emitted segment records the
    source line it starts on, so tokens keep their original line numbers.
    """

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.starts: list[int] = []
        self.lines: list[int] = []
        self._out = 0

    def _emit(self, text: str) -> None:
        if text:
            self.starts.append(self._out)
            self.lines.append(self.getpos()[0])
            self.parts.append(text)
            self._out += len(text)

    def handle_data(self, data):
        self._emit(EMPHASIS.sub("", data))

    def _tag(self, tag: str) -> None:
        self._emit("" if tag in INLINE_TAGS else " ")

    def handle_starttag(self, tag, attrs):
        self._tag(tag)

    def handle_startendtag(self, tag, attrs):
        self._tag(tag)

    def handle_endtag(self, tag):
        self._tag(tag)

    def handle_entityref(self, name):
        self._emit(f"&{name};")

    def handle_charref(self, name):
        self._emit(f"&#{name};")

    def set_cdata_mode(self, *args, **kwargs):
        # No raw-text mode: <script>/<style>/<textarea>/<title> bodies are
        # tokenized like any other markup, so formatting inside them still
        # rejoins words, and a literal "<script>" (e.g. in a Markdown code
        # span) cannot swallow the rest of the file waiting for its end tag.
        pass

    def _cdata(self, payload: str) -> None:
        # CDATA payloads are text: normalize them like the rest of the view
        # (inline tags and emphasis dropped, other tags a space; newlines kept,
        # so the segment's line mapping stays exact).
        payload = _replace_keeping_lines(INLINE_TAG, payload, "")
        payload = _replace_keeping_lines(HTML_TAG, payload, " ")
        self._emit(" " + EMPHASIS.sub("", payload) + " ")

    def handle_comment(self, data):
        # Some html.parser versions report CDATA outside foreign content as a
        # bogus comment "[CDATA[...]]".
        if data.startswith("[CDATA[") and data.endswith("]]"):
            self._cdata(data[7:-2])

    def unknown_decl(self, data):
        if data.startswith("CDATA["):
            self._cdata(data[6:])
        else:
            self._emit(" ")

    def handle_decl(self, decl):
        self._emit(" ")

    handle_pi = handle_decl

    def line_of(self, offset: int) -> int:
        i = bisect.bisect_right(self.starts, offset) - 1
        if i < 0:
            return 1
        return self.lines[i] + self.parts[i].count("\n", 0, offset - self.starts[i])


def _joined_view(links: str) -> tuple[str, Callable[[int], int]]:
    """The joined view via :class:`_JoinedParser`; the regex path only if the
    parser itself raises."""
    try:
        parser = _JoinedParser()
        parser.feed(links)
        parser.close()
        return "".join(parser.parts), parser.line_of
    except Exception:  # noqa: BLE001 - malformed input: fall back to regex stripping
        joined = _OffsetMap(links)
        joined.sub(HTML_COMMENT, "")
        joined.sub(INLINE_TAG, "")
        joined.sub(HTML_TAG, " ")
        joined.sub(EMPHASIS, "")
        return joined.text, joined.line_of


def views(text: str) -> list[str]:
    """Normalized views of ``text`` (see :func:`_views_with_lines`)."""
    return [view for view, _ in _views_with_lines(text)]


def _views_with_lines(text: str) -> list[tuple[str, Callable[[int], int]]]:
    """Normalized views of ``text``, each with an offset -> original-line map.

    Raw (decoded) text; tags/comments as separators; and a "joined" view where
    HTML comments, inline tags (b, span, a, ...) and Markdown emphasis are
    removed so formatting inside a word rejoins it (also across lines, e.g. a
    multi-line comment inside a word), while block tags (p, br, div, li, td,
    headings, ...) still separate words. Markdown links and images are reduced
    to their text in the last two views. Every token is reported on the line
    where its first character sits in the source.
    """
    base = _decode(text)
    links = MD_LINK.sub(lambda m: m.group(1), base)
    spaced = _replace_keeping_lines(HTML_TAG, _replace_keeping_lines(HTML_COMMENT, links, " "), " ")
    joined_text, joined_line_of = _joined_view(links)

    def counted(view: str) -> Callable[[int], int]:
        return lambda start: view.count("\n", 0, start) + 1

    result = [(base, counted(base))]
    for view, line_of in ((spaced, counted(spaced)), (joined_text, joined_line_of)):
        if all(view != seen for seen, _ in result):
            result.append((view, line_of))
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
    so mixed forms such as ``FooBar BazQux`` yield ``foobar-baz-qux``. A window
    that starts (ends) inside a CamelCase token may also read the rest (the
    start) of it as one word: ``AcmeFooBar Baz`` yields ``foobar-baz`` and
    ``Foo BarBazQux`` yields ``foo-barbaz``.
    """
    grams: set[str] = set()

    def extend(words: list[str], nxt: int) -> None:
        grams.add("-".join(words))
        if len(words) >= MAX_NGRAM or nxt >= len(tokens):
            return
        for reading in tokens[nxt][1]:
            for size in range(1, len(reading) + 1):
                if 1 < size < len(reading):
                    # A window may end mid-token with that CamelCase prefix read
                    # as ONE word; the word limit applies after joining.
                    grams.add("-".join(words + ["".join(reading[:size])]))
                window = words + reading[:size]
                if len(window) > MAX_NGRAM:
                    continue
                if size < len(reading):
                    grams.add("-".join(window))  # a window may end mid-token
                else:
                    extend(window, nxt + 1)

    for reading in tokens[index][1]:
        for offset in range(len(reading)):  # a window may start mid-token
            tail = reading[offset:]
            if offset and len(tail) > 1:  # ... with that CamelCase suffix read as one word
                extend(["".join(tail)], index + 1)
            for size in range(1, min(len(tail), MAX_NGRAM) + 1):
                if size < len(tail):
                    grams.add("-".join(tail[:size]))
                else:
                    extend(tail, index + 1)
    return grams


def _view_lines(text: str, hashes: frozenset[str], line_of: Callable[[int], int]) -> set[int]:
    # Collect each distinct n-gram once (public JSON repeats a lot), then hash.
    grams: dict[str, list[int]] = {}
    tokens = _tokens(text)
    for index, (start, _) in enumerate(tokens):
        for gram in _grams_from(tokens, index):
            grams.setdefault(gram, []).append(start)
    return {
        line_of(start)
        for gram, starts in grams.items() if _digest(gram) in hashes
        for start in starts
    }


def matched_lines(text: str, hashes: frozenset[str]) -> list[int]:
    """Return 1-based line numbers where a denied fingerprint starts."""
    lines: set[int] = set()
    for view, line_of in _views_with_lines(text):
        lines |= _view_lines(view, hashes, line_of)
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
