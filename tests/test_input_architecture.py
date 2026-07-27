"""Tests for input-architecture analyzer (Epic #569, Story #574)."""

from telemetry.input_architecture import analyze

BOUNDED_PROMPT = """
Implement telemetry/schema.py with SessionTelemetry.
Write tests and verify: pytest -q tests/test_telemetry_schema.py.
If it fails, check the import. Acceptance: all tests green.
Closes #570
"""

FLAT_BACKLOG = [
    {"title": "Add schema", "body": "Create schema.py", "files_modified": 2, "issues_closed_by_pr": 1},
    {"title": "Add store", "body": "Create store.py", "files_modified": 1, "issues_closed_by_pr": 1},
    {"title": "Add tests", "body": "Write test_schema.py", "files_modified": 1, "issues_closed_by_pr": 1},
]

SEQUENTIAL_BACKLOG = [
    {"title": "Schema first", "body": "Create schema.py", "files_modified": 2, "issues_closed_by_pr": 1},
    {
        "title": "Store after schema",
        "body": "Create store.py, depends on schema being done first",
        "files_modified": 1,
        "issues_closed_by_pr": 1,
    },
]


def test_prompt_type_declarative():
    result = analyze(BOUNDED_PROMPT, FLAT_BACKLOG)
    assert result["prompt_type"] == "declarative_bounded"


def test_dependency_structure_flat():
    result = analyze(BOUNDED_PROMPT, FLAT_BACKLOG)
    assert result["dependency_structure"] == "flat_parallel"


def test_dependency_structure_sequential():
    result = analyze(BOUNDED_PROMPT, SEQUENTIAL_BACKLOG)
    assert result["dependency_structure"] == "deep_sequential"


def test_atomic_density_index_value():
    result = analyze(BOUNDED_PROMPT, FLAT_BACKLOG)
    # avg files_modified = (2+1+1)/3 = 4/3; issues_closed_total = 3 → density = (4/3)/3
    expected = (4 / 3) / 3
    assert result["atomic_density_index"] is not None
    assert abs(result["atomic_density_index"] - expected) < 1e-9


def test_atomic_density_zero_issues_closed():
    zero_backlog = [
        {"title": "Issue with zero closed", "body": "something", "files_modified": 3, "issues_closed_by_pr": 0},
    ]
    result = analyze(BOUNDED_PROMPT, zero_backlog)
    # Must not raise ZeroDivisionError; must return None or a finite value
    density = result["atomic_density_index"]
    assert density is None or (isinstance(density, float) and density != float("inf"))


def test_error_handling_instructions_detected():
    result = analyze(BOUNDED_PROMPT, FLAT_BACKLOG)
    assert result["contains_error_handling_instructions"] is True


def test_verification_commands_detected():
    result = analyze(BOUNDED_PROMPT, FLAT_BACKLOG)
    assert result["has_explicit_verification_commands"] is True


def test_avg_issue_word_count():
    result = analyze(BOUNDED_PROMPT, FLAT_BACKLOG)
    # "Create schema.py" = 2, "Create store.py" = 2, "Write test_schema.py" = 2 → avg = 2.0
    assert result["avg_issue_word_count"] == 2.0


def test_empty_backlog():
    result = analyze(BOUNDED_PROMPT, [])
    assert result["atomic_density_index"] is None
    assert result["avg_issue_word_count"] == 0.0


def test_open_ended_prompt():
    open_prompt = "Explore this codebase and think about what could be improved. What do you think?"
    result = analyze(open_prompt, FLAT_BACKLOG)
    assert result["prompt_type"] == "open_ended"
