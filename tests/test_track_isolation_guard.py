"""#1085: the track-isolation guard walks the AST, so evasion forms cannot slip past it.

Scoring/publishing code must read run-log trials through ``run_log_io.trial_rows``. The engine
(``tests/_trial_guard.py``) flags any use of the constant key ``"trials"`` however it is spelled;
the allowlist below is by (file, enclosing function) and holds only the helper module, writers
and resume indexes, and two scripts that handle unrelated studio showcase data.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _trial_guard import analyze_source, key_findings, loaded_log_findings, run_log_module_findings, scan_tree

ROOT = Path(__file__).resolve().parents[1]

_HELPER = "run_log_io helper module: the only sanctioned readers of the raw list"
_WRITER = "writer/merger: appends, merges or indexes rows; reviewed line"
_OTHER_DATA = "not a run log: studio showcase/demo data with its own 'trials' key"
_UNRELATED = "unrelated: a table column title"
# Exact approved occurrences: (file, enclosing function) -> (reason, exact stripped source lines).
# A new aggregation inside an approved function is a different line and fails the guard.
RAW_TRIAL_ALLOWLIST: dict[tuple[str, str], tuple[str, frozenset]] = {
    ("makerbench/run_log_io.py", "trial_rows"): (_HELPER, frozenset({
        'return [row for row in run_log.get("trials") or [] if not _off_track(row, config)]'})),
    ("makerbench/run_log_io.py", "find_trial"): (_HELPER + " (by-id lookup)", frozenset({
        'matches = [row for row in run_log.get("trials") or [] if row.get("trial_id") == trial_id]'})),
    ("makerbench/run_log_io.py", "trials_by_id"): (_HELPER + " (by-id index)", frozenset({
        'return {str(row.get("trial_id")): row for row in run_log.get("trials") or []}'})),
    ("makerbench/run_log_io.py", "summary_trials"): (_HELPER + " (reads an already-filtered summary)", frozenset({
        'return list(summary.get("trials") or [])'})),
    ("makerbench/code_cad_arena_runner.py", "ingest_candidate"): (_WRITER, frozenset({
        'log.setdefault("trials", []).append(entry)'})),
    ("makerbench/code_cad_orchestrator.py", "run_orchestration"): (_WRITER + " (resume index by trial_id)", frozenset({
        'existing = {entry["trial_id"]: entry for entry in log["trials"]}'})),
    ("makerbench/code_cad_orchestrator.py", "_load_log"): (_WRITER, frozenset({
        'payload.setdefault("trials", [])'})),
    ("makerbench/code_cad_orchestrator.py", "_merge_and_write"): (_WRITER, frozenset({
        'payload["trials"] = merge_trial_rows(payload["trials"], managed)'})),
    ("makerbench/nightly_cad.py", "_append_error_trial"): (_WRITER, frozenset({
        'payload.setdefault("trials", []).append('})),
    ("makerbench/cli_arena.py", "arena_run"): (_UNRELATED, frozenset({
        'table.add_column("trials", justify="right")'})),
    ("makerbench/arena_studio/demo.py", "_without_retired"): (_OTHER_DATA, frozenset({
        'if isinstance(node.get("trials"), list):',
        'node["trials"] = [_without_retired(t) for t in node["trials"]]'})),
    ("scripts/regrade_scoreline.py", "regrade"): (_WRITER + " (writes the already-filtered copy)", frozenset({
        'regraded["trials"] = copy.deepcopy(trial_rows(log))',
        'for trial in regraded.get("trials") or []:'})),
    ("scripts/build_studio_demo_data.py", "validate_aggregate_seeds"): (_OTHER_DATA, frozenset({
        'trials = row.get("trials", [])'})),
    ("scripts/build_studio_demo_data.py", "build"): (_OTHER_DATA, frozenset({
        'for trial in record["trials"]',
        'row["image"] = row["trials"][0]["image"]',
        'row["trials"] = [{"seed": trial["seed"], "image": image(trial["image"]),'})),
    ("scripts/build_studio_demo_space.py", "audit_snapshot"): (_OTHER_DATA, frozenset({
        '"status", "image", "recorded_pipeline_rate", "gates", "trials", "context_tier"}:',
        'for trial in row.get("trials", []):',
        'trials = row.get("trials", [])'})),
}
# A run log read in a scope that names ``run_log`` must not escape the helpers except on these
# exact reviewed lines (file, function) -> (reason, lines).
LOADED_LOG_ALLOWLIST: dict[tuple[str, str], tuple[str, frozenset]] = {
    ("makerbench/cli_arena.py", "arena_run"): ("an image map loaded next to the run, not the run log", frozenset({
        "raw_image_map.items"})),
    ("makerbench/code_cad_arena_report.py", "load_run_payloads"): ("packs the loaded log into the payload dict that report code reads with trial_rows", frozenset({
        "{'run_log': run_log, 'elo': elo, 'scoreline': scoreline, 'judge': judge, 'agreement': agreement}"})),
}
OTHER_DATA_FILES = (
    "makerbench/arena_studio/demo.py",
    "scripts/build_studio_demo_data.py", "scripts/build_studio_demo_space.py")

_SCAN = scan_tree(ROOT)


def _unapproved(findings, allowlist):
    return [
        str(f) for f in findings if f.text not in allowlist.get((f.file, f.function), ("", frozenset()))[1]
    ]


def _stale(findings, allowlist):
    present = {(f.file, f.function, f.text) for f in findings}
    return [
        f"{file}:{func}: {line}"
        for (file, func), (_reason, lines) in allowlist.items()
        for line in sorted(lines)
        if (file, func, line) not in present
    ]


def test_no_raw_trial_access_outside_the_allowlist():
    """A new reader of run-log trials must use run_log_io.trial_rows, or be justified here."""
    offenders = _unapproved(_SCAN[0], RAW_TRIAL_ALLOWLIST)
    assert not offenders, (
        "raw run-log trial access; iterate makerbench.run_log_io.trial_rows(run_log) "
        "(track isolation), or add the exact line to RAW_TRIAL_ALLOWLIST with a reason:\n"
        + "\n".join(offenders)
    )


def test_raw_trial_allowlist_has_no_stale_entries():
    stale = _stale(_SCAN[0], RAW_TRIAL_ALLOWLIST)
    assert not stale, "stale allowlist entries:\n" + "\n".join(stale)


def test_loaded_run_logs_do_not_escape_the_helpers():
    offenders = _unapproved(_SCAN[1], LOADED_LOG_ALLOWLIST)
    assert not offenders, (
        "a json-loaded run log is iterated/returned/unpacked outside the helpers; use "
        "trial_rows / find_trial or add the exact line to LOADED_LOG_ALLOWLIST:\n" + "\n".join(offenders)
    )
    stale = _stale(_SCAN[1], LOADED_LOG_ALLOWLIST)
    assert not stale, "stale LOADED_LOG_ALLOWLIST entries:\n" + "\n".join(stale)


@pytest.mark.parametrize("rel", OTHER_DATA_FILES)
def test_other_data_scripts_never_touch_run_logs(rel):
    """The 'other data' exemption holds only while these scripts stay away from run logs."""
    source = (ROOT / rel).read_text(encoding="utf-8")
    assert "run_log" not in source and "trial_rows" not in source


def test_allowlist_is_not_broader_than_helper_writers_and_other_data():
    for (file, _func), (reason, _lines) in RAW_TRIAL_ALLOWLIST.items():
        if file in OTHER_DATA_FILES:
            assert reason == _OTHER_DATA
        elif reason == _UNRELATED:
            assert file == "makerbench/cli_arena.py"
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
    "alias chain": 'def f(log):\n    first = log.get\n    second = first\n    return second("trials")',
    "long alias chain": 'a = log.get\nb = a\nc = b\nd = c\nrows = d("trials")',
    "alias chain declared in reverse": 'def f(log):\n    return second("trials")\nsecond = first\nfirst = log.get',
    "constant chain": 'def f(log):\n    first = "trials"\n    second = first\n    return log[second]',
    "built key chain": 'def f(log):\n    first = "tri"\n    second = first\n    key = second + "als"\n    return log[key]',
    "augmented key": 'def f(log):\n    key = "tri"\n    key += "als"\n    return log[key]',
    "tuple unpack key": 'def f(log):\n    a, b = "tri", "als"\n    return log[a + b]',
    "walrus key": 'def f(log):\n    return log[(k := "trials")]',
    "attribute read": "def f(log):\n    return log.trials",
    "getattr alias chain": 'def f(log):\n    g = getattr(log, "get")\n    h = g\n    return h("trials")',
    "container-held key": 'KEYS = {"rows": "trials"}\ndef f(log):\n    return log[KEYS["rows"]]',
    "nested alias": 'def outer(log):\n    get = log.get\n    def inner():\n        return get("trials")\n    return inner',
}


@pytest.mark.parametrize("name", sorted(EVASIONS))
def test_every_evasion_form_is_caught(name):
    findings = key_findings(EVASIONS[name], "x.py", frozenset({"trials"}))
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
    assert ("scripts/reader.py", "read") in [(f.file, f.function) for f in access]
    assert ("scripts/consts.py", "<module>") in [(f.file, f.function) for f in access]


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
    assert [f.function for f in loaded_log_findings(source, "x.py")] == ["rows"]  # ...the escape rule does
    assert run_log_module_findings(source, "x.py")


def test_importing_trial_rows_without_calling_it_does_not_count():
    source = (
        "import json\nfrom makerbench.run_log_io import trial_rows\n"
        "def rows(run_dir):\n"
        '    log = json.loads(open("run_log.json").read())\n'
        "    return list(log.values())\n"
    )
    assert run_log_module_findings(source, "x.py")  # imported, never called
    assert loaded_log_findings(source, "x.py")  # and the data still escapes
    called = source + "def ok(log):\n    return trial_rows(log)\n"
    assert not run_log_module_findings(called, "x.py")
    assert loaded_log_findings(called, "x.py")  # a call elsewhere does not excuse the escape


ESCAPES = {
    "values": "return list(log.values())",
    "items": "return dict(log.items())",
    "iteration": "return [row for row in log]",
    "for loop": "for key in log:\n        pass",
    "return it": "return log",
    "star-star": "return {**log}",
    "alias then values": "alias = log\n    return list(alias.values())",
    "run_log parameter": None,
}


@pytest.mark.parametrize("name", [n for n in sorted(ESCAPES) if ESCAPES[n]])
def test_loaded_run_log_escape_forms_are_caught(name):
    source = (
        "import json\n"
        "def rows(run_dir):\n"
        '    log = json.loads((run_dir / "run_log.json").read_text())\n'
        f"    {ESCAPES[name]}\n"
    )
    assert loaded_log_findings(source, "x.py"), name


def test_run_log_parameter_escape_is_caught():
    assert loaded_log_findings("def rows(run_log):\n    return list(run_log.values())\n", "x.py")


def test_constant_key_reads_and_calls_are_left_to_the_other_rules():
    source = (
        "import json\n"
        "def f(run_dir):\n"
        '    log = json.loads((run_dir / "run_log.json").read_text())\n'
        '    config = log["config"]\n    return summarize(log), config, log.get("seed")\n'
    )
    assert loaded_log_findings(source, "x.py") == []


def test_findings_name_the_enclosing_function():
    source = "class C:\n    def m(self, log):\n        return log['trials']\n"
    (finding,) = analyze_source(source, "x.py")
    assert finding.function == "C.m" and finding.line == 3


# ---------------------------------------------- mutation controls against the real sources (#1096 review)

def _source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _offenders_in(rel: str, source: str) -> list[str]:
    targets = frozenset({"trials"})
    return _unapproved(key_findings(source, rel, targets), RAW_TRIAL_ALLOWLIST) + _unapproved(
        loaded_log_findings(source, rel), LOADED_LOG_ALLOWLIST
    )


REAL_PROBES = {
    "chained accessor alias": "def _review_probe(log):\n    first = log.get\n    second = first\n    return second('trials')\n",
    "chained constant key": "def _review_probe(log):\n    first = 'trials'\n    second = first\n    return log[second]\n",
    "attribute read": "def _review_probe(log):\n    return log.trials\n",
    "built key chain": "def _review_probe(log):\n    a = 'tri'\n    b = a\n    return log[b + 'als']\n",
    "values() escape": "def _review_probe(run_log):\n    return list(run_log.values())\n",
}


@pytest.mark.parametrize("name", sorted(REAL_PROBES))
def test_red_probe_planted_in_a_real_module_is_caught(name):
    rel = "makerbench/code_cad_export.py"
    clean = _source(rel)
    assert _offenders_in(rel, clean) == []
    assert _offenders_in(rel, clean + "\n\n" + REAL_PROBES[name]), name


@pytest.mark.parametrize(
    ("rel", "anchor"),
    [
        ("makerbench/code_cad_arena_runner.py", 'log.setdefault("trials", []).append(entry)'),
        ("makerbench/code_cad_orchestrator.py", 'existing = {entry["trial_id"]: entry for entry in log["trials"]}'),
        ("scripts/regrade_scoreline.py", 'regraded["trials"] = copy.deepcopy(trial_rows(log))'),
        ("makerbench/nightly_cad.py", 'payload.setdefault("trials", []).append('),
    ],
)
def test_red_new_aggregate_inside_an_approved_writer_is_caught(rel, anchor):
    """Approval is for the exact reviewed line, not the whole function around it."""
    clean = _source(rel)
    assert _offenders_in(rel, clean) == []
    assert clean.count(anchor) == 1
    indent = " " * (len(clean[: clean.index(anchor)].rsplit("\n", 1)[-1]))
    planted = clean.replace(
        anchor,
        'leaked_mean = sum(r.get("score", 0) for r in log.get("trials", []))\n' + indent + anchor,
    )
    assert _offenders_in(rel, planted), rel
