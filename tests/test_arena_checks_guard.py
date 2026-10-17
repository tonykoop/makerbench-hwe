"""Guard: a recorded objective is read through ``makerbench.arena_checks`` only (#1011).

Wall thickness was removed from arena scoring on 2026-10-16, but old records still carry it and
a stored ``objective_pass_rate`` may still count it. Every consumer of a trial's recorded
objective must go through ``arena_checks`` (``trial_objective_rate`` / ``recorded_objective_rate``
/ ``retained_checks`` / ``current_objective``) or, in the Studio JavaScript, ``lib/objectiveChecks.js``.

The check is deliberately conservative. Python: the strings ``objective_pass_rate``,
``sub_scores`` and ``min_wall`` (and attribute reads of them) may appear only on the exact
(function, source line) pairs allowlisted below, each file with the reason it is approved; dict
literal keys (writers) and docstrings are ignored. Because every other occurrence of the string is
flagged, aliases (``read = obj.get; read(KEY)``), constant variable keys and ``getattr`` forms are
caught too. JavaScript: the same names may appear only on the exact allowlisted lines. Whole-function and
whole-file exemptions are not allowed (except the accessor files themselves).

Not covered here (tracked in the paused AST track-isolation guard story, #1085): reads whose key is
built at runtime, dynamic ``getattr`` names and cross-module alias tracking.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEYS = {"objective_pass_rate", "sub_scores", "min_wall"}

PY_ALLOWLIST: dict[str, tuple[str, frozenset]] = {
    'makerbench/adapters/__init__.py': (
        'DFM task metric (CadGenBench step adapter), not the arena objective',
        frozenset({
            ('CadGenBenchStepAdapter._score_metrics', 'name="min_wall", ok=ok, expected=f">={self.min_wall_mm} mm",'),
        }),
    ),
    'makerbench/arena_checks.py': (
        'the shared accessor / sanitizer itself',
        frozenset({
            ('<module>', 'RETIRED_CHECKS = frozenset({"min_wall"})'),
            ('current_objective', 'for key in ("sub_scores", "checks", "gates"):'),
            ('current_objective', 'out["objective_pass_rate"] = rate'),
            ('recorded_objective_rate', 'rate = objective.get("objective_pass_rate")'),
            ('retained_checks', 'sub_scores = (objective or {}).get("sub_scores")'),
        }),
    ),
    'makerbench/arena_studio/demo.py': (
        'sanitizes the packaged showcase rows with the accessor',
        frozenset({
            ('_without_retired', 'node["objective_pass_rate"] = rate'),
            ('_without_retired', 'rate = recorded_objective_rate({"sub_scores": node["gates"], "objective_pass_rate": node.get("objective_pass_rate")})'),
        }),
    ),
    'makerbench/arena_studio/service.py': (
        'reads only the watertight flag for the compiled/manifold counts, no rate',
        frozenset({
            ('_trial_result_and_sub_scores', 'sub_scores = objective.get("sub_scores") if isinstance(objective, Mapping) else None'),
        }),
    ),
    'makerbench/cli_arena.py': (
        'aggregate scoreline row, already rescored',
        frozenset({
            ('arena_run', 'table.add_row(row["entrant"], f"{row[\'objective_pass_rate\']:.3f}", str(row["n_objective_trials"]))'),
        }),
    ),
    'makerbench/code_cad_agreement.py': (
        'aggregate scoreline rows, already rescored',
        frozenset({
            ('ScorelineRow.from_mapping', 'objective_pass_rate=_optional_rate(value.get("objective_pass_rate")),'),
            ('ScorelineRow.validate', 'if self.objective_pass_rate is not None and not 0.0 <= self.objective_pass_rate <= 1.0:'),
            ('_optional_rate', 'rate = _optional_float(value, "objective_pass_rate")'),
            ('_side_by_side_rows', '"objective_pass_rate": _round(row.objective_pass_rate, 3),'),
            ('build_agreement_summary', '"objective": _average_ranks(parsed, "objective_pass_rate"),'),
            ('render_markdown_summary', 'objective=_fmt(record.get("objective_pass_rate")),'),
        }),
    ),
    'makerbench/code_cad_arena_report.py': (
        'aggregate scoreline rows, already rescored',
        frozenset({
            ('_dual_scoreline_table', 'f\'<td class="num">{_fmt_rate(record.get("objective_pass_rate"))}</td>\''),
            ('_objective_bars', 'rate = float(row["objective_pass_rate"])'),
        }),
    ),
    'makerbench/code_cad_arena_runner.py': (
        'aggregate scoreline rows (sort/agreement) and the retired-aware failed-check reader',
        frozenset({
            ('_trial_failed_checks', 'for name, score in (objective.get("sub_scores") or {}).items():'),
            ('build_agreement_rows', '"objective_pass_rate": objective_row.get("objective_pass_rate"),'),
            ('collect_objective_scoreline', 'rows.sort(key=lambda row: (-row["objective_pass_rate"], row["entrant"], row["backend"]))'),
        }),
    ),
    'makerbench/code_cad_objective.py': (
        'writes the objective of a new run (current gate)',
        frozenset({
            ('_normalize_gate_result', 'rate = result.get("objective_pass_rate", result.get("pass_rate"))'),
            ('_normalize_gate_result', 'sub_scores = result.get("sub_scores", {})'),
        }),
    ),
    'makerbench/code_cad_tier_comparison.py': (
        'aggregate scoreline rows, already rescored',
        frozenset({
            ('build_tier_comparison', '"objective_pass_rate": row["objective_pass_rate"] if row else None,'),
            ('build_tier_comparison', 'by_tier[tier]["objective_pass_rate"]'),
            ('build_tier_comparison', 'if by_tier[tier]["objective_pass_rate"] is not None'),
            ('render_markdown_comparison', 'rate = (record["tiers"].get(tier) or {}).get("objective_pass_rate")'),
        }),
    ),
    'makerbench/code_cad_time_budget.py': (
        'aggregate scoreline rows, already rescored',
        frozenset({
            ('<module>', 'METRICS = ("subjective_elo", "objective_pass_rate")'),
            ('BudgetObservation.from_mapping', 'objective_pass_rate=_optional_rate(value.get("objective_pass_rate")),'),
            ('BudgetObservation.validate', 'if self.objective_pass_rate is not None and not 0.0 <= self.objective_pass_rate <= 1.0:'),
            ('_optional_rate', 'rate = _optional_float(value, "objective_pass_rate")'),
        }),
    ),
    'scripts/build_frontier_replay.py': (
        'replay builder: filters RETIRED_CHECKS and rates through objective_rate',
        frozenset({
            ('_row', 'failed_checks={k: 1 for k, v in objective["sub_scores"].items()'),
            ('_row', 'rate = round(objective_rate(objective["sub_scores"]), 6)'),
        }),
    ),
    'scripts/build_hf_dataset.py': (
        'DFM task parameter name, not the arena objective',
        frozenset({
            ('<module>', '"reverse_engineer_bracket": ("obs_w", "obs_d", "obs_t", "hole_dia", "obs_tol", "min_wall"),'),
        }),
    ),
    'scripts/build_openrouter_replay.py': (
        'replay builder: filters RETIRED_CHECKS and rates through objective_rate',
        frozenset({
            ('build', 'sub_scores = obj.get("sub_scores") or {}'),
            ('build', 'sub_scores = replayed.get("sub_scores") or {}'),
        }),
    ),
    'scripts/build_studio_demo_data.py': (
        'builds the frozen historical showcase data (predates the removal)',
        frozenset({
            ('<module>', 'GATES = {"renders", "watertight", "nonzero_volume", "fits_envelope", "body_count", "min_wall"}'),
            ('backend_story', 'or round(after["objective_pass_rate"], 3) != values[2]):'),
            ('backend_story', 'or round(before["objective_pass_rate"], 3) != values[1]'),
            ('build', '"gates": trial["gates"], "objective_pass_rate": trial["objective_pass_rate"],'),
            ('build', '"gates": {k: v for k, v in d.get("sub_scores", {}).items() if k in GATES},'),
            ('build', '"objective_pass_rate": d["objective_pass_rate"] if d["status"] == "scored" else None,'),
            ('build', '"objective_pass_rate": d["objective_pass_rate"], "failed_checks": []}]'),
            ('build', '"objective_pass_rate": row["objective_pass_rate"],'),
            ('build', '"recorded_pipeline_rate": d["objective_pass_rate"],'),
            ('build', 'if abs(sum(trial["objective_pass_rate"] for trial in trials) / 3 - row["objective_pass_rate"]) > 1e-6:'),
            ('build', 'if abs(trial["objective_pass_rate"] - (sum(decided) / len(decided) if decided else 0.0)) > 1e-6:'),
            ('build', 'if any(set(design.get("sub_scores", {})) != GATES for design in gallery["designs"] if design["status"] == "scored"):'),
            ('build.score', '"objective_pass_rate": row["objective_pass_rate"],'),
            ('build.score', '"objective_pass_rate": row["objective_pass_rate"], "failed_checks": failures}]'),
            ('build.score', 'if abs(row["objective_pass_rate"] - (6 - len(failed)) / 6) < 1e-6 else {})'),
            ('validate_aggregate_seeds', 'if abs(sum(trial["objective_pass_rate"] for trial in trials) / len(seeds) - row["objective_pass_rate"]) > 1e-6:'),
        }),
    ),
    'scripts/build_studio_demo_space.py': (
        'audits the frozen historical showcase data (predates the removal)',
        frozenset({
            ('audit_snapshot', 'if abs(sum(trial["objective_pass_rate"] for trial in trials) / len(seeds) - row["objective_pass_rate"]) > 1e-6:'),
            ('audit_snapshot', 'if set(row) - {"label", "entrant", "backend", "objective_pass_rate", "n_objective_trials",'),
            ('audit_snapshot', 'if set(trial) != {"seed", "image", "gates", "objective_pass_rate", "failed_checks"}:'),
            ('audit_snapshot', 'if set(trial["gates"]) - {"renders", "watertight", "nonzero_volume", "fits_envelope", "body_count", "min_wall"}:'),
        }),
    ),
    'scripts/generate_render_gallery.py': (
        "reads a gallery card's already-retained sub_scores / rescored rate",
        frozenset({
            ('_failing', 'bad = [name for name, value in card["sub_scores"].items() if not isinstance(value, str) and value < 1.0]'),
            ('_rate_text', 'return f"Objective pass rate {card[\'objective_pass_rate\']:.3f}"'),
            ('build_gallery', '"sub_scores": design.sub_scores,'),
            ('render_html', 'for name, value in card["sub_scores"].items():'),
        }),
    ),
    'scripts/generate_run_explorer.py': (
        'DFM check-name aliases for the run explorer, not the arena objective',
        frozenset({
            ('<module>', '("printable_min_wall", "min_wall", "wall_thickness_ok", "printable", "min_wall_ok")),'),
        }),
    ),
    'scripts/import_studio_repeat_renders.py': (
        'imports the frozen historical showcase renders (predates the removal)',
        frozenset({
            ('<module>', 'GATES = {"renders", "watertight", "nonzero_volume", "fits_envelope", "body_count", "min_wall"}'),
            ('prepare', 'gates = result["objective"]["sub_scores"]'),
            ('prepare', 'or abs(row["objective_pass_rate"] - sum(t["objective_pass_rate"] for t in actual) / len(actual)) > 1e-6):'),
            ('prepare', 'rate = result["objective"]["objective_pass_rate"]'),
        }),
    ),
    'scripts/recompute_no_min_wall.py': (
        'the one-off recompute of the two replay bundles',
        frozenset({
            ('<module>', 'RETIRED = "min_wall"'),
            ('recompute', 'f"{where}: recorded rate {row[\'objective_pass_rate\']} is not reproduced by its "'),
            ('recompute', 'gate["objective_pass_rate"] = _rounded(_rate(g_new))'),
            ('recompute', 'if _rounded(_old_rate(failed, borderline)) != row["objective_pass_rate"]:'),
            ('recompute', 'if _rounded(_old_rate(g_failed, [])) != gate["objective_pass_rate"]:'),
            ('recompute', 'row["objective_pass_rate"] = _rounded(new_rate)'),
            ('recompute', 'stats["rate_changed"] += _rounded(new_rate) != row["objective_pass_rate"]'),
        }),
    ),
    'scripts/regrade_openrouter_baseline.py': (
        "records the fresh regrade run's own new-gate objective",
        frozenset({
            ('main', '"objective_pass_rate": objective.get("objective_pass_rate"),'),
            ('main', '"sub_scores": objective.get("sub_scores"),'),
        }),
    ),
    'site/build_data.py': (
        'published aggregate rows from the frozen rounds / recomputed bundles',
        frozenset({
            ('_arena_page_round', '"objective_pass_rate": row.get("objective_pass_rate"),'),
            ('_arena_page_round', 'rows = sorted(rows, key=lambda r: (-(r["objective_pass_rate"] or 0.0), r["entrant"]))'),
            ('_arena_run_entry', '"objective_pass_rate": row.get("objective_pass_rate"),'),
            ('_prerender_matchups_html', 'elif row["objective_pass_rate"] is None and row["n_compile_errors"]:'),
            ('_prerender_matchups_html', 'elif row["objective_pass_rate"] is None and row["n_execution_errors"]:'),
            ('_prerender_matchups_html', 'if row["objective_pass_rate"] is None and row["n_infra_errors"]:'),
            ('_prerender_matchups_html', 'rate = f\'{row["objective_pass_rate"] * 100:.2f}%\' if row["objective_pass_rate"] is not None else "Unmeasured"'),
            ('_prerender_sections', '[row.get("entrant", ""), _fmt_mean(row.get("objective_pass_rate")),'),
            ('build_arena_page', '"name": "objective_pass_rate",'),
            ('build_matchups_page', 'rate = row.get("objective_pass_rate")'),
        }),
    ),
}


JS_ACCESSOR = "makerbench/arena_studio/static/app/lib/objectiveChecks.js"
JS_ALLOWLIST: dict[str, tuple[str, frozenset]] = {
    "makerbench/arena_studio/static/app/lib/workbench.js": (
        "hands the raw checks straight to retainedChecks",
        frozenset({"const checks = retainedChecks(block.checks || block.gates || block.sub_scores);"}),
    ),
    "makerbench/arena_studio/static/app/lib/agreement-stats.js": (
        "agreement scoreline rows, not a recorded trial objective",
        frozenset({
            '(row) => typeof row.subjective_elo === "number" && typeof row.objective_pass_rate === "number",',
            "passRate: row.objective_pass_rate,",
            "x: Math.min(Math.max(row.objective_pass_rate, 0), 1) * 100,",
        }),
    ),
    "makerbench/arena_studio/static/app/screens/agreement.js": (
        "agreement scoreline rows, not a recorded trial objective",
        frozenset({'<td class="num measure">${formatPassRate(row.objective_pass_rate)}</td>'}),
    ),
    "site/assets/app.js": (
        "published arena aggregate row",
        frozenset({'"<td class=\\"num\\">" + arenaNum(r.objective_pass_rate, 2) + "</td>" +'}),
    ),
}


def _py_files():
    for top in ("makerbench", "scripts", "site"):
        for path in sorted((ROOT / top).rglob("*.py")):
            yield path


def _hits(source: str) -> set[tuple[str, str, str]]:
    """Every (function, key, source line) that mentions a sensitive key, except dict-literal keys and docstrings."""
    lines = source.splitlines()
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def scope(node):
        names = []
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
        return ".".join(reversed(names)) or "<module>"

    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in KEYS:
            parent = parents.get(node)
            if (isinstance(parent, ast.Dict) and node in parent.keys) or isinstance(parent, ast.Expr):
                continue
            found.add((scope(node), node.value, lines[node.lineno - 1].strip()))
        elif isinstance(node, ast.Attribute) and node.attr in KEYS:
            found.add((scope(node), node.attr, lines[node.lineno - 1].strip()))
    return found


def _py_offenders(rel: str, source: str) -> list[str]:
    if rel == "makerbench/arena_checks.py":
        approved = None
    else:
        approved = PY_ALLOWLIST.get(rel, ("", frozenset()))[1]
    return [f"{rel}: {function}: {line}" for function, _key, line in sorted(_hits(source))
            if approved is not None and (function, line) not in approved]


def _js_offenders(rel: str, text: str) -> list[str]:
    if rel == JS_ACCESSOR:
        return []
    approved = JS_ALLOWLIST.get(rel, ("", frozenset()))[1]
    pattern = re.compile(r"objective_pass_rate|sub_scores|min_wall")
    return [f"{rel}:{number}: {line.strip()[:140]}" for number, line in enumerate(text.splitlines(), 1)
            if pattern.search(line) and not line.lstrip().startswith("//") and line.strip() not in approved]


def _js_files():
    for path in sorted((ROOT / "makerbench/arena_studio/static/app").rglob("*.js")):
        yield path
    yield ROOT / "site/assets/app.js"


def test_python_reads_of_a_recorded_objective_go_through_arena_checks():
    offenders = []
    for path in _py_files():
        rel = path.relative_to(ROOT).as_posix()
        offenders += _py_offenders(rel, path.read_text(encoding="utf-8"))
    assert not offenders, (
        "raw mention of objective_pass_rate / sub_scores / min_wall; use makerbench.arena_checks "
        "(trial_objective_rate, recorded_objective_rate, retained_checks, current_objective) or add the exact "
        "line to PY_ALLOWLIST with a reason:\n" + "\n".join(offenders))


def test_python_allowlist_has_no_stale_entries():
    stale = []
    for rel, (_reason, approved) in PY_ALLOWLIST.items():
        present = {(function, line) for function, _key, line in _hits((ROOT / rel).read_text(encoding="utf-8"))}
        stale += [f"{rel}: {function}: {line}" for function, line in sorted(approved - present)]
    assert not stale, "stale PY_ALLOWLIST entries:\n" + "\n".join(stale)


def test_javascript_reads_go_through_objective_checks():
    offenders = []
    for path in _js_files():
        offenders += _js_offenders(path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8"))
    assert not offenders, (
        "raw read in the Studio/site JavaScript; use lib/objectiveChecks.js (retainedChecks / objectiveRate) "
        "or add the exact line to JS_ALLOWLIST with a reason:\n" + "\n".join(offenders))


def test_javascript_allowlist_has_no_stale_entries():
    stale = []
    for rel, (_reason, approved) in JS_ALLOWLIST.items():
        lines = {line.strip() for line in (ROOT / rel).read_text(encoding="utf-8").splitlines()}
        stale += [f"{rel}: {line}" for line in sorted(approved - lines)]
    assert not stale, "stale JS_ALLOWLIST entries:\n" + "\n".join(stale)


# ---------------------------------------------------------------- RED controls: the guard must fail on these

def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_red_scoreline_regression_is_caught():
    """The old scoring bug: the scoreline reads the stored rate instead of rescoring it."""
    rel = "makerbench/code_cad_arena_runner.py"
    clean = _source(rel)
    assert not _py_offenders(rel, clean)
    good = "rate = recorded_objective_rate(objective)"
    assert clean.count(good) == 1
    mutated = clean.replace(good, 'rate = objective.get("objective_pass_rate")')
    assert _py_offenders(rel, mutated), "the guard accepted a stored-rate read in collect_objective_scoreline"


@pytest.mark.parametrize("snippet", [
    'read = row["result"]["objective"].get\n    return read("objective_pass_rate")',   # aliased getter
    'key = "objective_pass_rate"\n    return row["result"]["objective"].get(key)',       # constant variable key
    'return getattr(row["result"]["objective"], "get")("objective_pass_rate")',           # getattr form
    'return row["result"]["objective"]["objective_pass_rate"]',                           # plain subscript
    'return row["result"]["objective"].objective_pass_rate',                              # attribute read
    'return row["result"]["objective"].__getitem__("sub_scores")',                        # dunder getitem
    'return row["result"]["objective"].get("min_wall")',                                  # the retired check
])
def test_red_aliased_winner_getter_and_indirect_forms_are_caught(snippet):
    rel = "makerbench/code_cad_export.py"
    clean = _source(rel)
    assert not _py_offenders(rel, clean)
    mutated = clean + f"\n\ndef _planted(row):\n    {snippet}\n"
    assert _py_offenders(rel, mutated), snippet


def test_red_javascript_same_line_bypass_is_caught():
    rel = "makerbench/arena_studio/static/app/lib/workbench.js"
    clean = _source(rel)
    assert not _js_offenders(rel, clean)
    for planted in (
        'const rate = block["objective_pass_rate"]; retainedChecks({});',
        "const { objective_pass_rate: rate } = block; retainedChecks({});",
        "const rate = block.objective_pass_rate; objectiveRate(block);",
    ):
        assert _js_offenders(rel, clean + "\n" + planted + "\n"), planted


def test_red_unrelated_new_read_in_the_site_script_is_caught():
    rel = "site/assets/app.js"
    clean = _source(rel)
    assert not _js_offenders(rel, clean)
    assert _js_offenders(rel, clean + "\nconst leaked = trial.objective_pass_rate;\n")
