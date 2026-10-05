"""Tests for scripts/check_showcase_claims.py (story #856).

All fixtures are synthetic except the real-docs checks, which run over the
showcase case studies.
"""

from __future__ import annotations

import importlib.util
import json
import re
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
    doc = repo / "doc.md"
    doc.write_text(body, encoding="utf-8")
    code = claims.main([str(doc), "--root", str(repo), "-v", *extra])
    out = capsys.readouterr().out if capsys else ""
    return code, out


def quoted(text: str, *comments: str) -> str:
    return "## s1\n\n```text\n" + text + "\n```\n\n" + "\n".join(comments) + "\n"


def test_match_json_pointer_and_aggregates(repo, capsys):
    body = quoted(
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
    body = quoted(
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
    body = quoted(
        "The bbox was 668 mm long and rho was 0.09.",
        "<!-- claim: 668 source: results/report.md#re:bbox ([\\d.]+) × -->",
        "<!-- claim: 0.09 source: results/score.json#/headline/value -->",
    )
    code, out = run(repo, body, capsys=capsys)
    assert code == 1
    assert out.count("MISMATCH") == 2
    assert "668 (=668) vs source 668.6" in out


def test_missing_source_file_and_bad_pointer(repo, capsys):
    body = quoted(
        "Scores 0.5 and 0.7.",
        "<!-- claim: 0.5 source: results/nope.json#/x -->",
        "<!-- claim: 0.7 source: results/score.json#/headline/missing -->",
    )
    code, out = run(repo, body, capsys=capsys)
    assert code == 1
    assert "source file 'results/nope.json' not found" in out
    assert "key 'missing' not found" in out


def test_source_outside_repo_is_rejected(repo, capsys):
    body = quoted("Value 1.", "<!-- claim: 1 source: ../outside.json#/x -->")
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
    body = quoted("About 0.1.", "<!-- claim: 0.1 source: results/score.json#/headline/value tol: 0.03 -->")
    code, out = run(repo, body, capsys=capsys)
    assert code == 0, out


def test_uncited_number_warns_and_strict_fails(repo, capsys):
    body = quoted("Cited 0.07, uncited 42.", "<!-- claim: 0.07 source: results/score.json#/headline/value -->")
    code, out = run(repo, body, capsys=capsys)
    assert code == 0
    assert "warning: UNCITED number 42" in out
    code, out = run(repo, body, "--strict", capsys=capsys)
    assert code == 1
    assert "error: UNCITED number 42" in out


def test_nocheck_needs_reason_and_covers_number(repo, capsys):
    body = quoted("Built on 4 July, c. 2600 BC.", "<!-- nocheck: 4, 2600 reason: dates, no results file -->")
    code, out = run(repo, body, "--strict", capsys=capsys)
    assert code == 0, out
    assert "2 nocheck" not in out  # one nocheck comment, listing two values
    assert "1 nocheck" in out
    body = quoted("Built on 4 July.", "<!-- nocheck: 4 reason: -->")
    code, out = run(repo, body, capsys=capsys)
    assert code == 1


def test_stale_citation_when_value_not_in_text(repo, capsys):
    body = quoted("rho was about 0.08.", "<!-- claim: 0.07 source: results/score.json#/headline/value -->")
    code, out = run(repo, body, capsys=capsys)
    assert code == 1
    assert "STALE: 0.07 not found" in out


def test_hashtags_issue_refs_and_versions_are_not_numbers():
    found = [m.group(0) for m in claims.displayed_numbers("see #666 and #CAD, v1.2, p7, 28-body, 3 seeds")]
    assert found == ["28", "3"]


def test_fanout_without_aggregate_is_an_error():
    with pytest.raises(claims.SourceError):
        claims.resolve_json({"a": [1, 2]}, "/a/*")


def test_real_showcase_docs_pass():
    code = claims.main(["--root", str(REPO_ROOT), "--strict"])
    assert code == 0


# ---------------------------------------------------------------- signs


@pytest.fixture()
def signed(tmp_path: Path) -> Path:
    (tmp_path / "ci.json").write_text(
        json.dumps({"rho": -0.5, "lo": -0.4012, "hi": 0.5487}), encoding="utf-8"
    )
    return tmp_path


def test_sign_mismatch_fails(signed, capsys):
    body = quoted("Round 6 rho was +0.5.", "<!-- claim: +0.5 source: ci.json#/rho -->")
    code, out = run(signed, body, capsys=capsys)
    assert code == 1
    assert "MISMATCH +0.5 (=0.5) vs source -0.5" in out


def test_dropped_sign_in_text_is_stale_not_silently_matched(signed, capsys):
    # The text says 0.5 (positive); a citation for -0.5 must not be satisfied by it.
    body = quoted("Round 6 rho was 0.5.", "<!-- claim: -0.5 source: ci.json#/rho -->")
    code, out = run(signed, body, capsys=capsys)
    assert code == 1
    assert "STALE: -0.5 not found" in out


@pytest.mark.parametrize("claim", ["−0.40", "-0.40"])
def test_unicode_minus_matches(signed, capsys, claim):
    body = quoted(
        "Bootstrap CI [−0.40, +0.55].",
        f"<!-- claim: {claim} source: ci.json#/lo -->",
        "<!-- claim: +0.55 source: ci.json#/hi -->",
    )
    code, out = run(signed, body, "--strict", capsys=capsys)
    assert code == 0, out
    assert "2 matched, 0 errors, 0 uncited" in out


def test_en_dash_range_is_not_negative(signed, capsys):
    tokens = [m.group(0) for m in claims.displayed_numbers("over rounds 6–10")]
    assert tokens == ["6", "10"]
    assert claims.parse_displayed(tokens[1])[0] > 0
    # And a positive citation for the range end passes against a positive source.
    (signed / "r.json").write_text(json.dumps({"last": 10}), encoding="utf-8")
    body = quoted("Rounds 6–10.", "<!-- claim: 10 source: r.json#/last -->", "<!-- nocheck: 6 reason: test -->")
    code, out = run(signed, body, "--strict", capsys=capsys)
    assert code == 0, out


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("rounds r6-r10", []),
        ("run on 2026-09-30", ["2026", "09", "30"]),
        ("rounds 6-10", ["6", "10"]),
        ("rounds 6-999", ["6", "999"]),
        ("rho −0.5 then +0.866 and -1.0", ["−0.5", "+0.866", "-1.0"]),
        ("delta (-0.07)", ["-0.07"]),
        ("a - 3 spaced dash", ["3"]),
        ("cost -$8.56", ["-$8.56"]),
    ],
)
def test_sign_tokenization(text, expected):
    assert [m.group(0) for m in claims.displayed_numbers(text)] == expected


