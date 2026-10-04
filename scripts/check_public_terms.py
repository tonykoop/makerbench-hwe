#!/usr/bin/env python3
"""Check public text against hash-only private-term fingerprints.

Diagnostics identify locations without echoing matched text. Tokens and adjacent
hyphen-joined token pairs are lowercased before SHA-256 hashing.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DENYLIST = ROOT / "scripts" / "private_term_hashes.txt"
TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
TEXT_SUFFIXES = {".md", ".html", ".json", ".js", ".css", ".svg", ".txt",
                 ".py", ".yaml", ".yml", ".xml", ".csv", ".toml"}


def load_hashes(path: Path) -> frozenset[str]:
    hashes = path.read_text(encoding="ascii").splitlines()
    if not hashes or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes):
        raise ValueError("Denylist must contain only lowercase SHA-256 hashes, one per line")
    return frozenset(hashes)


def matched_lines(text: str, hashes: frozenset[str]) -> list[int]:
    tokens = list(TOKEN.finditer(text))
    positions = set()
    for index, token in enumerate(tokens):
        values = [token.group().lower()]
        if index + 1 < len(tokens):
            values.append(token.group().lower() + "-" + tokens[index + 1].group().lower())
        if any(hashlib.sha256(value.encode("utf-8")).hexdigest() in hashes for value in values):
            positions.add(text.count("\n", 0, token.start()) + 1)
    return sorted(positions)


def public_paths(root: Path):
    for name in ("docs", "site"):
        directory = root / name
        if directory.is_dir():
            yield from sorted(p for p in directory.rglob("*")
                              if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES)
    yield root / "README.md"


def check(root: Path, hashes: frozenset[str]) -> list[str]:
    problems = []
    for path in public_paths(root):
        text = path.read_text(encoding="utf-8")
        for line in matched_lines(text, hashes):
            problems.append(f"{path.relative_to(root)}:{line}: forbidden term fingerprint")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--denylist", type=Path, default=DENYLIST)
    args = parser.parse_args(argv)
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
