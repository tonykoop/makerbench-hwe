#!/usr/bin/env python3
"""Check numeric claims in showcase docs against the files they cite.

The showcase case studies (``docs/showcase/*/CASE_STUDY.md``) and other
annotated showcase docs quote numbers from results files: rank-agreement rho,
pass rates, body counts, bounding-box dimensions, costs. This script binds each
quoted number to its source and fails when the two disagree.

Citation convention
-------------------
Citations are HTML comments, so they never render in the published text. Put
them in the same ``## `` section as the number they back (by convention, right
after the paragraph, fenced block or blockquote that quotes it)::

    <!-- claim: 0.07 source: site/data/arena.json#/headline/value -->
    <!-- claim: 1.000 at: "OpenSCAD scored 1.000" source: results/x.json#/rows/0/rate -->
    <!-- claim: 17 source: gallery.json#/designs/*/status|count=scored -->
    <!-- nocheck: "c. 2600 BC", "Fable 5" reason: museum date and model name -->

Each citation binds to **one occurrence** of the number:

* ``claim: VALUE`` is the number exactly as displayed (``0.889``, ``669``,
  ``52%``, ``$8.56``, ``1,300``, ``−0.40``, ``+0.55``). Matching is on the
  displayed string, so ``0.0700`` does not satisfy a ``0.07`` citation; ASCII
  ``-`` and Unicode ``−`` are interchangeable.
* ``at: "CONTEXT"`` (optional) is a snippet of the visible text that occurs
  exactly once in the section and contains exactly one number equal to VALUE;
  that number is the bound occurrence. Without ``at:``, VALUE itself must occur
  exactly once in the section. Missing → STALE, several → AMBIGUOUS.
* Several citations may bind the same occurrence (one number, two sources).

``source: PATH#SELECTOR`` resolves to one number:

* ``file.json#/json/pointer``: an RFC 6901 pointer (``""`` is the root, ``"/"``
  is the ``""`` key), extended with negative list indices and ``*`` (fan out
  over a list or object). A fan-out must end in an aggregate: ``|min``,
  ``|max``, ``|mean``, ``|sum``, ``|len`` or ``|count=VALUE``.
* ``file.md#re:REGEX`` (any text file): the first match's first group (or the
  whole match if the regex has no group).
* ``file.csv#row=N,col=NAME`` / ``.tsv``: a cell; ``row`` is the zero-based
  data row. ``where=COL:VALUE`` may replace ``row``.

Optional ``tol: X`` overrides the tolerance. By default the tolerance is half a
unit of the displayed last digit (``0.07`` accepts 0.065 to 0.075), so a claim
passes exactly when the source rounds to the displayed value. A ``%`` claim is
divided by 100 when the source value is a fraction (at most 1).

``nocheck: ITEM, ... reason: ...`` acknowledges numbers that have no results
file (dates, model names, facts only in private notes). Each ITEM is a quoted
context (every number inside it is covered) or a bare value (bound like a
claim without ``at:``). The reason is required.

Numbers: a sign (``-``, ``−``, ``+``) counts only when attached to the digits
and not preceded by a word character, so ``6–10`` (en dash) and ``6-10`` are
ranges with two positive endpoints, ``2026-09-30`` yields 2026, 09 and 30, and
``r6-r10`` or ``v1.2`` yield nothing.

Quoted bodies are fenced ``text`` blocks and blockquotes: ``>`` lines indented
by up to three spaces, plus their lazy continuation lines. Every
number occurrence in a quoted body that no citation binds is reported as
UNCITED: a warning, or an error under ``--strict``.

Exit status: 0 when every citation matches, 1 on any mismatch, missing source,
bad selector, stale or ambiguous citation (and on uncited numbers with
``--strict``).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GLOB = "docs/showcase/**/*.md"
# Case studies are always checked, cited or not, so a quoted number added to
# one without a citation fails under --strict.
CASE_STUDY_GLOB = "docs/showcase/*/CASE_STUDY.md"

CLAIM_RE = re.compile(
    r"<!--\s*claim:\s*(?P<value>\S+)"
    r"(?:\s+at:\s*\"(?P<at>[^\"]*)\")?"
    r"\s+source:\s*(?P<source>.+?)"
    r"(?:\s+tol:\s*(?P<tol>\S+))?\s*-->",
    re.DOTALL,
)
NOCHECK_RE = re.compile(
    r"<!--\s*nocheck:\s*(?P<items>.+?)\s+reason:\s*(?P<reason>.*?)\s*-->",
    re.DOTALL,
)
NOCHECK_ITEM_RE = re.compile(r"\"(?P<ctx>[^\"]*)\"|(?P<value>[^,\s]+)")
_SIGNS = r"[-+−]"
_NOT_BEFORE = r"[\w#.$/+\-−]"
# A displayed number: optional attached sign, optional $, digits with optional
# thousands commas and decimals, optional %. It may start:
#   * with a sign, when the sign is not preceded by a word char/#/./$/sign;
#   * without a sign, when not preceded by a word char, '#', '.', '$', '/' or a
#     sign (hashtags, issue refs, versions, ids);
#   * right after "<digit>-", the second endpoint of an ASCII range or date.
NUMBER_RE = re.compile(
    rf"(?:(?<!{_NOT_BEFORE}){_SIGNS}(?=\$?\d)|(?<!{_NOT_BEFORE})|(?<=\d-))"
    r"\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?"
    r"(?![\w])"
)
FENCE_RE = re.compile(r"^```text\n(.*?)^```", re.DOTALL | re.MULTILINE)
# CommonMark blockquote marker: up to three spaces of indentation, then '>'.
QUOTE_RE = re.compile(r"^ {0,3}>(?P<rest>.*)$")
# Lines that start a new block, so they cannot be lazy paragraph continuations
# of a blockquote: ATX headings, code fences, list items, thematic breaks.
NEW_BLOCK_RE = re.compile(
    r"^ {0,3}(?:#{1,6}(?:\s|$)|```|~~~|[-*+]\s|\d{1,9}[.)]\s"
    # Thematic break: 3+ of the SAME marker, optional spaces/tabs between.
    r"|(?:-[ \t]*){3,}$|(?:\*[ \t]*){3,}$|(?:_[ \t]*){3,}$)"
)
AGGREGATES = ("min", "max", "mean", "sum", "len")


class SourceError(Exception):
    """A citation's source file or selector could not produce a number."""


