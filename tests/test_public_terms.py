"""Synthetic-only negative controls for the private-term publication guard."""

import hashlib
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_public_terms", ROOT / "scripts/check_public_terms.py")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def fingerprints(*terms):
    return frozenset(hashlib.sha256(term.lower().encode()).hexdigest() for term in terms)


def write_denylist(path, *terms, comments=True):
    lines = ["# synthetic test denylist", ""] if comments else []
    lines += sorted(fingerprints(*terms))
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


@pytest.mark.parametrize("text", ["SyntheticForbidden", "SYNTHETICFORBIDDEN", "syntheticforbidden"])
def test_tokens_are_lowercased(text):
    assert checker.matched_lines("safe\n" + text, fingerprints("syntheticforbidden")) == [2]


@pytest.mark.parametrize("text", ["Synthetic-Widget", "SYNTHETIC WIDGET", "synthetic_widget",
                                 "synthetic\nwidget", "SyntheticWidget", "**Synthetic** widget",
                                 '"synthetic-widget": 1', "<b>synthetic</b>&nbsp;widget",
                                 "Synthetic&#45;Widget", "synthetic\\u0020widget"])
def test_hyphen_joined_pairs_are_lowercased(text):
    assert checker.matched_lines(text, fingerprints("synthetic-widget")) == [1]


@pytest.mark.parametrize("text", ["syntheticforbiddenextra", "presyntheticforbidden",
                                  "synthetic forbidden", "SyntheticForbiddenExtra"])
def test_substrings_and_near_misses_do_not_match(text):
    assert checker.matched_lines(text, fingerprints("syntheticforbidden")) == []


def test_camel_case_part_matches_single_token_term():
    assert checker.matched_lines("x\nAcmeSyntheticforbiddenKit", fingerprints("syntheticforbidden")) == [2]


def test_multi_word_ngram_up_to_max():
    term = "-".join(["alpha", "synthetic", "gamma", "delta"][:checker.MAX_NGRAM])
    text = " ".join(term.split("-")).title()
    assert checker.matched_lines("ok\n" + text, fingerprints(term)) == [2]
    assert checker.matched_lines("alpha synthetic other delta", fingerprints(term)) == []


def test_fingerprint_helper_matches_canonical_form():
    assert checker.fingerprint("Synthetic  Widget") in fingerprints("synthetic-widget")


def test_positive_and_negative_control_against_temp_denylist(tmp_path, capsys):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "page.md").write_text("Nothing private here.", encoding="utf-8")
    (tmp_path / "README.md").write_text("Safe readme.", encoding="utf-8")
    denylist = write_denylist(tmp_path / "hashes.txt", "zyxsyntheticterm")
    assert checker.main(["--root", str(tmp_path), "--denylist", str(denylist)]) == 0
    (tmp_path / "docs" / "page.md").write_text("line\nUses ZyxSyntheticTerm.", encoding="utf-8")
    assert checker.main(["--root", str(tmp_path), "--denylist", str(denylist)]) == 1
    output = capsys.readouterr().out
    assert "docs/page.md:2: forbidden term fingerprint" in output
    assert "ZyxSyntheticTerm".lower() not in output.lower()


def test_private_and_out_of_scope_files_are_not_scanned(tmp_path):
    (tmp_path / "README.md").write_text("safe", encoding="utf-8")
    for rel in ["tests/test_x.py", "makerbench/mod.py", "docs/image.png"]:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SyntheticForbidden", encoding="utf-8")
    assert checker.check(tmp_path, fingerprints("syntheticforbidden")) == []


@pytest.mark.parametrize("relative", ["README.md", "docs/nested/draft.md", "site/data/leak.json",
                                     "site/nested/page.html", "site/assets/leak.js",
                                     "templates/foo/README.md", "CONTRIBUTING.md", "CITATION.cff"])
def test_each_public_surface_is_checked_without_echoing_terms(tmp_path, relative, capsys):
    (tmp_path / "README.md").write_text("safe", encoding="utf-8")
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("SyntheticForbidden", encoding="utf-8")
    denylist = write_denylist(tmp_path / "hashes.txt", "syntheticforbidden")
    assert checker.main(["--root", str(tmp_path), "--denylist", str(denylist)]) == 1
    output = capsys.readouterr().out
    assert "forbidden term fingerprint" in output
    assert "SyntheticForbidden" not in output


@pytest.mark.parametrize("text", ["", "# only a comment\n", "not-a-hash", "a" * 63, "A" * 64,
                                  "a" * 64 + " plaintext", "a" * 65])
def test_invalid_or_empty_denylist_is_rejected(tmp_path, text):
    path = tmp_path / "hashes.txt"
    path.write_text(text, encoding="ascii")
    with pytest.raises(ValueError):
        checker.load_hashes(path)


def test_committed_denylist_holds_only_hashes_and_comments():
    lines = checker.DENYLIST.read_text(encoding="ascii").splitlines()
    entries = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
    assert entries and all(checker.HASH_LINE.fullmatch(line) for line in entries)
    assert len(entries) == len(set(entries))
    assert checker.load_hashes(checker.DENYLIST) == frozenset(entries)


def test_ci_runs_the_checker():
    assert "scripts/check_public_terms.py" in (ROOT / ".github/workflows/ci.yml").read_text()


def test_committed_public_text_has_no_denied_fingerprints():
    assert checker.check(ROOT, checker.load_hashes(checker.DENYLIST)) == []
