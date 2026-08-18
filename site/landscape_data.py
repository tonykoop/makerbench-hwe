#!/usr/bin/env python3
"""Generate ``site/data/landscape.json`` from ``docs/landscape.yaml``.

The site's "Benchmark of Benchmarks" section (issue #285) renders from this JSON.
It is intentionally a SEPARATE, local-run generator rather than part of
``build_data.py``: the Pages deploy runs ``build_data.py`` stdlib-only (no
``pip install``), and parsing YAML needs PyYAML (already a dev dependency, used by
``tests/test_landscape_yaml.py``). So this runs LOCALLY on each quarterly landscape
sweep, and the regenerated ``site/data/landscape.json`` is committed alongside the
updated ``docs/landscape.yaml`` (``site/data`` is a committed, served directory).

    python site/landscape_data.py            # write site/data/landscape.json
    python site/landscape_data.py --check     # exit 1 if the committed JSON is stale

Wiring hook for the front-end refresh work: the site fetches ``data/landscape.json``;
this script is the only thing that writes it.

Capture-cluster ingest (issue #674): markdown capture docs dropped into
``docs/landscape-captures/`` (competitive scans #610, competitor teardowns #611,
prior-art writeups #612 — see that directory's README for the file convention)
are scanned into a ``captures`` array so the landscape page can list them with
links. An empty/missing directory yields an empty array; nothing blocks on the
capture issues landing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - dev dependency
    sys.stderr.write(
        "PyYAML is required (dev dependency): pip install pyyaml\n"
    )
    raise

# Lists pass through as lists; everything else as collapsed-whitespace scalars.
_LIST_FIELDS = ("axis", "input_modality", "output_representation", "fab_processes")
_SCALAR_FIELDS = (
    "name", "type", "source", "source_alt", "date", "framing_own", "scope",
    "grading", "grading_detail", "agentic", "openness", "backing",
    "dataset_scale", "integrity", "anti_memorization", "note",
    "kind",
)


def _collapse(value: str) -> str:
    """Collapse YAML block-scalar whitespace so the site shows clean one-liners."""
    return " ".join(str(value).split())


def _normalize(entry: dict) -> dict:
    out: dict = {}
    for key in _SCALAR_FIELDS:
        val = entry.get(key)
        if val is not None:
            out[key] = _collapse(val) if isinstance(val, str) else val
    for key in _LIST_FIELDS:
        val = entry.get(key) or []
        out[key] = list(val) if isinstance(val, (list, tuple)) else [val]
    out["recent"] = bool(entry.get("recent"))
    out["is_self"] = str(entry.get("name", "")).lower().startswith("makerbench")
    return out


# Capture docs live here (repo-relative); links point at the main-branch blobs.
CAPTURES_DIR = Path("docs") / "landscape-captures"
_REPO_BLOB_BASE = "https://github.com/tonykoop/makerbench-hwe/blob/main/"
_SUMMARY_MAX = 280


def _capture_summary(lines: list[str]) -> str:
    """First non-heading, non-blank paragraph line, collapsed and truncated."""
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        text = _collapse(stripped)
        if len(text) > _SUMMARY_MAX:
            text = text[: _SUMMARY_MAX - 1].rstrip() + "…"
        return text
    return ""


def collect_captures(captures_dir: Path) -> list[dict]:
    """Scan ``docs/landscape-captures/*.md`` into the ``captures`` payload.

    Deterministic (sorted by filename), README excluded, tolerant of an
    absent directory so the site build never blocks on #610/#611/#612.
    """
    if not captures_dir.is_dir():
        return []
    captures: list[dict] = []
    for path in sorted(captures_dir.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        title = path.stem
        for line in lines:
            if line.startswith("# "):
                title = _collapse(line[2:])
                break
        entry: dict = {
            "title": title,
            "summary": _capture_summary(lines),
            "path": str(CAPTURES_DIR / path.name).replace("\\", "/"),
            "url": _REPO_BLOB_BASE + str(CAPTURES_DIR / path.name).replace("\\", "/"),
        }
        issue_match = re.match(r"^(\d+)-", path.name)
        if issue_match:
            entry["issue"] = int(issue_match.group(1))
        captures.append(entry)
    return captures


def build(yaml_path: Path, captures_dir: Path | None = None) -> dict:
    doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    entries = [_normalize(e) for e in (doc.get("entries") or [])]
    sweep = dict(doc.get("sweep") or {})
    if isinstance(sweep.get("method"), str):
        sweep["method"] = _collapse(sweep["method"])
    if captures_dir is None:
        captures_dir = yaml_path.resolve().parent.parent / CAPTURES_DIR
    captures = collect_captures(captures_dir)
    return {
        "_comment": (
            "GENERATED from docs/landscape.yaml (+ docs/landscape-captures/) "
            "by site/landscape_data.py — do not edit by hand; re-run on each "
            "quarterly sweep or when a capture doc lands."
        ),
        "sweep": sweep,
        "count": len(entries),
        "entries": entries,
        "captures_count": len(captures),
        "captures": captures,
    }


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yaml", type=Path, default=repo_root / "docs" / "landscape.yaml")
    parser.add_argument(
        "--captures", type=Path, default=repo_root / CAPTURES_DIR,
        help="capture-docs directory to ingest (default: docs/landscape-captures)",
    )
    parser.add_argument("--out", type=Path, default=script_dir / "data" / "landscape.json")
    parser.add_argument(
        "--check", action="store_true",
        help="exit 1 if the committed JSON is missing or stale (CI sync guard)",
    )
    args = parser.parse_args()

    payload = build(args.yaml, captures_dir=args.captures)
    # default=str coerces YAML-parsed datetime.date values (bare 2026-06-10) to
    # ISO strings so the payload is JSON-serializable and deterministic.
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n"

    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        # Compare line-ending-insensitively: a CRLF checkout (Windows
        # core.autocrlf) must not report a semantically-identical JSON as stale.
        if current.replace("\r\n", "\n") != text.replace("\r\n", "\n"):
            sys.stderr.write(
                f"{args.out} is stale — run: python site/landscape_data.py\n"
            )
            sys.exit(1)
        print(f"{args.out} is up to date ({payload['count']} entries).")
        return

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(
        f"Wrote {args.out} ({payload['count']} entries; "
        f"{payload['captures_count']} capture docs; "
        f"sweep {payload['sweep'].get('date', '?')})."
    )


if __name__ == "__main__":
    main()