@dataclass
class Finding:
    level: str  # "ok", "error", "warning", "info"
    path: str
    line: int
    message: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, level: str, path: str, line: int, message: str) -> None:
        self.findings.append(Finding(level, path, line, message))

    def count(self, level: str) -> int:
        return sum(1 for f in self.findings if f.level == level)


# ---------------------------------------------------------------- numbers


def parse_displayed(token: str) -> tuple[Decimal, int, bool]:
    """Return (value, decimal places, is_percent) for a displayed number."""
    text = token.strip().replace("−", "-")
    is_percent = text.endswith("%")
    text = text.rstrip("%").replace("$", "").replace(",", "")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {token!r}") from exc
    places = -value.as_tuple().exponent if value.as_tuple().exponent < 0 else 0
    return value, int(places), is_percent


def to_number(raw: object) -> float:
    if isinstance(raw, bool):
        return float(raw)
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(parse_displayed(raw)[0])
        except ValueError as exc:
            raise SourceError(f"source value {raw!r} is not a number") from exc
    raise SourceError(f"source value {raw!r} is not a number")


def compare(claim: str, source_value: float, tol: float | None) -> tuple[bool, float, float]:
    """Return (matches, claim value on the source's scale, tolerance used)."""
    value, places, is_percent = parse_displayed(claim)
    claimed = float(value)
    tolerance = 0.5 * 10 ** (-places) if tol is None else tol
    if is_percent and abs(source_value) <= 1.0:
        claimed /= 100.0
        if tol is None:
            tolerance /= 100.0
    # A small epsilon so exact half-unit boundaries (0.065 for "0.07") pass.
    ok = abs(claimed - source_value) <= tolerance + 1e-9
    return ok, claimed, tolerance


# ---------------------------------------------------------------- sources