def test_unicode_and_ascii_minus_are_the_same_display():
    assert claims.canonical("\u22120.40") == claims.canonical("-0.40")
    assert claims.canonical("0.0700") != claims.canonical("0.07")


# ------------------------------------------- Codex review regressions (#962)


def test_ascii_range_end_is_checked(repo, capsys):
    """P1a: '6-10' has two endpoints; editing it to '6-999' must fail."""
    cites = (
        "<!-- claim: 6 source: results/score.json#/headline/rounds/0 -->",
        "<!-- claim: 10 source: results/score.json#/headline/rounds/-1 -->",
    )
    code, out = run(repo, quoted("Rounds 6-10.", *cites), "--strict", capsys=capsys)
    assert code == 0, out
    code, out = run(repo, quoted("Rounds 6-999.", *cites), "--strict", capsys=capsys)
    assert code == 1
    assert "STALE: 10 not found" in out
    assert "UNCITED number 999" in out


def test_date_is_three_positive_numbers_not_negatives():
    tokens = [m.group(0) for m in claims.displayed_numbers("dated 2026-09-30, rounds r6-r10")]
    assert tokens == ["2026", "09", "30"]
    assert all(claims.parse_displayed(t)[0] > 0 for t in tokens)


def test_substituted_value_covered_elsewhere_fails(signed_scores, capsys):
    """P1b: a value cited for one occurrence must not cover another occurrence."""
    cites = (
        '<!-- claim: 1.000 at: "OpenSCAD scored 1.000" source: s.json#/openscad -->',
        '<!-- claim: 0.778 at: "CadQuery scored 0.778" source: s.json#/cadquery -->',
    )
    good = quoted("OpenSCAD scored 1.000. CadQuery scored 0.778.", *cites)
    code, out = run(signed_scores, good, "--strict", capsys=capsys)
    assert code == 0, out
    bad = quoted("OpenSCAD scored 0.778. CadQuery scored 0.778.", *cites)
    code, out = run(signed_scores, bad, "--strict", capsys=capsys)
    assert code == 1
    assert "STALE: context 'OpenSCAD scored 1.000' not found" in out
    assert "UNCITED number 0.778" in out


def test_precision_change_in_text_fails(repo, capsys):
    """P1b: '0.07' displayed as '0.0700' is a different claim."""
    cite = "<!-- claim: 0.07 source: results/score.json#/headline/value -->"
    code, out = run(repo, quoted("rho averaged about 0.0700.", cite), "--strict", capsys=capsys)
    assert code == 1
    assert "STALE: 0.07 not found" in out
    assert "UNCITED number 0.0700" in out


