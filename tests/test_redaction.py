"""Host-path redaction: the shared contract between runner and audit (#684)."""

from __future__ import annotations

import json

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


def test_agent_error_detail_is_redacted(tmp_path, monkeypatch):
    """An agent crash publishes its exception text into `grade.levels[].detail`.

    Found by the independent reviewer of the first pass at this fix: redacting
    the evaluator's three sites left `runner._error_result` untouched, and an
    agent traceback carrying a path reached the published grade the same way.
    Redaction lives inside `_error_result` so any future caller is covered too.
    """
    from makerbench import runner

    result = runner._error_result(
        "vented_plate", 0, "blind",
        "agent raised: FileNotFoundError: /home/tony/bench-wt/runs/x/input.scad",
    )

    detail = result.grade.levels[0].detail or ""
    assert find_host_paths(detail) == [], detail
    assert "tony" not in detail.lower()
    # The failure is still legible and still scored as a failure.
    assert "agent raised" in detail
    assert result.grade.levels[0].passed is False
    assert result.grade.notes == "agent_error"


def test_vector_parse_rejection_detail_is_redacted(monkeypatch):
    """The path three rounds of producer-patching missed.

    A parser exception is stored in a `VectorRejection` and only concatenated
    into `grade.levels[].detail` later, so it is invisible to a grep for
    `detail=...{exc}`. Reported by the independent reviewer of the second pass.
    """
    from makerbench import vector as vec
    from makerbench.schema import Attempt, TaskSpec
    from makerbench.vector_eval import evaluate_vector

    def exploding_parse(_source):
        raise RuntimeError("parser scratch: /home/tony/private/vector.svg")

    monkeypatch.setattr(vec, "parse_vector", exploding_parse, raising=True)
    spec = TaskSpec(task_id="t", seed=0, params={}, brief="")
    attempt = Attempt(task_id="t", seed=0, track="blind", source="<svg/>")

    try:
        result = evaluate_vector(attempt, spec, lambda *a, **k: ([], {}))
    except RuntimeError:
        pytest.skip("parse_vector raising is not caught on this path")

    details = " ".join(level.detail or "" for level in result.levels)
    assert find_host_paths(details) == [], details


def test_level_result_redacts_detail_from_any_producer():
    """The invariant is enforced on the model, so no construction path can miss it."""
    from makerbench.schema import FailureLevel, LevelResult

    for raw in [
        "malformed: parser scratch: /home/tony/private/vector.svg",
        r"OpenSCAD exited 1.\nSTDERR: Can't parse 'C:\Users\Tony\Temp\x.scad'!",
        "agent raised: FileNotFoundError: /mnt/c/Users/Tony/Documents/x",
    ]:
        level = LevelResult(level=FailureLevel.STRUCTURAL, passed=False, detail=raw)
        assert find_host_paths(level.detail) == [], level.detail
        assert "tony" not in level.detail.lower()

    # A clean detail is untouched — the validator must not mangle normal text.
    clean = "OpenSCAD compiled to non-empty mesh."
    assert LevelResult(level=FailureLevel.STRUCTURAL, passed=True, detail=clean).detail == clean


# --- the publication boundary, not just construction (#684, third review) ----
def _published_detail(level):
    """What actually lands in a committed bundle."""
    return level.model_dump(mode="json")["detail"]


def test_model_copy_update_cannot_publish_a_host_path():
    """`model_copy(update=...)` does not re-run field validators."""
    from makerbench.schema import FailureLevel, LevelResult

    clean = LevelResult(level=FailureLevel.STRUCTURAL, passed=False, detail="clean")
    leaked = clean.model_copy(update={"detail": "/home/tony/private/copied.scad"})

    assert find_host_paths(_published_detail(leaked)) == []


def test_attribute_assignment_cannot_publish_a_host_path():
    """Plain assignment does not re-run field validators without validate_assignment."""
    from makerbench.schema import FailureLevel, LevelResult

    level = LevelResult(level=FailureLevel.GEOMETRIC, passed=False, detail="clean")
    level.detail = "/mnt/c/Users/Tony/private/assigned.scad"

    assert find_host_paths(_published_detail(level)) == []


def test_escapes_survive_nesting_into_a_full_run_bundle():
    """The end-to-end shape the reviewer demonstrated: nested, then dumped."""
    from makerbench.schema import FailureLevel, GradeResult, LevelResult

    clean = LevelResult(level=FailureLevel.STRUCTURAL, passed=False, detail="clean")
    copied = clean.model_copy(update={"detail": "/home/tony/private/copied.scad"})
    assigned = LevelResult(level=FailureLevel.GEOMETRIC, passed=False, detail="clean")
    assigned.detail = "/mnt/c/Users/Tony/private/assigned.scad"

    # GradeResult accepts already-built LevelResults without revalidating them.
    grade = GradeResult(task_id="t", track="blind", levels=[copied, assigned])
    payload = json.dumps(grade.model_dump(mode="json"))

    assert find_host_paths(payload) == [], payload
    assert "tony" not in payload.lower()


def test_grade_notes_is_protected_too():
    """Latent today — the only writer is static — but the same published class."""
    from makerbench.schema import GradeResult

    grade = GradeResult(task_id="t", track="blind", levels=[])
    grade.notes = "crashed reading /home/tony/private/x.scad"

    assert find_host_paths(grade.model_dump(mode="json")["notes"]) == []


def test_serializer_leaves_clean_text_byte_identical():
    """Redaction must not mangle an ordinary detail or note."""
    from makerbench.schema import FailureLevel, GradeResult, LevelResult

    detail = "OpenSCAD compiled to non-empty mesh; bbox=120.00x55.00, cutouts=3/3"
    level = LevelResult(level=FailureLevel.STRUCTURAL, passed=True, detail=detail)
    assert _published_detail(level) == detail

    grade = GradeResult(task_id="t", track="blind", levels=[level], notes="agent_error")
    assert grade.model_dump(mode="json")["notes"] == "agent_error"
