"""Guard: a recorded objective is read through ``makerbench.arena_checks`` only (#1011).

Wall thickness was removed from arena scoring on 2026-10-16, but old records still carry it and
a stored ``objective_pass_rate`` may still count it. Every consumer of a trial's recorded
objective must go through ``arena_checks`` (``trial_objective_rate`` / ``recorded_objective_rate``
/ ``retained_checks`` / ``current_objective``) or, in Studio JavaScript, ``lib/objectiveChecks.js``.
This test fails on any other raw read of ``objective_pass_rate``, ``sub_scores`` or ``min_wall``
in ``makerbench/``, ``scripts/`` and ``site/*.py`` (AST) or the Studio/site JavaScript (text),
unless the (file, function) pair is allowlisted below with a reason.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEYS = {"objective_pass_rate", "sub_scores", "min_wall"}

_WRITER = "writes the objective of a new run (current gate), nothing is read back"
_AGGREGATE = "reads published/aggregate scoreline rows, already rescored, not a trial's recorded objective"
_FROZEN = "builds or audits the frozen historical showcase data, which predates the removal"
_ACCESSOR = "the shared accessor / sanitizer itself"
_RETIRED_AWARE = "filters RETIRED_CHECKS explicitly and rates through objective_rate"

PY_ALLOWLIST: dict[str, dict[str, str]] = {
    "makerbench/arena_checks.py": {
        "current_objective": _ACCESSOR, "recorded_objective_rate": _ACCESSOR, "retained_checks": _ACCESSOR},
    "makerbench/arena_studio/demo.py": {"_without_retired": "sanitizes the packaged showcase rows with the accessor"},
    "makerbench/arena_studio/service.py": {
        "_trial_result_and_sub_scores": "reads only the watertight flag for the compiled/manifold counts, no rate"},
    "makerbench/cli_arena.py": {"arena_run": _AGGREGATE},
    "makerbench/code_cad_agreement.py": {
        "ScorelineRow.from_mapping": _AGGREGATE, "render_markdown_summary": _AGGREGATE},
    "makerbench/code_cad_arena_report.py": {
        "_dual_scoreline_table": _AGGREGATE, "_objective_bars": _AGGREGATE},
    "makerbench/code_cad_arena_runner.py": {
        "_trial_failed_checks": _RETIRED_AWARE, "build_agreement_rows": _AGGREGATE,
        "collect_objective_scoreline": _AGGREGATE},
    "makerbench/code_cad_objective.py": {"_normalize_gate_result": _WRITER},
    "makerbench/code_cad_tier_comparison.py": {
        "build_tier_comparison": _AGGREGATE, "render_markdown_comparison": _AGGREGATE},
    "makerbench/code_cad_time_budget.py": {"BudgetObservation.from_mapping": _AGGREGATE},
    "scripts/build_frontier_replay.py": {"_row": _RETIRED_AWARE},
    "scripts/build_openrouter_replay.py": {"build": _RETIRED_AWARE},
    "scripts/build_studio_demo_data.py": {
        "backend_story": _FROZEN, "build": _FROZEN, "build.score": _FROZEN, "validate_aggregate_seeds": _FROZEN},
    "scripts/build_studio_demo_space.py": {"audit_snapshot": _FROZEN},
    "scripts/generate_render_gallery.py": {
        "_rate_text": "formats a card's already-rescored rate",
        "_failing": "reads a card's sub_scores, built from retained_checks",
        "render_html": "reads a card's sub_scores, built from retained_checks"},
    "scripts/import_studio_repeat_renders.py": {"prepare": _FROZEN},
    "scripts/recompute_no_min_wall.py": {"recompute": "the one-off recompute of the two replay bundles"},
    "scripts/regrade_openrouter_baseline.py": {"main": "records the fresh regrade run's own new-gate objective"},
    "site/build_data.py": {
        "_arena_page_round": _AGGREGATE, "_arena_run_entry": _AGGREGATE, "_prerender_matchups_html": _AGGREGATE,
        "_prerender_sections": _AGGREGATE, "build_matchups_page": "reads the recomputed replay bundles' cells"},
}

JS_ALLOWLIST: dict[str, dict[str, str]] = {
    "makerbench/arena_studio/static/app/lib/objectiveChecks.js": {"*": "the shared helper itself"},
    # Aggregate/scoreline rows from the report and agreement APIs, not a recorded trial objective.
    "makerbench/arena_studio/static/app/lib/agreement-stats.js": {"*": "agreement scoreline rows"},
    "makerbench/arena_studio/static/app/screens/agreement.js": {"*": "agreement scoreline rows"},
    "site/assets/app.js": {"*": "published arena/leaderboard aggregate rows (frozen rounds or recomputed bundles)"},
}


def _py_files():
    for top in ("makerbench", "scripts", "site"):
        for path in sorted((ROOT / top).rglob("*.py")):
            yield path


def _hits(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack = []

        def _scope(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

        def _hit(self, key):
            found.add((".".join(self.stack) or "<module>", key))

        def visit_Subscript(self, node):
            if isinstance(node.slice, ast.Constant) and node.slice.value in KEYS:
                self._hit(node.slice.value)
            self.generic_visit(node)

        def visit_Call(self, node):
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr in {"get", "pop", "setdefault"} and node.args
                    and isinstance(node.args[0], ast.Constant) and node.args[0].value in KEYS):
                self._hit(node.args[0].value)
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


def test_python_reads_of_a_recorded_objective_go_through_arena_checks():
    offenders = []
    for path in _py_files():
        rel = path.relative_to(ROOT).as_posix()
        allowed = PY_ALLOWLIST.get(rel, {})
        for function, key in sorted(_hits(path)):
            if function not in allowed:
                offenders.append(f"{rel}: {function} reads {key!r}")
    assert not offenders, (
        "raw read of objective_pass_rate / sub_scores / min_wall; use makerbench.arena_checks "
        "(trial_objective_rate, recorded_objective_rate, retained_checks, current_objective) or add a "
        "justified PY_ALLOWLIST entry:\n" + "\n".join(offenders))


def test_python_allowlist_has_no_stale_entries():
    present = {(path.relative_to(ROOT).as_posix(), function) for path in _py_files() for function, _ in _hits(path)}
    stale = [f"{rel}: {function}" for rel, entries in PY_ALLOWLIST.items() for function in entries
             if (rel, function) not in present]
    assert not stale, "stale PY_ALLOWLIST entries:\n" + "\n".join(stale)


def _js_files():
    for path in sorted((ROOT / "makerbench/arena_studio/static/app").rglob("*.js")):
        yield path
    yield ROOT / "site/assets/app.js"


def test_javascript_reads_go_through_objective_checks():
    pattern = re.compile(r"objective_pass_rate|sub_scores|min_wall")
    offenders = []
    for path in _js_files():
        rel = path.relative_to(ROOT).as_posix()
        if "*" in JS_ALLOWLIST.get(rel, {}):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line) and not line.lstrip().startswith("//") and "retainedChecks(" not in line:
                offenders.append(f"{rel}:{number}: {line.strip()[:120]}")
    assert not offenders, (
        "raw read in the Studio JavaScript; use lib/objectiveChecks.js (retainedChecks / objectiveRate):\n"
        + "\n".join(offenders))


@pytest.mark.parametrize("line", [
    'rate = objective.get("objective_pass_rate")',
    'checks = result["objective"]["sub_scores"]',
    'x = row.get("min_wall")',
])
def test_the_guard_detects_a_planted_raw_read(line, tmp_path):
    path = tmp_path / "planted.py"
    path.write_text(f"def read(objective, result, row):\n    {line}\n", encoding="utf-8")
    assert _hits(path), line
