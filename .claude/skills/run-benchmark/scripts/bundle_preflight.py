#!/usr/bin/env python3
"""Generic, dependency-light preflight for a benchmark result bundle.

Run this BEFORE opening a submission PR. It is a cheap sanity gate, not a grader:
it checks that a bundle carries its contamination canary, leaks no answer-key or
source paths, populates the metadata a leaderboard needs, and isn't self-marked
as verified. It deliberately does NOT recompute scores -- reproducing the score is
the benchmark's own regrade/reproduce step, which this never replaces.

It knows MakerBench's schema by default but is written to degrade gracefully on
unfamiliar bundles: unknown fields are reported as "couldn't check", never as a
pass. Stdlib only; works anywhere Python 3.8+ runs.

Usage:
    python bundle_preflight.py results/<model>/r_*.json
    python bundle_preflight.py --canary "<expected-canary-string>" bundle.json
    python bundle_preflight.py --verified-default unverified bundle1.json bundle2.json

Exit code 0 = clean (warnings allowed), 1 = at least one ERROR.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

# Path segments that should never appear in a public bundle's artifact paths --
# they signal an answer key or a private archive leaking into public metadata.
PRIVATE_SEGMENTS = (
    "private", "oracle", "oracles", "answer", "answers", "gold",
    "heldout", "held-out", "held_out", "solution", "submissions",
)
# A drive-letter (C:\) or POSIX-root (/...) absolute path, or parent traversal.
ABSOLUTE_OR_TRAVERSAL = re.compile(r"^(?:[a-zA-Z]:[\\/]|[\\/])|(?:^|[\\/])\.\.(?:[\\/]|$)")

DEFAULT_VERIFIED_STATES = ("unverified", "", None)


class Findings:
    def __init__(self, path: str):
        self.path = path
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.checked: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def ok(self, msg: str) -> None:
        self.checked.append(msg)


def _iter_strings(obj):
    """Yield every string value anywhere in a nested JSON structure."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


def _looks_like_path(s: str) -> bool:
    return ("/" in s or "\\" in s) and len(s) < 400 and "\n" not in s


def check_canary(data, raw_text: str, expected: str | None, f: Findings) -> None:
    if expected:
        if expected in raw_text:
            f.ok(f"canary present ({expected[:24]}...)")
        else:
            f.error("expected canary string not found in bundle")
        return
    # No expected canary given: look for a canary-ish field, else warn.
    found = None
    if isinstance(data, dict):
        for k, v in data.items():
            if "canary" in k.lower() and isinstance(v, str) and v.strip():
                found = v
                break
    if found:
        f.ok(f"canary field present ({found[:24]}...)")
    else:
        f.warn("no canary found; pass --canary to enforce, or confirm this "
               "benchmark has no contamination canary")


def check_leaks(data, f: Findings) -> None:
    leaked = []
    for s in _iter_strings(data):
        if not _looks_like_path(s):
            continue
        low = s.lower()
        segs = re.split(r"[\\/]+", low)
        if any(seg in PRIVATE_SEGMENTS for seg in segs):
            leaked.append(s)
        elif ABSOLUTE_OR_TRAVERSAL.search(s):
            leaked.append(s)
    if leaked:
        for s in sorted(set(leaked))[:10]:
            f.error(f"path looks private/unsafe for a public bundle: {s}")
    else:
        f.ok("no private/absolute/traversal paths in bundle")


def check_metadata(data, f: Findings) -> None:
    if not isinstance(data, dict):
        f.warn("top level is not an object; can't check metadata fields")
        return
    mid = data.get("model_identifier") or data.get("model_id") or data.get("model")
    if mid:
        f.ok(f"model identifier present ({mid})")
    else:
        f.error("no model_identifier/model_id/model field")

    results = data.get("results")
    if isinstance(results, list) and results:
        f.ok(f"{len(results)} result row(s)")
        missing = 0
        for r in results:
            if not isinstance(r, dict):
                missing += 1
                continue
            grade = r.get("grade") if isinstance(r.get("grade"), dict) else r
            score = grade.get("score") if isinstance(grade, dict) else None
            note = (r.get("grade") or {}).get("notes") if isinstance(r.get("grade"), dict) else r.get("notes")
            if score is None and note != "agent_error":
                missing += 1
        if missing:
            f.warn(f"{missing} row(s) have no score and aren't marked agent_error "
                   "-- confirm that's intended")
        else:
            f.ok("every row has a score or is a recorded error")
    else:
        f.error("no non-empty 'results' array")


def check_verification(data, default_states, f: Findings) -> None:
    if not isinstance(data, dict):
        return
    vs = data.get("verification_status", "__absent__")
    if vs == "__absent__":
        f.ok("no verification_status field (fine; ingest assigns it)")
    elif vs in default_states:
        f.ok(f"verification_status at default ({vs!r})")
    else:
        f.error(f"verification_status is {vs!r} -- submitters must not self-verify; "
                "leave it at the default and let the maintainer regrade raise it")


def preflight_file(path: str, expected_canary, default_states) -> Findings:
    f = Findings(path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        data = json.loads(raw)
    except FileNotFoundError:
        f.error("file not found")
        return f
    except json.JSONDecodeError as e:
        f.error(f"invalid JSON: {e}")
        return f
    check_canary(data, raw, expected_canary, f)
    check_leaks(data, f)
    check_metadata(data, f)
    check_verification(data, default_states, f)
    return f


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="+", help="result bundle JSON file(s) or globs")
    p.add_argument("--canary", default=os.environ.get("BENCH_CANARY"),
                   help="exact canary string that must appear in each bundle")
    p.add_argument("--verified-default", action="append", default=None,
                   help="verification_status value(s) considered an acceptable "
                        "default (repeatable; default: 'unverified'/empty)")
    args = p.parse_args(argv)

    default_states = tuple(args.verified_default) if args.verified_default else DEFAULT_VERIFIED_STATES

    files: list[str] = []
    for pat in args.paths:
        hits = glob.glob(pat)
        files.extend(hits if hits else [pat])

    any_error = False
    for path in files:
        f = preflight_file(path, args.canary, default_states)
        status = "FAIL" if f.errors else ("WARN" if f.warnings else "PASS")
        print(f"\n[{status}] {path}")
        for m in f.checked:
            print(f"  ok   - {m}")
        for m in f.warnings:
            print(f"  warn - {m}")
        for m in f.errors:
            print(f"  ERR  - {m}")
        any_error = any_error or bool(f.errors)

    print(f"\n{'FAIL' if any_error else 'OK'}: preflighted {len(files)} bundle(s). "
          "Reminder: this is a sanity gate, not a regrade -- still run the "
          "benchmark's reproduce/regrade before submitting.")
    return 1 if any_error else 0


if __name__ == "__main__":
    sys.exit(main())
