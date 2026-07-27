"""Tests for context-dynamics extractor (Epic #569, Story #571)."""

from telemetry.context_dynamics import extract

# Stable saw-tooth fixture: two compaction events and downward resets
STABLE_TRANSCRIPT = [
    {"role": "user", "content": "Do task A", "token_delta": 50, "has_compaction": False, "has_tool_clearing": False},
    {"role": "assistant", "content": "Working...", "token_delta": 200, "has_compaction": False, "has_tool_clearing": False},
    # Compaction event: context drops
    {"role": "assistant", "content": "<summary>Prior work</summary>", "token_delta": -800, "has_compaction": True, "has_tool_clearing": False},
    {"role": "user", "content": "Continue", "token_delta": 50, "has_compaction": False, "has_tool_clearing": False},
    {"role": "assistant", "content": "More work", "token_delta": 180, "has_compaction": False, "has_tool_clearing": False},
    # Tool clearing
    {"role": "tool", "content": "cleared", "token_delta": -300, "has_compaction": False, "has_tool_clearing": True},
    {"role": "assistant", "content": "Done", "token_delta": 100, "has_compaction": False, "has_tool_clearing": False},
]

# Runaway fixture: monotonically increasing token deltas, no compaction, no resets
RUNAWAY_TRANSCRIPT = [
    {"role": "user", "content": "read file A", "token_delta": 100, "has_compaction": False, "has_tool_clearing": False},
    {"role": "assistant", "content": "read file A again", "token_delta": 200, "has_compaction": False, "has_tool_clearing": False},
    {"role": "assistant", "content": "read file A again", "token_delta": 400, "has_compaction": False, "has_tool_clearing": False},
    {"role": "assistant", "content": "read file A again", "token_delta": 800, "has_compaction": False, "has_tool_clearing": False},
    {"role": "assistant", "content": "read file A again", "token_delta": 1600, "has_compaction": False, "has_tool_clearing": False},
]


def test_stable_classifies_saw_tooth():
    result = extract(STABLE_TRANSCRIPT)
    assert result["growth_class"] == "saw_tooth"


def test_runaway_classifies_runaway_exponential():
    result = extract(RUNAWAY_TRANSCRIPT)
    assert result["growth_class"] == "runaway_exponential"


def test_compaction_events_counted():
    result = extract(STABLE_TRANSCRIPT)
    assert result["compaction_events"] == 1


def test_tool_clearing_events_counted():
    result = extract(STABLE_TRANSCRIPT)
    assert result["tool_clearing_events"] == 1


def test_velocity_series_length():
    result = extract(STABLE_TRANSCRIPT)
    assert len(result["token_velocity_series"]) == len(STABLE_TRANSCRIPT)


def test_avg_velocity():
    result = extract(STABLE_TRANSCRIPT)
    series = result["token_velocity_series"]
    expected_avg = sum(series) / len(series)
    assert abs(result["context_velocity_avg_tokens_per_turn"] - expected_avg) < 1e-9


def test_fallback_summary_tag_detection():
    # Compaction detected via <summary> prefix in content even without has_compaction flag
    transcript = [
        {"role": "assistant", "content": "<summary>Context so far</summary>", "token_delta": -500, "has_compaction": False, "has_tool_clearing": False},
    ]
    result = extract(transcript)
    assert result["compaction_events"] == 1


def test_empty_transcript():
    result = extract([])
    assert result["compaction_events"] == 0
    assert result["tool_clearing_events"] == 0
    assert result["context_velocity_avg_tokens_per_turn"] == 0.0
    assert result["token_velocity_series"] == []
    assert result["growth_class"] == "saw_tooth"


def test_all_five_keys_present():
    result = extract(STABLE_TRANSCRIPT)
    expected_keys = {
        "compaction_events",
        "tool_clearing_events",
        "context_velocity_avg_tokens_per_turn",
        "token_velocity_series",
        "growth_class",
    }
    assert expected_keys == set(result.keys())