def test_repeated_value_without_context_is_ambiguous(repo, capsys):
    body = quoted("6 rounds, from 6 to 10.", "<!-- claim: 6 source: results/score.json#/headline/rounds/0 -->")
    code, out = run(repo, body, capsys=capsys)
    assert code == 1
    assert "AMBIGUOUS: 6 occurs 2 times" in out


def test_two_sources_can_back_one_occurrence(signed_scores, capsys):
    body = quoted(
        "The re-run scored 1.000 for both.",
        '<!-- claim: 1.000 at: "scored 1.000 for both" source: s.json#/openscad -->',
        '<!-- claim: 1.000 at: "scored 1.000 for both" source: s.json#/after -->',
    )
    code, out = run(signed_scores, body, "--strict", capsys=capsys)
    assert code == 0, out
    assert "2 matched" in out


def test_blockquote_body_is_scanned(repo, capsys):
    """P2a: blockquotes count as quoted bodies under --strict."""
    cite = '<!-- claim: 0.07 at: "rho ≈ 0.07" source: results/score.json#/headline/value -->'
    good = "## Notes\n\n> Mean rho ≈ 0.07 here.\n\n" + cite + "\n"
    code, out = run(repo, good, "--strict", capsys=capsys)
    assert code == 0, out
    bad = "## Notes\n\n> Mean rho ≈ 0.99 here.\n\n" + cite + "\n"
    code, out = run(repo, bad, "--strict", capsys=capsys)
    assert code == 1
    assert "STALE" in out
    assert "UNCITED number 0.99" in out
    uncited = "## Notes\n\n> Passed 5 of 6 checks.\n"
    code, out = run(repo, uncited, "--strict", capsys=capsys)
    assert code == 1
    assert "UNCITED number 5" in out


def test_nocheck_context_covers_its_numbers(repo, capsys):
    body = quoted("Built 4 July with Claude Fable 5.", '<!-- nocheck: "4 July", "Fable 5" reason: date and model name -->')
    code, out = run(repo, body, "--strict", capsys=capsys)
    assert code == 0, out
    body = quoted("Built 4 July.", '<!-- nocheck: "5 July" reason: stale -->')
    code, out = run(repo, body, capsys=capsys)
    assert code == 1
    assert "nocheck context '5 July' not found" in out


def test_rfc6901_root_and_empty_key():
    """P2b: '' is the root document; '/' selects the '' key."""
    assert claims.resolve_json(7, "") == 7
    assert claims.resolve_json({"": 3, "a": 1}, "/") == 3
    with pytest.raises(claims.SourceError):
        claims.resolve_json({"a": 1}, "/")
    assert claims.resolve_json({"a/b": 2, "m~n": 4}, "/a~1b") == 2
    assert claims.resolve_json({"a/b": 2, "m~n": 4}, "/m~0n") == 4


def test_default_targets_are_the_case_studies():
    targets = {p.relative_to(REPO_ROOT).as_posix() for p in claims.default_targets(REPO_ROOT)}
    case_studies = {p.relative_to(REPO_ROOT).as_posix() for p in REPO_ROOT.glob("docs/showcase/*/CASE_STUDY.md")}
    assert "docs/showcase/sambuca/CASE_STUDY.md" in case_studies
    assert case_studies <= targets


def test_default_targets_include_cited_docs_and_uncited_case_studies(tmp_path):
    show = tmp_path / "docs" / "showcase"
    (show / "a").mkdir(parents=True)
    (show / "b").mkdir()
    (show / "a" / "CASE_STUDY.md").write_text("## x\n\nNo citations.\n", encoding="utf-8")
    (show / "b" / "notes.md").write_text("## x\n\n<!-- nocheck: 4 reason: date -->\n", encoding="utf-8")
    (show / "b" / "plain.md").write_text("## x\n\nNothing here.\n", encoding="utf-8")
    targets = {p.relative_to(tmp_path).as_posix() for p in claims.default_targets(tmp_path)}
    assert targets == {"docs/showcase/a/CASE_STUDY.md", "docs/showcase/b/notes.md"}


def test_real_case_studies_carry_checked_citations(capsys):
    code = claims.main(["--root", str(REPO_ROOT), "--strict"])
    out = capsys.readouterr().out
    assert code == 0, out
    matched = int(re.search(r"(\d+) matched", out).group(1))
    assert matched > 0, out


@pytest.fixture()
def signed_scores(tmp_path: Path) -> Path:
    (tmp_path / "s.json").write_text(
        json.dumps({"openscad": 1.0, "cadquery": 0.777778, "after": 1.0}), encoding="utf-8"
    )
    return tmp_path


