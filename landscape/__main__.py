"""CLI entry point for the landscape package.

Usage
-----
python -m landscape [--format text|json|markdown|csv] [--dims dim1,dim2,...]

Options
-------
--format    Output format: text (default), json, markdown, csv.
--dims      Comma-separated list of matrix dimensions to include.
            Defaults to all nine.

Examples
--------
$ python -m landscape
$ python -m landscape --format json
$ python -m landscape --format markdown > docs/LANDSCAPE_REPORT.md
$ python -m landscape --format csv --dims axis,grading > matrix.csv
"""

from __future__ import annotations

import argparse
import sys

from .matrix import DIMENSIONS
from .report import generate_report


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m landscape",
        description="MakerBench-HWE competitive landscape report generator",
    )
    parser.add_argument(
        "--format", "-f",
        choices=["text", "json", "markdown", "csv"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--dims", "-d",
        default=None,
        help=(
            "Comma-separated list of comparison dimensions to include. "
            f"Available: {', '.join(DIMENSIONS)}"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    dims: list[str] | None = None
    if args.dims:
        dims = [d.strip() for d in args.dims.split(",") if d.strip()]
        unknown = [d for d in dims if d not in DIMENSIONS]
        if unknown:
            print(
                f"ERROR: unknown dimension(s): {unknown}\n"
                f"Available: {DIMENSIONS}",
                file=sys.stderr,
            )
            return 2

    try:
        report = generate_report(matrix_dimensions=dims)
    except Exception as exc:  # pragma: no cover
        print(f"ERROR generating report: {exc}", file=sys.stderr)
        return 1

    if report.total_entries == 0:
        print(
            "WARNING: no entries loaded. Install PyYAML to read docs/landscape.yaml.",
            file=sys.stderr,
        )

    if args.format == "text":
        print(report.to_text())
    elif args.format == "json":
        print(report.to_json())
    elif args.format == "markdown":
        print(report.to_markdown())
    elif args.format == "csv":
        print(report.matrix_csv, end="")

    return 0


if __name__ == "__main__":
    sys.exit(main())
