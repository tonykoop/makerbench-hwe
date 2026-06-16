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
"""
from __future__ import annotations

import argparse
import json
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
_LIST_FIELDS = ("input_modality", "output_representation", "fab_processes")
_SCALAR_FIELDS = (
    "name", "type", "source", "source_alt", "date", "framing_own", "scope",
    "grading", "grading_detail", "agentic", "openness", "backing",
    "dataset_scale", "integrity", "anti_memorization", "note",
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


def build(yaml_path: Path) -> dict:
    doc = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    entries = [_normalize(e) for e in (doc.get("entries") or [])]
    sweep = dict(doc.get("sweep") or {})
    if isinstance(sweep.get("method"), str):
        sweep["method"] = _collapse(sweep["method"])
    return {
        "_comment": (
            "GENERATED from docs/landscape.yaml by site/landscape_data.py — "
            "do not edit by hand; re-run on each quarterly sweep."
        ),
        "sweep": sweep,
        "count": len(entries),
        "entries": entries,
    }


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yaml", type=Path, default=repo_root / "docs" / "landscape.yaml")
    parser.add_argument("--out", type=Path, default=script_dir / "data" / "landscape.json")
    parser.add_argument(
        "--check", action="store_true",
        help="exit 1 if the committed JSON is missing or stale (CI sync guard)",
    )
    args = parser.parse_args()

    payload = build(args.yaml)
    # default=str coerces YAML-parsed datetime.date values (bare 2026-06-10) to
    # ISO strings so the payload is JSON-serializable and deterministic.
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n"

    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
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
        f"sweep {payload['sweep'].get('date', '?')})."
    )


if __name__ == "__main__":
    main()
