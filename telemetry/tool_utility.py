"""Tool utility & fan-out metrics extractor (Epic #569, Story #573)."""

from __future__ import annotations

from typing import Any


def compute(tool_log: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute tool-utility and fan-out metrics from a session tool log.

    Each entry is a dict with at least:
      ``tool``: str — tool name (e.g. "Bash", "Read", "Edit")
      ``turn_id``: str — model turn that issued the call
      ``failed``: bool — True if the call exited nonzero or caused a syntax-error backtrack
    """
    tool_call_distribution: dict[str, int] = {}
    bash_invocations = 0
    failed_tool_calls = 0
    total_calls = 0

    # Track batching: calls per turn
    calls_per_turn: dict[str, int] = {}

    for event in tool_log:
        tool_name = event.get("tool", "unknown")
        turn_id = str(event.get("turn_id", ""))
        failed = bool(event.get("failed", False))

        total_calls += 1
        tool_call_distribution[tool_name] = tool_call_distribution.get(tool_name, 0) + 1
        if tool_name.lower() == "bash":
            bash_invocations += 1
        if failed:
            failed_tool_calls += 1
        calls_per_turn[turn_id] = calls_per_turn.get(turn_id, 0) + 1

    # A "model round-trip" is any turn that issued ≥1 tool call.
    # A "programmatic tool batch" is any turn that issued ≥2 tool calls in a single turn.
    model_round_trips = len(calls_per_turn)
    programmatic_tool_batches = sum(1 for c in calls_per_turn.values() if c >= 2)

    batch_ratio = (programmatic_tool_batches / model_round_trips) if model_round_trips > 0 else 0.0
    tool_failure_rate = (failed_tool_calls / total_calls) if total_calls > 0 else 0.0

    return {
        "tool_call_distribution": tool_call_distribution,
        "bash_invocations": bash_invocations,
        "programmatic_tool_batches": programmatic_tool_batches,
        "model_round_trips": model_round_trips,
        "batch_ratio": batch_ratio,
        "failed_tool_calls": failed_tool_calls,
        "tool_failure_rate": tool_failure_rate,
    }