def _pointer_tokens(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise SourceError(f"JSON pointer must start with '/': {pointer!r}")
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")]


def _step(nodes: list[object], token: str, fanned: bool) -> tuple[list[object], bool]:
    out: list[object] = []
    for node in nodes:
        if token == "*":
            if isinstance(node, list):
                out.extend(node)
            elif isinstance(node, dict):
                out.extend(node.values())
            else:
                raise SourceError("'*' applied to a scalar")
            continue
        if isinstance(node, list):
            try:
                out.append(node[int(token)])
            except (ValueError, IndexError) as exc:
                raise SourceError(f"bad list index {token!r}") from exc
        elif isinstance(node, dict):
            if token not in node:
                raise SourceError(f"key {token!r} not found")
            out.append(node[token])
        else:
            raise SourceError(f"cannot index scalar with {token!r}")
    return out, fanned or token == "*"


def resolve_json(data: object, selector: str) -> float:
    pointer, _, aggregate = selector.partition("|")
    nodes: list[object] = [data]
    fanned = False
    for token in _pointer_tokens(pointer):
        nodes, fanned = _step(nodes, token, fanned)
    aggregate = aggregate.strip()
    if not aggregate:
        if fanned or len(nodes) != 1:
            raise SourceError("a '*' pointer needs an aggregate such as |min or |len")
        node = nodes[0]
        if isinstance(node, (list, dict)):
            raise SourceError("pointer resolves to a container; add |len or a key")
        return to_number(node)
    if aggregate.startswith("count="):
        want = aggregate[len("count="):]
        return float(sum(1 for n in nodes if str(n) == want))
    if aggregate == "len":
        if fanned:
            return float(len(nodes))
        if len(nodes) == 1 and isinstance(nodes[0], (list, dict)):
            return float(len(nodes[0]))
        raise SourceError("|len needs a list, an object or a '*' fan-out")
    if aggregate not in AGGREGATES:
        raise SourceError(f"unknown aggregate {aggregate!r}")
    values = [to_number(n) for n in nodes]
    if not values:
        raise SourceError("aggregate over an empty selection")
    if aggregate == "min":
        return min(values)
    if aggregate == "max":
        return max(values)
    if aggregate == "sum":
        return math.fsum(values)
    return math.fsum(values) / len(values)


def resolve_regex(text: str, pattern: str) -> float:
    try:
        match = re.search(pattern, text, re.MULTILINE)
    except re.error as exc:
        raise SourceError(f"bad regex: {exc}") from exc
    if not match:
        raise SourceError(f"regex {pattern!r} has no match")
    return to_number(match.group(1) if match.groups() else match.group(0))


def resolve_table(path: Path, selector: str) -> float:
    delimiter = "\t" if path.suffix == ".tsv" else ","
    params = dict(part.split("=", 1) for part in selector.split(",") if "=" in part)
    if "col" not in params:
        raise SourceError("table selector needs col=NAME")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter=delimiter))
    if "where" in params:
        key, _, want = params["where"].partition(":")
        matches = [r for r in rows if r.get(key) == want]
        if len(matches) != 1:
            raise SourceError(f"where={params['where']} matched {len(matches)} rows")
        row = matches[0]
    else:
        try:
            row = rows[int(params.get("row", "0"))]
        except (ValueError, IndexError) as exc:
            raise SourceError(f"row {params.get('row')!r} out of range") from exc
    if params["col"] not in row:
        raise SourceError(f"column {params['col']!r} not found")
    return to_number(row[params["col"]])


def resolve_source(source: str, root: Path) -> float:
    path_text, sep, selector = source.strip().partition("#")
    if not sep:
        raise SourceError("source needs PATH#SELECTOR")
    path = (root / path_text).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise SourceError(f"source {path_text!r} is outside the repository") from exc
    if not path.is_file():
        raise SourceError(f"source file {path_text!r} not found")
    if selector.startswith("re:"):
        return resolve_regex(path.read_text(encoding="utf-8"), selector[3:])
    if path.suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SourceError(f"invalid JSON in {path_text!r}: {exc}") from exc
        return resolve_json(data, selector)
    if path.suffix in (".csv", ".tsv"):
        return resolve_table(path, selector)
    raise SourceError(f"no selector type for {path.suffix!r}; use #re:REGEX")


