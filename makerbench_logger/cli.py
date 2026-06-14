"""CLI for emitting MakerBench WorkflowManifest files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from .core import (
    build_manifest,
    emit_manifest,
    load_tool_log,
    write_mbc_if_available,
)

app = typer.Typer(
    add_completion=False,
    help="Emit MakerBench WorkflowManifest JSON from an agent tool-call log.",
)


def _parse_metadata(items: Optional[list[str]]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise typer.BadParameter("--metadata entries must be key=value")
        key, value = item.split("=", 1)
        metadata[key] = value
    return metadata


@app.command("emit")
def emit(
    log: Path = typer.Option(
        ...,
        "--log",
        exists=True,
        readable=True,
        help="JSON or JSONL tool log.",
    ),
    out: Path = typer.Option(..., "--out", help="WorkflowManifest JSON output path."),
    run_id: str = typer.Option(..., "--run-id", help="Stable run/session identifier."),
    orchestrator: Optional[str] = typer.Option(None, help="Top-level agent orchestrator."),
    framework: Optional[str] = typer.Option(None, help="Agent/runtime framework."),
    host_application: Optional[str] = typer.Option(None, help="CAD or maker host application."),
    execution_bridge: Optional[str] = typer.Option(None, help="Bridge used to drive the host app."),
    started_at: Optional[str] = typer.Option(None, help="Run start timestamp."),
    completed_at: Optional[str] = typer.Option(None, help="Run completion timestamp."),
    wall_clock_seconds: Optional[float] = typer.Option(None, help="Run wall-clock duration."),
    input_tokens: Optional[int] = typer.Option(None, help="Input token count when known."),
    output_tokens: Optional[int] = typer.Option(None, help="Output token count when known."),
    total_tokens: Optional[int] = typer.Option(None, help="Total token count when known."),
    metadata: Optional[list[str]] = typer.Option(
        None,
        "--metadata",
        help="Extra manifest metadata as key=value. May be repeated.",
    ),
    mbc_out: Optional[Path] = typer.Option(
        None,
        "--mbc-out",
        help="Optional .mbc certificate path; written only when makerbench.write_mbc is installed.",
    ),
):
    """Write a WorkflowManifest JSON from a fake or real tool-call log."""
    stack = {
        key: value
        for key, value in {
            "orchestrator": orchestrator,
            "framework": framework,
            "host_application": host_application,
            "execution_bridge": execution_bridge,
        }.items()
        if value is not None
    }
    tokens = {
        key: value
        for key, value in {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
        }.items()
        if value is not None
    }
    manifest = build_manifest(
        load_tool_log(log),
        run_id=run_id,
        stack=stack,
        started_at=started_at,
        completed_at=completed_at,
        wall_clock_seconds=wall_clock_seconds,
        tokens=tokens,
        metadata=_parse_metadata(metadata),
    )
    emit_manifest(manifest, out)
    typer.echo(f"Wrote {out}")

    if mbc_out is not None:
        if write_mbc_if_available(manifest, mbc_out):
            typer.echo(f"Wrote {mbc_out}")
        else:
            typer.echo(
                "Skipped .mbc: no makerbench write_mbc() helper is installed yet.",
                err=True,
            )


@app.command("inspect")
def inspect_manifest(path: Path = typer.Argument(..., exists=True, readable=True)):
    """Print a compact summary of a WorkflowManifest."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    typer.echo(json.dumps({
        "run_id": payload.get("run_id"),
        "tool_calls": payload.get("metrics", {}).get("tool_calls"),
        "autonomy_ratio": payload.get("autonomy_ratio"),
        "human_intervention_index": payload.get("human_intervention_index"),
    }, indent=2, sort_keys=True))
