"""Context-dynamics extractor (Epic #569, Story #571).

Extracts compaction events, tool-clearing events, per-turn token velocity, and
growth class from a parsed session transcript/payload structure.

Data-source note (load-bearing): token counts and compaction_events come from
the API payload dump, not the plain tmux pane text. The ``transcript`` arg
should be the parsed payload structure, not raw pane text.
"""

from __future__ import annotations

import math
from typing import Any


def extract(transcript: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract context-dynamics signals from a session transcript.

    Each turn in ``transcript`` is a dict with:
      ``role``: "assistant" | "user" | "tool"
      ``content``: str  — turn text / tool result text
      ``token_delta``: int — net token count change after this turn (from API dump)
      ``has_compaction``: bool — True if this assistant turn starts with a <summary> block
      ``has_tool_clearing``: bool — True if this turn contains an explicit tool-result clear

    Returns:
        compaction_events: int
        tool_clearing_events: int
        context_velocity_avg_tokens_per_turn: float
        token_velocity_series: list[int]
        growth_class: "saw_tooth" | "runaway_exponential"
    """
    compaction_events = 0
    tool_clearing_events = 0
    token_velocity_series: list[int] = []

    for turn in transcript:
        role = turn.get("role", "")
        content = str(turn.get("content", ""))
        token_delta = int(turn.get("token_delta", 0))
        has_compaction = bool(turn.get("has_compaction", False))
        has_tool_clearing = bool(turn.get("has_tool_clearing", False))

        # Fallback: count <summary> tags at the start of assistant turn content
        if role == "assistant" and not has_compaction:
            stripped = content.lstrip()
            if stripped.startswith("<summary>"):
                has_compaction = True

        if has_compaction:
            compaction_events += 1
        if has_tool_clearing:
            tool_clearing_events += 1

        token_velocity_series.append(token_delta)

    if token_velocity_series:
        context_velocity_avg = sum(token_velocity_series) / len(token_velocity_series)
    else:
        context_velocity_avg = 0.0

    growth_class = _classify_growth(token_velocity_series, compaction_events)

    return {
        "compaction_events": compaction_events,
        "tool_clearing_events": tool_clearing_events,
        "context_velocity_avg_tokens_per_turn": context_velocity_avg,
        "token_velocity_series": token_velocity_series,
        "growth_class": growth_class,
    }


def _classify_growth(series: list[int], compaction_events: int) -> str:
    """Classify token-velocity series as saw_tooth or runaway_exponential.

    Saw-tooth: series resets downward at least once after a compaction event, or
    the overall trend is not monotonically increasing.
    Runaway exponential: monotonic super-linear growth with no resets.
    """
    if not series or len(series) < 2:
        return "saw_tooth"

    # Check for any downward reset (a turn with negative or zero delta after positives)
    has_reset = any(series[i] < series[i - 1] for i in range(1, len(series)))

    if has_reset or compaction_events > 0:
        return "saw_tooth"

    # Check for super-linear (accelerating) growth: each delta >= previous
    monotonic_increasing = all(series[i] >= series[i - 1] for i in range(1, len(series)))
    if monotonic_increasing and len(series) >= 3:
        return "runaway_exponential"

    return "saw_tooth"