@pytest.mark.parametrize("indent", [" ", "  ", "   "])
def test_indented_blockquote_is_scanned(repo, capsys, indent):
    """Codex re-review P2: up to three spaces before '>' is still a blockquote."""
    body = f"## Notes\n\n{indent}> Claimed pass rate: 0.99.\n"
    code, out = run(repo, body, "--strict", capsys=capsys)
    assert code == 1
    assert "UNCITED number 0.99" in out


def test_four_space_indent_is_code_not_quote(repo, capsys):
    body = "## Notes\n\n    > Claimed pass rate: 0.99.\n"
    code, out = run(repo, body, "--strict", capsys=capsys)
    assert code == 0, out


def test_lazy_continuation_line_is_scanned(repo, capsys):
    """Codex re-review P2: an unprefixed line after a '>' line stays in the quote."""
    body = "## Notes\n\n> The headline:\nClaimed pass rate: 0.99.\nand 42 more.\n\nOutside 7.\n"
    code, out = run(repo, body, "--strict", capsys=capsys)
    assert code == 1
    assert "UNCITED number 0.99" in out
    assert "UNCITED number 42" in out
    assert "UNCITED number 7" not in out  # the blank line ended the quote


def test_lazy_continuation_stops_at_new_block_or_empty_quote_line(repo, capsys):
    body = (
        "## Notes\n\n> Quoted text.\n- list item 5\n\n"
        "> Quoted text.\n>\nNew paragraph 9.\n\n"
        "> Quoted text.\n### Heading 3\n"
    )
    code, out = run(repo, body, "--strict", capsys=capsys)
    assert code == 0, out


def test_mixed_marker_line_is_not_a_thematic_break(repo, capsys):
    """Codex round 3: '-_-' mixes markers, so it is lazy text and the quote continues."""
    body = "## Notes\n\n> The headline:\n-_-\nClaimed pass rate: 0.99.\n"
    code, out = run(repo, body, "--strict", capsys=capsys)
    assert code == 1
    assert "UNCITED number 0.99" in out


@pytest.mark.parametrize("rule", ["---", "***", "_ _ _", "  ___", "- - -", "*\t*\t*"])
def test_same_marker_thematic_break_ends_the_quote(repo, capsys, rule):
    body = f"## Notes\n\n> The headline:\n{rule}\nClaimed pass rate: 0.99.\n"
    code, out = run(repo, body, "--strict", capsys=capsys)
    assert code == 0, out


# ------------------------------- real-doc mutations must fail under --strict


@pytest.mark.parametrize(
    ("doc", "before", "after"),
    [
        ("docs/showcase/djembe/CASE_STUDY.md", "| Objective pass rate | 0.833 |", "| Objective pass rate | 0.999 |"),
        ("docs/showcase/kora/CASE_STUDY.md", "| 1.000 (0.833 first", "| 0.833 (0.833 first"),
        ("docs/showcase/kora/CASE_STUDY.md", "(0.833 first published)", "(0.944 first published)"),
        ("docs/showcase/post3/matchup-backend.md", "| CadQuery | 0.778 |", "| CadQuery | 0.889 |"),
        ("docs/showcase/post3/matchup-backend.md", "| 0.778 | 0.889 |", "| 0.778 | 0.778 |"),
        ("docs/showcase/strings/matchup-model.md", "| 1.000 | nothing | 0.778", "| 0.778 | nothing | 0.778"),
        ("docs/showcase/strings/matchup-model.md", "| nothing | 0.778 |", "| nothing | 0.878 |"),
        ("docs/showcase/sambuca/CASE_STUDY.md", "= 5 of 6 sub-gates", "= 6 of 6 sub-gates"),
        ("docs/showcase/sambuca/CASE_STUDY.md", "`min_wall`: 0.0149 mm measured", "`min_wall`: 99.0 mm measured"),
    ],
)
def test_mutating_a_cited_result_fails_strict(tmp_path, capsys, doc, before, after):
    text = (REPO_ROOT / doc).read_text(encoding="utf-8")
    # The first occurrence must be visible text, not a copy inside a citation comment.
    first = text.index(before)
    assert text.rfind("<!--", 0, first) <= text.rfind("-->", 0, first)
    assert claims.main([str(REPO_ROOT / doc), "--root", str(REPO_ROOT), "--strict"]) == 0
    mutated = tmp_path / "mutated.md"
    mutated.write_text(text.replace(before, after, 1), encoding="utf-8")
    capsys.readouterr()
    code = claims.main([str(mutated), "--root", str(REPO_ROOT), "--strict"])
    out = capsys.readouterr().out
    assert code == 1, out
    assert "STALE" in out or "MISMATCH" in out
