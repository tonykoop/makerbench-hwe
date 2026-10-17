"""#1085: the track-isolation guard walks the AST, so evasion forms cannot slip past it.

Scoring/publishing code must read run-log trials through ``run_log_io.trial_rows``. The engine
(``tests/_trial_guard.py``) flags any use of the constant key ``"trials"`` however it is spelled;
the allowlist below is by (file, enclosing function) and holds only the helper module, writers
and resume indexes, and two scripts that handle unrelated studio showcase data.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _trial_guard import analyze_source, run_log_module_findings, scan_tree

ROOT = Path(__file__).resolve().parents[1]

_HELPER = "run_log_io helper module: the only sanctioned readers of the raw list"
_WRITER = "writer/merger: must preserve, update or index every stored row"
_OTHER_DATA = "not a run log: studio showcase/demo data with its own 'trials' key"
RAW_TRIAL_ALLOWLIST: dict[tuple[str, str], str] = {
    ("makerbench/run_log_io.py", "trial_rows"): _HELPER,
    ("makerbench/run_log_io.py", "find_trial"): _HELPER + " (by-id lookup)",
    ("makerbench/run_log_io.py", "trials_by_id"): _HELPER + " (by-id index)",
    ("makerbench/run_log_io.py", "summary_trials"): _HELPER
    + " (reads an already-filtered summary)",
    ("makerbench/code_cad_arena_runner.py", "ingest_candidate"): _WRITER,
    ("makerbench/code_cad_orchestrator.py", "run_orchestration"): _WRITER + " (resume index)",
    ("makerbench/code_cad_orchestrator.py", "_load_log"): _WRITER,
    ("makerbench/code_cad_orchestrator.py", "_merge_and_write"): _WRITER,
    ("makerbench/nightly_cad.py", "_append_error_trial"): _WRITER,
    ("scripts/regrade_scoreline.py", "regrade"): _WRITER + " (writes the already-filtered copy)",
    ("scripts/build_studio_demo_data.py", "validate_aggregate_seeds"): _OTHER_DATA,
    ("scripts/build_studio_demo_data.py", "build"): _OTHER_DATA,
    ("scripts/build_studio_demo_space.py", "audit_snapshot"): _OTHER_DATA,
}
# Modules that json-load a run log but never iterate trials: they read ``config``, hash the file,
# or hand the log to functions that already go through trial_rows / find_trial.
RUN_LOG_LOADER_ALLOWLIST: dict[str, str] = {
    "makerbench/cli_arena.py": "reads config and passes the log to filtered scoreline/pairing functions",
    "makerbench/code_cad_tier_comparison.py": "reads config; scores via collect_objective_scoreline",
    "makerbench/arena_studio/workbench.py": "loads one log to look a trial up with find_trial",
    "scripts/replay_provenance.py": "hashes run_log.json files; never reads rows",
    "site/build_data.py": "reads config and scoreline files; trial rows never touched",
}
OTHER_DATA_FILES = ("scripts/build_studio_demo_data.py", "scripts/build_studio_demo_space.py")

_SCAN = scan_tree(ROOT)


def test_no_raw_trial_access_outside_the_allowlist():
    """A new reader of run-log trials must use run_log_io.trial_rows, or be justified here."""
    offenders = [str(f) for f in _SCAN[0] if (f.file, f.function) not in RAW_TRIAL_ALLOWLIST]
    assert not offenders, (
        "raw run-log trial access; iterate makerbench.run_log_io.trial_rows(run_log) "
        "(track isolation), or add a justified (file, function) to RAW_TRIAL_ALLOWLIST:\n"
        + "\n".join(offenders)
    )


def test_raw_trial_allowlist_has_no_stale_entries():
    present = {(f.file, f.function) for f in _SCAN[0]}
    stale = [
        f"{file}:{func}" for (file, func) in RAW_TRIAL_ALLOWLIST if (file, func) not in present
    ]
    assert not stale, "stale allowlist entries:\n" + "\n".join(stale)


def test_run_log_loaders_use_the_helper_or_are_listed():
    offenders = [str(f) for f in _SCAN[1] if f.file not in RUN_LOG_LOADER_ALLOWLIST]
    assert not offenders, (
        "module json-loads a run log without trial_rows; use the helper or justify it in "
        "RUN_LOG_LOADER_ALLOWLIST:\n" + "\n".join(offenders)
    )
    stale = [file for file in RUN_LOG_LOADER_ALLOWLIST if file not in {f.file for f in _SCAN[1]}]
    assert not stale, "stale loader allowlist entries:\n" + "\n".join(stale)


@pytest.mark.parametrize("rel", OTHER_DATA_FILES)
def test_other_data_scripts_never_touch_run_logs(rel):
    """The 'other data' exemption holds only while these scripts stay away from run logs."""
    source = (ROOT / rel).read_text(encoding="utf-8")
    assert "run_log" not in source and "trial_rows" not in source


def test_allowlist_is_not_broader_than_helper_writers_and_other_data():
    for (file, _func), reason in RAW_TRIAL_ALLOWLIST.items():
        if file in OTHER_DATA_FILES:
            assert reason == _OTHER_DATA
        elif file == "makerbench/run_log_io.py":
            assert reason.startswith(_HELPER)
        else:
            assert reason.startswith(_WRITER), f"{file} is neither helper, writer nor other data"


# ---------------------------------------------------------------- the guard itself

EVASIONS = {
    "subscript": 'rows = log["trials"]',
    "store subscript": 'log["trials"] = []',
    "delete": 'del log["trials"]',
    "get": 'rows = log.get("trials", [])',
    "setdefault": 'log.setdefault("trials", []).append(x)',
    "pop": 'rows = log.pop("trials")',
    "dunder getitem": 'rows = log.__getitem__("trials")',
    "alias of .get": 'fetch = log.get\nrows = fetch("trials")',
    "alias in function": 'def f(log):\n    g = log.get\n    return g("trials")',
    "getattr call": 'rows = getattr(log, "get")("trials", [])',
    "alias of getattr": 'g = getattr(log, "get")\nrows = g("trials")',
    "getattr attribute": 'rows = getattr(log, "trials")',
    "variable key": 'KEY = "trials"\nrows = log[KEY]',
    "variable key in function": 'def f(log):\n    key = "trials"\n    return log.get(key)',
    "module constant used in function": 'K = "trials"\ndef f(log):\n    return log[K]',
    "loop variable over literals": 'for key in ("config", "trials"):\n    x = log[key]',
    "concatenated key": 'rows = log["tri" + "als"]',
    "fstring key": 'part = "tri"\nrows = log[f"{part}als"]',
    "join key": 'rows = log["".join(["tr", "ials"])]',
    "case-folded key": 'rows = log["TRIALS".lower()]',
    "partial accessor": 'import functools\nread = functools.partial(log.get, "trials")',
    "itemgetter": 'import operator\nrows = operator.itemgetter("trials")(log)',
    "operator.getitem": 'import operator\nrows = operator.getitem(log, "trials")',
    "json then subscript": 'import json\ndata = json.loads(text)\nrows = data["trials"]',
    "json then variable alias": 'import json\ndata = json.load(fh)\nname = "trials"\nrows = data.get(name)',
    "nested alias": 'def outer(log):\n    get = log.get\n    def inner():\n        return get("trials")\n    return inner',
}


@pytest.mark.parametrize("name", sorted(EVASIONS))
def test_every_evasion_form_is_caught(name):
    findings = analyze_source(EVASIONS[name], "x.py")
    assert findings, f"guard missed: {name}\n{EVASIONS[name]}"


CLEAN = {
    "helper": "rows = trial_rows(log)",
    "dict literal key": 'log = {"trials": [], "config": {}}',
    "keyword argument": "f(trials=3)",
    "trial_id lookup": 'row = other["trial_id"]',
    "docstring mention": 'def f():\n    """Reads ``run_log["trials"]`` -- see trial_rows."""\n    return trial_rows(log)',
    "comment only": "# log.get('trials') is forbidden\nx = 1",
    "different key": 'rows = log.get("config")',
    "partial text": 'rows = log["trials_total"]',
    "unrelated alias": 'fetch = log.get\nrows = fetch("config")',
}


@pytest.mark.parametrize("name", sorted(CLEAN))
def test_clean_code_is_not_flagged(name):
    assert analyze_source(CLEAN[name], "x.py") == []


def test_constant_imported_from_another_module_is_resolved(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "consts.py").write_text('RUN_KEY = "trials"\n')
    (tmp_path / "scripts" / "reader.py").write_text(
        "from consts import RUN_KEY\n\ndef read(log):\n    return log[RUN_KEY]\n"
    )
    access, _ = scan_tree(tmp_path, source_dirs=("scripts",))
    assert [(f.file, f.function) for f in access] == [("scripts/reader.py", "read")]


def test_indirect_reads_of_a_loaded_run_log_are_caught():
    """No literal key at all: values()/items() over a json-loaded run log."""
    source = (
        "import json\n"
        "from pathlib import Path\n"
        "def rows(run_dir):\n"
        '    log = json.loads((Path(run_dir) / "run_log.json").read_text())\n'
        "    return [v for v in log.values() if isinstance(v, list)]\n"
    )
    assert analyze_source(source, "x.py") == []  # the key search cannot see it...
    assert run_log_module_findings(source, "x.py")  # ...the loader rule does
    assert not run_log_module_findings(
        source + "from makerbench.run_log_io import trial_rows\n", "x.py"
    )


def test_findings_name_the_enclosing_function():
    source = "class C:\n    def m(self, log):\n        return log['trials']\n"
    (finding,) = analyze_source(source, "x.py")
    assert finding.function == "C.m" and finding.line == 3
