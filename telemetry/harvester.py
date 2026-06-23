"""Supervisor harvest daemon (Epic #569, Story #575).

Assembles a complete SessionTelemetry payload from a finished session directory
by calling the A2–A5 extractors and writing the result to the JSONL store.

Expected session directory layout:
  <session_dir>/
    pane.log          — tmux pane dump (plain text transcript)
    payload.json      — API payload dump (JSON; optional but recommended for token counts)
    git_events.json   — git/gh activity log (list of event dicts)
    tool_events.json  — tool call log (list of event dicts)
    prompt.txt        — session prompt text
    backlog.json      — list of issue dicts (backlog)
    meta.json         — {"session_id": ..., "agent_id": ..., "duration_seconds": ...}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context_dynamics import extract as extract_context
from .git_throughput import compute as compute_git
from .input_architecture import analyze as analyze_input
from .schema import SessionTelemetry
from .store import append
from .tool_utility import compute as compute_tools


def harvest(
    session_dir: str | Path,
    store_path: str = "data/sessions.jsonl",
) -> SessionTelemetry:
    """Harvest a completed session into the telemetry store.

    Reads the session directory artifacts, calls A2–A5 extractors, assembles
    a SessionTelemetry, and appends it to the JSONL store.

    Returns the assembled SessionTelemetry for inspection/testing.
    """
    d = Path(session_dir)

    meta = _load_json(d / "meta.json", default={})
    session_id = str(meta.get("session_id", d.name))
    agent_id = str(meta.get("agent_id", "unknown"))
    duration_seconds = float(meta.get("duration_seconds", 0.0))

    # A2: context dynamics from pane log + payload
    transcript = _load_transcript(d)
    ctx = extract_context(transcript)

    # A3: git throughput from git events log
    git_events = _load_json_list(d / "git_events.json")
    git = compute_git(git_events)

    # A4: tool utility from tool events log
    tool_events = _load_json_list(d / "tool_events.json")
    tools = compute_tools(tool_events)

    # A5: input architecture from prompt + backlog
    prompt_text = _load_text(d / "prompt.txt")
    backlog = _load_json_list(d / "backlog.json")
    inp = analyze_input(prompt_text, backlog)

    # Merge API token counts from payload if available
    payload_meta = _load_json(d / "payload.json", default={})
    total_input_tokens = int(payload_meta.get("total_input_tokens", ctx["compaction_events"]))
    total_output_tokens = int(payload_meta.get("total_output_tokens", 0))

    telemetry_dict = {
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "compaction_events": ctx["compaction_events"],
        "tool_clearing_events": ctx["tool_clearing_events"],
        "context_velocity_avg_tokens_per_turn": ctx["context_velocity_avg_tokens_per_turn"],
        "growth_class": ctx["growth_class"],
        "batch_ratio": tools["batch_ratio"],
        "prompt_type": inp["prompt_type"],
        "atomic_density_index": inp["atomic_density_index"],
    }

    git_metrics_dict = {
        "issues_closed": git["issues_closed"],
        "pull_requests_opened": git["pull_requests_opened"],
        "issue_to_pr_ratio": git["issue_to_pr_ratio"],
        "self_correction_cycles": git["self_correction_cycles"],
        "code_retention_ratio": git["code_retention_ratio"],
        "failed_ci_runs_resolved": git["failed_ci_runs_resolved"],
    }

    tool_metrics_dict = {
        "bash_invocations": tools["bash_invocations"],
        "programmatic_tool_batches": tools["programmatic_tool_batches"],
        "model_round_trips": tools["model_round_trips"],
        "batch_ratio": tools["batch_ratio"],
        "failed_tool_calls": tools["failed_tool_calls"],
        "tool_failure_rate": tools["tool_failure_rate"],
        "tool_call_distribution": tools["tool_call_distribution"],
    }

    payload = SessionTelemetry(
        session_id=session_id,
        agent_id=agent_id,
        duration_seconds=duration_seconds,
        telemetry=telemetry_dict,
        git_metrics=git_metrics_dict,
        tool_metrics=tool_metrics_dict,
    )

    append(payload, path=store_path)
    return payload


def _load_json(path: Path, default: Any = None) -> Any:
    if path.exists():
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    return default if default is not None else {}


def _load_json_list(path: Path) -> list[dict]:
    result = _load_json(path, default=[])
    return result if isinstance(result, list) else []


def _load_text(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _load_transcript(d: Path) -> list[dict]:
    """Build transcript from payload.json (preferred) or pane.log (fallback)."""
    payload = _load_json(d / "payload.json", default={})
    if isinstance(payload.get("turns"), list):
        return payload["turns"]

    # Fallback: treat each line of pane.log as an assistant turn with no token data
    pane_log = d / "pane.log"
    if pane_log.exists():
        lines = pane_log.read_text(encoding="utf-8").splitlines()
        return [
            {
                "role": "assistant",
                "content": line,
                "token_delta": 0,
                "has_compaction": "<summary>" in line,
                "has_tool_clearing": False,
            }
            for line in lines
            if line.strip()
        ]
    return []
