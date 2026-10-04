"""Tests for scripts/check_showcase_claims.py (story #856)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "check_showcase_claims", REPO_ROOT / "scripts" / "check_showcase_claims.py"
)
claims = importlib.util.module_from_spec(_SPEC)
sys.modules["check_showcase_claims"] = claims
_SPEC.loader.exec_module(claims)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "score.json").write_text(
        json.dumps(
            {
                "headline": {"value": 0.0732, "rounds": [6, 7, 8, 9, 10]},
                "rows": [
                    {"name": "a", "rate": 0.520833, "status": "scored"},
                    {"name": "b", "rate": 0.895833, "status": "scored"},
                    {"name": "c", "rate": 0.7, "status": "failed"},
                ],
                "cost_usd": 8.56,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "results" / "report.md").write_text(
        "- Output: 881 bodies; bbox 668.6 × 149.9 × 841.9 mm\n", encoding="utf-8"
    )
    (tmp_path / "results" / "times.tsv").write_text(
        "backend\tseconds\nopenscad\t41.5\ncadquery\t63.2\n", encoding="utf-8"
    )
    return tmp_path


def run(repo: Path, body: str, *extra: str, capsys=None) -> tuple[int, str]:
    doc = repo / "post.md"
    doc.write_text(body, encoding="utf-8")
    code = claims.main([str(doc), "--root", str(repo), "-v", *extra])
    out = capsys.readouterr().out if capsys else ""
    return code, out


def post(text: str, *comments: str) -> str:
    return "## p1\n\n```text\n" + text + "\n```\n\n" + "\n".join(comments) + "\n"


def test_match_json_pointer_and_aggregates(repo, capsys):
    body = post(
        "rho was about 0.07 over rounds 6 to 10; pass rates 0.52 to 0.90; 2 of 3 scored; $8.56 spent.",
        "<!-- claim: 0.07 source: results/score.json#/headline/value -->",
        "<!-- claim: 6 source: results/score.json#/headline/rounds/0 -->",
        "<!-- claim: 10 source: results/score.json#/headline/rounds/-1 -->",
        "<!-- claim: 0.52 source: results/score.json#/rows/*/rate|min -->",
        "<!-- claim: 0.90 source: results/score.json#/rows/*/rate|max -->",
        "<!-- claim: 2 source: results/score.json#/rows/*/status|count=scored -->",
        "<!-- claim: 3 source: results/score.json#/rows|len -->",
        "<!-- claim: $8.56 source: results/score.json#/cost_usd -->",
    )
    code, out = run(repo, body, capsys=capsys)
    assert code == 0, out
    assert "8 matched, 0 errors, 0 uncited" in out


def test_match_regex_and_table_sources(repo, capsys):
    body = post(
        "881 bodies in a 669 × 150 × 842 mm box; CadQuery took 63.2 s.",
        "<!-- claim: 881 source: results/report.md#re:Output: (\\d+) bodies -->",
        "<!-- claim: 669 source: results/report.md#re:bbox ([\\d.]+) × -->",
        "<!-- claim: 150 source: results/report.md#re:bbox [\\d.]+ × ([\\d.]+) × -->",
        "<!-- claim: 842 source: results/report.md#re:× ([\\d.]+) mm -->",
        "<!-- claim: 63.2 source: results/times.tsv#where=backend:cadquery,col=seconds -->",
    )
    code, out = run(repo, body, capsys=capsys)
    assert code == 0, out
    assert "5 matched" in out


def test_mismatch_fails(repo, capsys):
    body = post(
        "The bbox was 668 mm long and rho was 0.09.",
        "<!-- claim: 668 source: results/report.md#re:bbox ([\\d.]+) × -->",
        "<!-- claim: 0.09 source: results/score.json#/headline/value -->",
    )
    code, out = run(repo, body, capsys=capsys)
    assert code == 1
    assert out.count("MISMATCH") == 2
    assert "668 (=668) vs source 668.6" in out


def test_missing_source_file_and_bad_pointer(repo, capsys):
    body = post(
        "Scores 0.5 and 0.7.",
        "<!-- claim: 0.5 source: results/nope.json#/x -->",
        "<!-- claim: 0.7 source: results/score.json#/headline/missing -->",
    )
    code, out = run(repo, body, capsys=capsys)
    assert code == 1
    assert "source file 'results/nope.json' not found" in out
    assert "key 'missing' not found" in out


def test_source_outside_repo_is_rejected(repo, capsys):
    body = post("Value 1.", "<!-- claim: 1 source: ../outside.json#/x -->")
    code, out = run(repo, body, capsys=capsys)
    assert code == 1
    assert "outside the repository" in out


@pytest.mark.parametrize(
    ("claim", "actual", "ok"),
    [
        ("0.07", 0.0732, True),
        ("0.07", 0.0751, False),
        ("0.073", 0.0732, True),
        ("0.0732", 0.0735, False),
        ("669", 668.6, True),
        ("668", 668.6, False),
        ("1.000", 0.9996, True),
        ("1.000", 0.9994, False),
        ("52%", 0.520833, True),
        ("52%", 0.526, False),
        ("52%", 52.3, True),
        ("1,300", 1300, True),
        ("$8.56", 8.564, True),
    ],
)
def test_precision_tolerance(claim, actual, ok):
    assert claims.compare(claim, actual, None)[0] is ok


def test_explicit_tolerance_override(repo, capsys):
    body = post("About 0.1.", "<!-- claim: 0.1 source: results/score.json#/headline/value tol: 0.03 -->")
    code, out = run(repo, body, capsys=capsys)
    assert code == 0, out


def test_uncited_number_warns_and_strict_fails(repo, capsys):
    body = post("Cited 0.07, uncited 42.", "<!-- claim: 0.07 source: results/score.json#/headline/value -->")
    code, out = run(repo, body, capsys=capsys)
    assert code == 0
    assert "warning: UNCITED number 42" in out
    code, out = run(repo, body, "--strict", capsys=capsys)
    assert code == 1
    assert "error: UNCITED number 42" in out


def test_nocheck_needs_reason_and_covers_number(repo, capsys):
    body = post("Built on 4 July, c. 2600 BC.", "<!-- nocheck: 4, 2600 reason: dates, no results file -->")
    code, out = run(repo, body, "--strict", capsys=capsys)
    assert code == 0, out
    assert "2 nocheck" not in out  # one nocheck comment, listing two values
    assert "1 nocheck" in out
    body = post("Built on 4 July.", "<!-- nocheck: 4 reason: -->")
    code, out = run(repo, body, capsys=capsys)
    assert code == 1


def test_stale_citation_when_value_not_in_text(repo, capsys):
    body = post("rho was about 0.08.", "<!-- claim: 0.07 source: results/score.json#/headline/value -->")
    code, out = run(repo, body, capsys=capsys)
    assert code == 1
    assert "STALE claim 0.07" in out


def test_hashtags_issue_refs_and_versions_are_not_numbers():
    found = [m.group(0) for m in claims.displayed_numbers("see #666 and #CAD, v1.2, p7, 28-body, 3 seeds")]
    assert found == ["28", "3"]


def test_fanout_without_aggregate_is_an_error():
    with pytest.raises(claims.SourceError):
        claims.resolve_json({"a": [1, 2]}, "/a/*")


def test_real_showcase_docs_pass():
    code = claims.main(["--root", str(REPO_ROOT), "--strict"])
    assert code == 0
