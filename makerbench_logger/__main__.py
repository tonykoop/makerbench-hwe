"""makerbench-logger CLI.

The installed ``makerbench-logger emit ...`` command, runnable standalone as::

    makerbench-logger emit --tool-call-log calls.json -o workflow_manifest.json

Reads a JSON tool-call log (a list of calls, or an object with stack/metrics +
``tool_call_log``), assembles a :class:`WorkflowManifest`, writes it, and
optionally emits bob's ``.mbc`` certificate (mb#109) when that helper is present.

Input tool-call log shape (minimal)::

    [
      {"name": "blender.add_cube", "params": {"size": 20}},
      {"name": "blender.bevel", "params": {"width": 1}, "human_steering": "L1"}
    ]

Or the richer object form::

    {
      "task_id": "bracket_v1", "seed": 0,
      "stack": {"framework": "blender-mcp", "execution_bridge": "mcp"},
      "metrics": {"inference_tokens": 4200, "estimated_cost_usd": 0.06},
      "provenance_trace": {"session_recording_hash": "sha256:..."},
      "tool_call_log": [ ... ]
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from . import __version__
from .logger import WorkflowLogger
from .manifest import Stack


def _load_log(path: Path) -> dict[str, Any]:
    """Normalize either the bare-list or object tool-call-log form to the object form."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {"tool_call_log": data}
    if isinstance(data, dict):
        return data
    raise ValueError("tool-call log must be a JSON list of calls or an object with 'tool_call_log'")


def _logger_from_log(log_obj: dict[str, Any], *, run_id: Optional[str], task_id: Optional[str],
                     seed: Optional[int]) -> WorkflowLogger:
    stack_kwargs = log_obj.get("stack") or {}
    logger = WorkflowLogger(
        run_id=run_id or log_obj.get("run_id"),
        task_id=task_id if task_id is not None else log_obj.get("task_id"),
        seed=seed if seed is not None else log_obj.get("seed"),
        stack=Stack(**stack_kwargs) if stack_kwargs else None,
    )
    metrics = log_obj.get("metrics") or {}
    logger.set_metrics(
        inference_tokens=metrics.get("inference_tokens"),
        estimated_cost_usd=metrics.get("estimated_cost_usd"),
    )
    prov = log_obj.get("provenance_trace") or {}
    logger.set_provenance(
        tool_call_log_url=prov.get("tool_call_log_url"),
        session_recording_hash=prov.get("session_recording_hash"),
    )
    for artifact in log_obj.get("artifacts") or []:
        logger.add_artifact(artifact)
    for note in log_obj.get("notes") or []:
        logger.add_note(note)
    for call in log_obj.get("tool_call_log") or []:
        logger.tool_call(
            call["name"],
            call.get("params"),
            steering=call.get("human_steering", call.get("steering", "L0")),
            iteration=call.get("iteration"),
            note=call.get("note"),
            timestamp=call.get("timestamp"),
        )
    # wall-clock from a log replay is not a live measurement; honor an explicit value if given.
    if metrics.get("wall_clock_seconds") is not None:
        logger._wall_clock_seconds = metrics["wall_clock_seconds"]  # noqa: SLF001 (replay passthrough)
    return logger


def _cmd_emit(args: argparse.Namespace) -> int:
    log_obj = _load_log(Path(args.tool_call_log))
    logger = _logger_from_log(log_obj, run_id=args.run_id, task_id=args.task_id, seed=args.seed)
    if args.framework:
        logger.stack.framework = args.framework
    if args.orchestrator:
        logger.stack.orchestrator = args.orchestrator
    if args.execution_bridge:
        logger.stack.execution_bridge = args.execution_bridge
    try:
        manifest = logger.emit(
            args.output,
            validate=not args.no_validate,
            write_mbc=args.mbc,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    hii = manifest["hii"]
    print(
        f"wrote {args.output}  "
        f"(tool_calls={manifest['metrics']['tool_calls_count']}, "
        f"HII={hii['highest_level']}, autonomy_ratio={hii['autonomy_ratio']})"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="makerbench-logger",
        description="Emit a WorkflowManifest (mb#89) from an agent's tool-call log.",
    )
    parser.add_argument("--version", action="version", version=f"makerbench-logger {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    emit = sub.add_parser("emit", help="Build a workflow_manifest.json from a tool-call log.")
    emit.add_argument("--tool-call-log", required=True, help="Path to the JSON tool-call log.")
    emit.add_argument("-o", "--output", default="workflow_manifest.json", help="Output manifest path.")
    emit.add_argument("--run-id", default=None)
    emit.add_argument("--task-id", default=None)
    emit.add_argument("--seed", type=int, default=None)
    emit.add_argument("--orchestrator", default=None)
    emit.add_argument("--framework", default=None)
    emit.add_argument("--execution-bridge", default=None)
    emit.add_argument("--no-validate", action="store_true",
                      help="Skip makerbench.schema validation even when it is installed.")
    emit.add_argument("--mbc", action="store_true",
                      help="Also emit bob's .mbc certificate (mb#109) if write_mbc() is available.")
    emit.set_defaults(func=_cmd_emit)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
