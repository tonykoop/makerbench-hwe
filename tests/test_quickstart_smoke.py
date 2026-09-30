"""quickstart_smoke helpers: extraction from the real doc and tolerant output matching."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("quickstart_smoke", ROOT / "scripts" / "quickstart_smoke.py")
qs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qs)

RICH = """arena matrix: 1 instruments x 1 seeds x 1 reps x 2 models = 2 trials
summary: {"scored": 2}
   Objective scoreline (mean
           pass-rate)
┏━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━┓
┃ entrant ┃ pass-rate ┃ trials ┃
│ stub-a  │     1.000 │      1 │
│ stub-b  │     1.000 │      1 │
"""


def test_extracts_rung1_from_the_real_doc():
    cmds, expected = qs.extract(qs.DOC.read_text(encoding="utf-8"))
    assert any("arena run" in c and "--stub" in c for c in cmds)
    assert not any(c.startswith(("git clone", "cd ")) for c in cmds)
    assert 'summary: {"scored": 2}' in expected


def test_rich_table_output_matches_doc():
    _, expected = qs.extract(qs.DOC.read_text(encoding="utf-8"))
    assert qs.missing(RICH, expected) == []


def test_detects_regression():
    _, expected = qs.extract(qs.DOC.read_text(encoding="utf-8"))
    bad = RICH.replace('{"scored": 2}', '{"auto_fail": 2}').replace("1.000", "0.000")
    assert qs.missing(bad, expected)
