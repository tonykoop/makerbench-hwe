"""Tests for tool utility & fan-out metrics (Epic #569, Story #573)."""

from telemetry.tool_utility import compute

# Fixture: 3 turns
# Turn "t1": Bash + Read (batched, 2 calls)
# Turn "t2": Edit (single call)
# Turn "t3": Bash + Bash + Read (batched, 3 calls), one Bash fails
FIXTURE_LOG = [
    {"tool": "Bash", "turn_id": "t1", "failed": False},
    {"tool": "Read", "turn_id": "t1", "failed": False},
    {"tool": "Edit", "turn_id": "t2", "failed": False},
    {"tool": "Bash", "turn_id": "t3", "failed": False},
    {"tool": "Bash", "turn_id": "t3", "failed": True},
    {"tool": "Read", "turn_id": "t3", "failed": False},
]


def test_tool_call_distribution():
    result = compute(FIXTURE_LOG)
    dist = result["tool_call_distribution"]
    assert dist["Bash"] == 3
    assert dist["Read"] == 2
    assert dist["Edit"] == 1


def test_bash_invocations():
    result = compute(FIXTURE_LOG)
    assert result["bash_invocations"] == 3


def test_model_round_trips():
    result = compute(FIXTURE_LOG)
    assert result["model_round_trips"] == 3  # t1, t2, t3


def test_programmatic_tool_batches():
    result = compute(FIXTURE_LOG)
    # t1 (2 calls) and t3 (3 calls) are batches; t2 (1 call) is not
    assert result["programmatic_tool_batches"] == 2


def test_batch_ratio():
    result = compute(FIXTURE_LOG)
    # 2 batches / 3 round-trips
    assert abs(result["batch_ratio"] - 2 / 3) < 1e-9


def test_failed_tool_calls():
    result = compute(FIXTURE_LOG)
    assert result["failed_tool_calls"] == 1


def test_tool_failure_rate():
    result = compute(FIXTURE_LOG)
    # 1 failed / 6 total
    assert abs(result["tool_failure_rate"] - 1 / 6) < 1e-9


def test_divide_by_zero_empty_log():
    result = compute([])
    assert result["batch_ratio"] == 0.0
    assert result["tool_failure_rate"] == 0.0
    assert result["model_round_trips"] == 0
    assert result["programmatic_tool_batches"] == 0
    assert result["bash_invocations"] == 0
    assert result["tool_call_distribution"] == {}
