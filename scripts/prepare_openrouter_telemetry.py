#!/usr/bin/env python3
"""Offline historical proxy from public cost/runtime rows, never a model call.

This writes SessionTelemetry-shaped records for `arena estimate`. These are
historical task-stack measurements, not new arena sessions or verified invoices.
"""

import argparse
import json
import math
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=Path("results"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    records = []
    for path in sorted(args.results_dir.rglob("*.json")):
        bundle = json.loads(path.read_text())
        if not isinstance(bundle, dict) or bundle.get("agent_identifier") != "openrouter_api":
            continue
        for number, row in enumerate(bundle.get("results") or []):
            cost = (row.get("cost") or {}).get("total_cost_usd")
            duration = (row.get("runtime") or {}).get("wall_time_s")
            if any(not isinstance(v, (int, float)) or isinstance(v, bool)
                   or not math.isfinite(v) or v < 0 for v in (cost, duration)):
                continue
            source = "results/" + path.relative_to(args.results_dir).as_posix()
            records.append({
                "session_id": f"{source}:{number}",
                "agent_id": "openrouter-" + bundle["model_identifier"],
                "duration_seconds": duration,
                "telemetry": {"cost_usd": cost, "backend": "openscad",
                              "source_bundle": source, "measurement_kind": "historical_task_stack_proxy",
                              "cost_source": (row.get("cost") or {}).get("source")},
            })
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(r, allow_nan=False) + "\n" for r in records))
    print(json.dumps({"records": len(records), "models": sorted({r["agent_id"] for r in records})}))


if __name__ == "__main__":
    main()
