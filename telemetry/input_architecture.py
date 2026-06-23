"""Input-architecture analyzer (Epic #569, Story #574).

Analyzes the structural shape of a session's input: prompt taxonomy and
backlog atomic-density / dependency-structure metrics.

atomic_density_index = files_modified_per_PR / issues_closed.
When issues_closed == 0, returns None (never raises ZeroDivisionError).
"""

from __future__ import annotations

from typing import Any


# Keywords that signal explicit error-handling instructions in a prompt.
_ERROR_HANDLING_KEYWORDS = [
    "if it fails", "on failure", "error handling", "catch", "fallback",
    "retry", "guard", "handle the case", "if missing", "zero issues",
    "divide-by-zero", "guard div",
]

# Keywords that signal explicit verification commands.
_VERIFICATION_KEYWORDS = [
    "pytest", "verify:", "assert", "run the test", "confirm", "check",
    "validate", "green", "pass", "fails raises",
]


def analyze(prompt: str, backlog: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze prompt + backlog and return input-architecture signals.

    Args:
        prompt: The session's system/task prompt text.
        backlog: List of issue dicts, each with at least a ``body`` or ``title``
                 string field and optional ``files_modified`` (int) and
                 ``issues_closed_by_pr`` (int).

    Returns:
        Dict with keys: prompt_type, contains_error_handling_instructions,
        atomic_density_index, dependency_structure, has_explicit_verification_commands,
        avg_issue_word_count.
    """
    prompt_lower = prompt.lower()

    # Prompt type classification
    is_declarative = _is_declarative(prompt_lower)
    prompt_type = "declarative_bounded" if is_declarative else "open_ended"

    contains_error_handling = any(kw in prompt_lower for kw in _ERROR_HANDLING_KEYWORDS)
    has_verification = any(kw in prompt_lower for kw in _VERIFICATION_KEYWORDS)

    # Dependency structure: flat_parallel if issues appear independent (no "after", "depends on",
    # "blocked by" in bodies); deep_sequential otherwise.
    dependency_structure = _classify_dependency(backlog)

    # Atomic density: files_modified_per_PR / issues_closed
    # Guard: if issues_closed == 0, return None
    files_modified_per_pr = _avg_files_modified(backlog)
    issues_closed_total = sum(
        int(issue.get("issues_closed_by_pr", 1)) for issue in backlog
    )
    if issues_closed_total == 0:
        atomic_density_index = None
    else:
        atomic_density_index = files_modified_per_pr / issues_closed_total

    # Average word count across backlog issue bodies/titles
    avg_issue_word_count = _avg_word_count(backlog)

    return {
        "prompt_type": prompt_type,
        "contains_error_handling_instructions": contains_error_handling,
        "atomic_density_index": atomic_density_index,
        "dependency_structure": dependency_structure,
        "has_explicit_verification_commands": has_verification,
        "avg_issue_word_count": avg_issue_word_count,
    }


def _is_declarative(prompt_lower: str) -> bool:
    declarative_signals = [
        "implement", "create", "add", "compute", "return", "write tests",
        "closes #", "story points", "acceptance", "scope", "verify:",
    ]
    open_ended_signals = ["explore", "think about", "consider", "what do you think", "brainstorm"]
    declarative_score = sum(1 for s in declarative_signals if s in prompt_lower)
    open_score = sum(1 for s in open_ended_signals if s in prompt_lower)
    return declarative_score > open_score


def _classify_dependency(backlog: list[dict[str, Any]]) -> str:
    sequential_keywords = ["after", "depends on", "blocked by", "once", "requires", "prerequisite"]
    for issue in backlog:
        text = (issue.get("body", "") + " " + issue.get("title", "")).lower()
        if any(kw in text for kw in sequential_keywords):
            return "deep_sequential"
    return "flat_parallel"


def _avg_files_modified(backlog: list[dict[str, Any]]) -> float:
    if not backlog:
        return 0.0
    total = sum(int(issue.get("files_modified", 1)) for issue in backlog)
    return total / len(backlog)


def _avg_word_count(backlog: list[dict[str, Any]]) -> float:
    if not backlog:
        return 0.0
    total_words = 0
    for issue in backlog:
        text = issue.get("body", issue.get("title", ""))
        total_words += len(str(text).split())
    return total_words / len(backlog)
