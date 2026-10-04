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


@pytest.mark.parametrize("text", ["SyntheticForbidden", "SYNTHETICFORBIDDEN", "syntheticforbidden"])
def test_tokens_are_lowercased(text):
    assert checker.matched_lines("safe\n" + text, fingerprints("syntheticforbidden")) == [2]


@pytest.mark.parametrize("text", ["Synthetic-Widget", "SYNTHETIC WIDGET", "synthetic_widget",
                                 "synthetic\nwidget"])
def test_hyphen_joined_pairs_are_lowercased(text):
    assert checker.matched_lines(text, fingerprints("synthetic-widget")) == [1]


def test_substrings_do_not_match():
    assert checker.matched_lines("syntheticforbiddenextra", fingerprints("syntheticforbidden")) == []


@pytest.mark.parametrize("relative", ["README.md", "docs/nested/draft.md", "site/data/leak.json",
                                     "site/nested/page.html", "site/assets/leak.js"])
def test_each_public_surface_is_checked_without_echoing_terms(tmp_path, relative, capsys):
    (tmp_path / "README.md").write_text("safe", encoding="utf-8")
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("SyntheticForbidden", encoding="utf-8")
    denylist = tmp_path / "hashes.txt"
    denylist.write_text(next(iter(fingerprints("syntheticforbidden"))) + "\n", encoding="ascii")
    assert checker.main(["--root", str(tmp_path), "--denylist", str(denylist)]) == 1
    output = capsys.readouterr().out
    assert "forbidden term fingerprint" in output
    assert "SyntheticForbidden" not in output


@pytest.mark.parametrize("text", ["", "not-a-hash", "a" * 63, "A" * 64])
def test_invalid_or_empty_denylist_is_rejected(tmp_path, text):
    path = tmp_path / "hashes.txt"
    path.write_text(text, encoding="ascii")
    with pytest.raises(ValueError):
        checker.load_hashes(path)


def test_committed_public_text_has_no_denied_fingerprints():
    assert checker.check(ROOT, checker.load_hashes(checker.DENYLIST)) == []