# ---------------------------------------------------------------- documents


def split_sections(text: str) -> list[tuple[int, str]]:
    """Split on ``## `` headings. Return (start offset, section text) pairs."""
    starts = [0] + [m.start() for m in re.finditer(r"^## ", text, re.MULTILINE)]
    starts = sorted(set(starts))
    ends = starts[1:] + [len(text)]
    return [(s, text[s:e]) for s, e in zip(starts, ends)]


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def displayed_numbers(text: str) -> list[re.Match[str]]:
    return list(NUMBER_RE.finditer(text))


def canonical(token: str) -> str:
    """The displayed string with the minus sign unified; precision is kept."""
    return token.strip().replace("−", "-")


@dataclass
class Token:
    start: int
    end: int
    text: str
    in_quote: bool
    bound: bool = False


def _quote_body_spans(visible: str) -> list[tuple[int, int]]:
    spans = [(m.start(1), m.end(1)) for m in FENCE_RE.finditer(visible)]
    spans += _quote_spans(visible)
    return spans


def _quote_spans(visible: str) -> list[tuple[int, int]]:
    """Spans of blockquote lines, including CommonMark lazy continuations.

    A quote line has up to three spaces before '>'. An unprefixed, non-blank
    line directly after a quote line that has paragraph text is a lazy
    continuation of that paragraph (it renders inside the quote), unless it
    starts a new block. A blank line (HTML comments are already blanked) ends
    the quote.
    """
    spans: list[tuple[int, int]] = []
    offset = 0
    open_paragraph = False
    for line in visible.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        quote = QUOTE_RE.match(body)
        if quote:
            spans.append((offset, offset + len(body)))
            open_paragraph = bool(quote.group("rest").strip())
        elif open_paragraph and body.strip() and not NEW_BLOCK_RE.match(body):
            spans.append((offset, offset + len(body)))
        else:
            open_paragraph = False
        offset += len(line)
    return spans


def _bind(tokens: list[Token], visible: str, value: str, at: str | None) -> tuple[Token | None, str]:
    """Find the one token a citation binds to, or explain why there is none."""
    want = canonical(value)
    if at is None:
        hits = [tok for tok in tokens if canonical(tok.text) == want]
        if not hits:
            return None, f"STALE: {value} not found in the section text"
        if len(hits) > 1:
            return None, f"AMBIGUOUS: {value} occurs {len(hits)} times; add at: \"context\""
        return hits[0], ""
    starts = [m.start() for m in re.finditer(re.escape(at), visible)]
    if not starts:
        return None, f"STALE: context {at!r} not found in the section text"
    if len(starts) > 1:
        return None, f"AMBIGUOUS: context {at!r} occurs {len(starts)} times"
    lo, hi = starts[0], starts[0] + len(at)
    hits = [tok for tok in tokens if lo <= tok.start and tok.end <= hi and canonical(tok.text) == want]
    if not hits:
        return None, f"STALE: {value} not found inside context {at!r}"
    if len(hits) > 1:
        return None, f"AMBIGUOUS: {value} occurs {len(hits)} times inside context {at!r}"
    return hits[0], ""


