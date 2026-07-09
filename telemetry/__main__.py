"""makerbench-telemetry CLI (Epic #569).

Makes the post-mortem telemetry engine reachable. Two subcommands wire the
real harvest -> store -> report flow:

    # Harvest a finished session directory into the JSONL store.
    python -m telemetry harvest runs/session-42 --store data/sessions.jsonl

    # Rank which signals correlate with pull_requests_opened.
    python -m telemetry report --store data/sessions.jsonl --min-n 5 -o report.json

The installed console script ``makerbench-telemetry`` invokes the same ``main``.

See ``telemetry/harvester.py`` for the expected session directory layout.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from . import __version__
from .harvester import harvest
from .report import correlate


def _cmd_harvest(args: argparse.Namespace) -> int:
    try:
        payload = harvest(args.session_dir, store_path=args.store)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"harvested session {payload.session_id} "
        f"(agent={payload.agent_id}, "
        f"prs={payload.git_metrics.get('pull_requests_opened', 0)}) "
        f"-> {args.store}"
    )
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    result = correlate(path=args.store, min_n=args.min_n)
    text = json.dumps(result, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"wrote {args.output} (status={result['status']}, n={result['n']})")
    else:
        print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="makerbench-telemetry",
        description="Harvest agent session telemetry and report signal correlations.",
    )
    parser.add_argument(
        "--version", action="version", version=f"makerbench-telemetry {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    harvest_p = sub.add_parser(
        "harvest", help="Harvest a finished session directory into the JSONL store."
    )
    harvest_p.add_argument("session_dir", help="Path to the finished session directory.")
    harvest_p.add_argument(
        "--store", default="data/sessions.jsonl", help="JSONL telemetry store path."
    )
    harvest_p.set_defaults(func=_cmd_harvest)

    report_p = sub.add_parser(
        "report", help="Rank telemetry signals by correlation with pull_requests_opened."
    )
    report_p.add_argument(
        "--store", default="data/sessions.jsonl", help="JSONL telemetry store path."
    )
    report_p.add_argument(
        "--min-n",
        type=int,
        default=5,
        help="Minimum sessions required before a correlation is reported.",
    )
    report_p.add_argument(
        "-o", "--output", default=None, help="Write the report JSON here (default: stdout)."
    )
    report_p.set_defaults(func=_cmd_report)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
