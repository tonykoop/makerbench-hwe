"""Host-path redaction: the shared contract between runner and audit (#684)."""

from __future__ import annotations

import pytest

from makerbench.redaction import (
    REDACTION_TOKEN,
    find_host_paths,
    redact_host_paths,
    run_relative_path,
)


LEAKS = [
    "/tmp/tmpr72t7bwb.scad",
    "/tmp/claude-1000/-mnt-c-Users-Tony-Documents-GitHub/abc/scratchpad/runs/x.json",
    "/home/tony/bench-wt/runs/m/t/perceive/iter_1/section_x.png",
    r"C:\Users\Tony\AppData\Local\Temp\tmp8axycz3t.scad",
    "C:/Users/Tony/Documents/GitHub/x.scad",
    "/mnt/c/Users/Tony/Documents/GitHub/instruments/x.jpg",
    "/Users/tony/Library/Caches/x.scad",
    "/private/var/folders/ab/cd/T/tmpx.scad",
]

# Values that legitimately appear in published JSON and must survive untouched.
SAFE = [
    "results/model/artifacts/render.png",
    "runs/m/t__seed0__perception__abc/perceive/iter_1/section_x.png",
    "site/data/leaderboard.json",
    "private/oracles/vented_plate/oracle.scad",
    "https://github.com/tonykoop/makerbench-oracles",
    "https://api.github.com/users/tonykoop",
    "official-heldout-verified",
    "temp/relative/ok.txt",
    "tmpfile.scad",
    "",
]


@pytest.mark.parametrize("value", LEAKS)
def test_leaks_are_detected_and_redacted(value):
    assert find_host_paths(value), value
    redacted = redact_host_paths(value)
    assert REDACTION_TOKEN in redacted
    assert "tony" not in redacted.lower()


@pytest.mark.parametrize("value", SAFE)
def test_safe_values_are_untouched(value):
    assert find_host_paths(value) == []
    assert redact_host_paths(value) == value
    assert run_relative_path(value) == value


def test_openscad_stderr_is_scrubbed_of_both_absolute_and_relative_forms():
    """OpenSCAD prints the temp path twice — a relative climb and an absolute."""
    message = (
        "iso: Render failed: ERROR: Parser error: syntax error in file "
        "../../../../../../../tmp/tmpr72t7bwb.scad, line 1\n"
        "Can't parse file '/tmp/tmpr72t7bwb.scad'!"
    )

    redacted = redact_host_paths(message)

    assert "tmpr72t7bwb" not in redacted
    assert ".." not in redacted
    # The diagnostic prose survives; only the paths go.
    assert redacted.startswith("iso: Render failed: ERROR: Parser error:")
    assert "line 1" in redacted


def test_run_relative_path_keeps_the_reproducible_tail():
    scoped = run_relative_path(
        "/home/tony/bench-wt/runs/claude-code-sonnet-5/"
        "enclosure_two_body__seed0__perception__uo51jj3i/perceive/iter_1/section_x.png"
    )

    assert scoped == (
        "runs/claude-code-sonnet-5/enclosure_two_body__seed0__perception__uo51jj3i/"
        "perceive/iter_1/section_x.png"
    )
    assert find_host_paths(scoped) == []


def test_run_relative_path_falls_back_to_the_token_without_a_run_root():
    """No run segment means no safe tail to keep, so the whole value goes."""
    assert run_relative_path("/tmp/tmpabc/loose.png") == REDACTION_TOKEN


def test_run_relative_path_normalises_windows_separators():
    assert run_relative_path(r"C:\Users\Tony\runs\m\t\iter_1\x.png") == (
        "runs/m/t/iter_1/x.png"
    )


def test_redaction_is_idempotent():
    once = redact_host_paths("see /home/tony/x.scad")
    assert redact_host_paths(once) == once


@pytest.mark.parametrize("value", [None, 42, [], {}])
def test_non_string_values_pass_through(value):
    assert redact_host_paths(value) is value
    assert run_relative_path(value) is value
    assert find_host_paths(value) == []


# --- grade.levels[].detail is a published field too (#684 follow-up) ---------
def test_compile_error_detail_is_redacted_before_publication(tmp_path):
    """A failing compile publishes OpenSCAD stderr into `grade.levels[].detail`.

    The perception funnel was redacted in the first pass; this path was not, so a
    fresh run still recorded the temp file the parser choked on. Caught while
    redacting PR #683, whose bundles carry three of these.
    """
    from makerbench.evaluator import evaluate
    from makerbench.schema import Attempt, TaskSpec

    spec = TaskSpec(task_id="t", seed=0, params={}, brief="")
    attempt = Attempt(
        task_id="t", seed=0, track="blind",
        source="this is not valid openscad ((((",
    )

    result = evaluate(attempt, spec, lambda *a, **k: ([], {}),
                      work_dir=str(tmp_path))

    structural = result.levels[0]
    assert structural.passed is False
    detail = structural.detail or ""
    # The diagnosis survives; the host path does not.
    assert find_host_paths(detail) == [], detail
    if detail:
        assert "/tmp/" not in detail and "\\Users\\" not in detail


def test_grader_crash_detail_is_redacted(tmp_path):
    """`Grader raised: {exc}` can carry a path out of any grader traceback."""
    from makerbench.evaluator import evaluate
    from makerbench.schema import Attempt, TaskSpec

    def exploding_grader(*_args, **_kwargs):
        raise RuntimeError("failed reading /home/tony/bench-wt/runs/x/input.scad")

    spec = TaskSpec(task_id="t", seed=0, params={}, brief="")
    attempt = Attempt(task_id="t", seed=0, track="blind", source="cube([1,1,1]);")

    result = evaluate(attempt, spec, exploding_grader, work_dir=str(tmp_path))

    details = " ".join(level.detail or "" for level in result.levels)
    assert "tony" not in details.lower(), details
    assert find_host_paths(details) == []