def check_document(path: Path, root: Path, report: Report) -> None:
    rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
    text = path.read_text(encoding="utf-8")
    for start, section in split_sections(text):
        # Visible text: every HTML comment blanked out (offsets preserved), so
        # a citation never binds to the value written in its own comment.
        visible = re.sub(r"<!--.*?-->", lambda m: " " * len(m.group(0)), section, flags=re.DOTALL)
        spans = _quote_body_spans(visible)
        tokens = [
            Token(m.start(), m.end(), m.group(0), any(a <= m.start() < b for a, b in spans))
            for m in displayed_numbers(visible)
        ]

        for m in NOCHECK_RE.finditer(section):
            line = line_of(text, start + m.start())
            reason = m.group("reason").strip()
            if not reason:
                report.add("error", rel, line, "nocheck needs a reason")
                continue
            for item in NOCHECK_ITEM_RE.finditer(m.group("items")):
                if item.group("ctx") is not None:
                    ctx = item.group("ctx")
                    found = [x.start() for x in re.finditer(re.escape(ctx), visible)] if ctx else []
                    if len(found) != 1:
                        what = "not found" if not found else f"occurs {len(found)} times"
                        report.add("error", rel, line, f"nocheck context {ctx!r} {what}")
                        continue
                    lo, hi = found[0], found[0] + len(ctx)
                    covered = [tok for tok in tokens if lo <= tok.start and tok.end <= hi]
                    if not covered:
                        report.add("error", rel, line, f"nocheck context {ctx!r} contains no number")
                    for tok in covered:
                        tok.bound = True
                else:
                    raw = item.group("value")
                    try:
                        parse_displayed(raw)
                    except ValueError:
                        report.add("error", rel, line, f"nocheck value {raw!r} is not a number")
                        continue
                    tok, why = _bind(tokens, visible, raw, None)
                    if tok is None:
                        report.add("error", rel, line, f"nocheck {why}")
                        continue
                    tok.bound = True
            report.add("info", rel, line, f"nocheck {m.group('items').strip()}: {reason}")

        for m in CLAIM_RE.finditer(section):
            line = line_of(text, start + m.start())
            claim = m.group("value")
            source = m.group("source").strip()
            try:
                parse_displayed(claim)
            except ValueError:
                report.add("error", rel, line, f"claim value {claim!r} is not a number")
                continue
            tok, why = _bind(tokens, visible, claim, m.group("at"))
            if tok is None:
                report.add("error", rel, line, f"{why} (claim {claim})")
                continue
            tok.bound = True
            tol = None
            if m.group("tol"):
                try:
                    tol = float(m.group("tol"))
                except ValueError:
                    report.add("error", rel, line, f"bad tol {m.group('tol')!r}")
                    continue
            try:
                actual = resolve_source(source, root)
            except SourceError as exc:
                report.add("error", rel, line, f"MISSING source for {claim} ({source}): {exc}")
                continue
            ok, claimed, tolerance = compare(claim, actual, tol)
            if ok:
                report.add("ok", rel, line, f"{claim} == {actual:g} ({source})")
            else:
                report.add(
                    "error",
                    rel,
                    line,
                    f"MISMATCH {claim} (={claimed:g}) vs source {actual:g}, "
                    f"tolerance {tolerance:g} ({source})",
                )

        for tok in tokens:
            if tok.in_quote and not tok.bound:
                report.add("warning", rel, line_of(text, start + tok.start), f"UNCITED number {tok.text} in quoted text")


def default_targets(root: Path) -> list[Path]:
    targets = set(root.glob(CASE_STUDY_GLOB))
    for path in root.glob(DEFAULT_GLOB):
        text = path.read_text(encoding="utf-8")
        if "<!-- claim:" in text or "<!-- nocheck:" in text:
            targets.add(path)
    return sorted(targets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*", type=Path, help="markdown files (default: case studies and annotated showcase docs)")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root for source paths")
    parser.add_argument("--strict", action="store_true", help="treat uncited numbers as errors")
    parser.add_argument("-v", "--verbose", action="store_true", help="also print passing claims and nocheck notes")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    paths = [p if p.is_absolute() else (Path.cwd() / p) for p in args.paths] or default_targets(root)
    report = Report()
    for path in paths:
        if not path.is_file():
            report.add("error", str(path), 0, "file not found")
            continue
        check_document(path.resolve(), root, report)

    for f in report.findings:
        if f.level in ("ok", "info") and not args.verbose:
            continue
        level = "error" if (f.level == "warning" and args.strict) else f.level
        print(f"{f.path}:{f.line}: {level}: {f.message}")

    errors = report.count("error") + (report.count("warning") if args.strict else 0)
    print(
        f"showcase claims: {report.count('ok')} matched, {report.count('error')} errors, "
        f"{report.count('warning')} uncited, {sum(1 for f in report.findings if f.level == 'info')} nocheck"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
