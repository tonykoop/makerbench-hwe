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


# P1a: JSON escape separators and formatting inside a word must not hide a term.
@pytest.mark.parametrize("text", ['"synthetic\\nwidget"', '"synthetic\\twidget"', '"synthetic\\rwidget"',
                                  '"synthetic\\/widget"', '"synthetic\\"widget"', '"Synthetic\\u002DWidget"'])
def test_json_escape_separators_are_decoded(text):
    assert checker.matched_lines("{}\n" + text, fingerprints("synthetic-widget")) == [2]


@pytest.mark.parametrize("text", ["**Syn**thetic", "Syn<b>thetic</b>", "Syn_thetic_", "*Syn*thetic",
                                  "Syn<span class=\"x\">thet</span>ic", "`Syn`thetic", "~~Syn~~thetic",
                                  "Syn&lt;b&gt;thetic&lt;/b&gt;", "Syn\\u003cb\\u003ethetic"])
def test_formatting_inside_a_word_is_rejoined(text):
    assert checker.matched_lines("ok\n" + text, fingerprints("synthetic")) == [2]


@pytest.mark.parametrize("text", ["Syn<!-- note -->thetic", "Syn<!-- a -->the<!--b-->tic",
                                  "[Syn](https://example.invalid)thetic", "Syn<STRONG>thetic</STRONG>",
                                  "Syn<a href=\"x\">thetic</a>", "Syn<em>the</em><code>tic</code>"])
def test_comments_links_and_inline_tags_join(text):
    assert checker.matched_lines(text, fingerprints("synthetic")) == [1]


@pytest.mark.parametrize("text", ["[Synthetic Widget](https://example.invalid/x)",
                                  "![Synthetic widget](img/x.png)", "see [**Synthetic** widget](u)"])
def test_markdown_link_and_image_text_is_checked(text):
    assert checker.matched_lines(text, fingerprints("synthetic-widget")) == [1]


@pytest.mark.parametrize("text", ["**Syn** thetic", "Syn <b>thetic</b>", "<p>Syn</p>\n<p>thetic</p>",
                                  "Syn<br>thetic", "Syn<br/>thetic", "<p>Syn</p><p>thetic</p>",
                                  "Syn<div>thetic</div>", "<li>Syn</li><li>thetic</li>",
                                  "<td>Syn</td><td>thetic</td>", "<h2>Syn</h2>thetic", "Syn<hr>thetic",
                                  "Syn<bdi>thetic</bdi>"])
def test_genuine_word_boundaries_are_not_merged(text):
    assert checker.matched_lines(text, fingerprints("synthetic")) == []


def test_multiline_tag_keeps_line_numbers():
    text = 'a\n<span\n  class="x">Syn</span>thetic\nend'
    assert checker.matched_lines(text, fingerprints("synthetic")) == [3]


def test_terms_in_tag_attributes_are_seen():
    assert checker.matched_lines('<img alt="Synthetic Widget">', fingerprints("synthetic-widget")) == [1]


# P2b: CamelCase is split before n-gram windows, so mixed forms join up.
@pytest.mark.parametrize("text", ["SyntheticWidget Kit", "Synthetic WidgetKit", "SyntheticWidgetKit",
                                  "syntheticWidget-kit", "**SyntheticWidget** kit", "Synthetic_WidgetKit"])
def test_camel_case_parts_form_ngrams_across_tokens(text):
    assert checker.matched_lines(text, fingerprints("synthetic-widget-kit")) == [1]


@pytest.mark.parametrize("text", ["SyntheticWidget SpareKit", "SyntheticWidget Spare Kit",
                                  "SyntheticWidget spare-kit", "syntheticwidget SpareKit",
                                  "Acme SyntheticWidget SpareKit"])
def test_mixed_whole_and_split_camel_case_windows(text):
    assert checker.matched_lines(text, fingerprints("syntheticwidget-spare-kit")) == [1]


def test_mixed_windows_stay_bounded():
    assert checker.matched_lines("SyntheticWidget One Two ThreeFour",
                                 fingerprints("synthetic-widget-one-two-three")) == []


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


def _private_tree(tmp_path):
    (tmp_path / "README.md").write_text("safe", encoding="utf-8")
    (tmp_path / ".gitmodules").write_text(
        '[submodule "vendored/sub"]\n\tpath = vendored/sub\n\turl = git@example.invalid:x.git\n',
        encoding="utf-8")
    for rel in ["private/oracles/README.md", "private/submissions/README.md",
                "vendored/sub/README.md", "docs/private/README.md",
                "node_modules/pkg/README.md", ".venv/lib/README.md", ".git/README"]:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SyntheticForbidden", encoding="utf-8")


# P1b: private/ and submodule trees are pruned before traversal and never opened.
def test_private_and_submodule_readmes_are_never_opened(tmp_path, monkeypatch):
    _private_tree(tmp_path)
    blocked = [tmp_path / "private", tmp_path / "vendored" / "sub", tmp_path / "node_modules",
               tmp_path / ".venv", tmp_path / ".git"]
    real_scandir = checker.os.scandir

    def guarded_scandir(path="."):
        assert not any(Path(path) == b or b in Path(path).parents for b in blocked), path
        return real_scandir(path)

    real_read_bytes = Path.read_bytes

    def guarded_read_bytes(self):
        assert not any(self == b or b in self.parents for b in blocked), self
        return real_read_bytes(self)

    monkeypatch.setattr(checker.os, "scandir", guarded_scandir)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    paths = checker.public_paths(tmp_path)
    assert all(not any(b in p.parents for b in blocked) for p in paths)
    # docs/private is ordinary public docs (only the top-level private/ tree is excluded).
    problems = checker.check(tmp_path, fingerprints("syntheticforbidden"))
    assert problems == ["docs/private/README.md:1: forbidden term fingerprint"]


@pytest.mark.parametrize("use_git", [True, False])
def test_quoted_submodule_path_is_never_traversed(tmp_path, monkeypatch, use_git):
    (tmp_path / "README.md").write_text("safe", encoding="utf-8")
    (tmp_path / ".gitmodules").write_text(
        '[submodule "vendor"]\n\tpath = "docs/vendor"  \n\turl = git@example.invalid:x.git\n',
        encoding="utf-8")
    for rel in ["docs/vendor/README.md", "docs/vendor/page.md"]:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SyntheticForbidden", encoding="utf-8")
    (tmp_path / "docs" / "ok.md").write_text("fine", encoding="utf-8")
    if not use_git:
        def no_git(*args, **kwargs):
            raise OSError("git unavailable")
        monkeypatch.setattr(checker.subprocess, "run", no_git)
    assert "docs/vendor" in checker.excluded_prefixes(tmp_path)
    blocked = tmp_path / "docs" / "vendor"
    real_scandir, real_read_bytes = checker.os.scandir, Path.read_bytes

    def guarded_scandir(path="."):
        assert not (Path(path) == blocked or blocked in Path(path).parents), path
        return real_scandir(path)

    def guarded_read_bytes(self):
        assert blocked not in self.parents, self
        return real_read_bytes(self)

    monkeypatch.setattr(checker.os, "scandir", guarded_scandir)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    assert checker.check(tmp_path, fingerprints("syntheticforbidden")) == []


@pytest.mark.parametrize("relative", ["README.md", "docs/nested/draft.md", "site/data/leak.json",
                                     "site/nested/page.html", "site/assets/leak.js",
                                     "templates/foo/README.md", "CONTRIBUTING.md", "CITATION.cff",
                                     "docs/data/table.tsv", "docs/scripts/run.sh", "docs/notes.rst",
                                     "docs/data/raw.dat", "docs/LICENSE", "site/feed.atom"])
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


def test_binary_files_are_skipped_by_content(tmp_path):
    (tmp_path / "README.md").write_text("safe", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "blob.dat").write_bytes(b"\0\x01SyntheticForbidden\0")
    assert checker.check(tmp_path, fingerprints("syntheticforbidden")) == []


# P2a: every plain-text format actually present under docs/ and site/ is scanned.
def test_every_text_suffix_present_in_public_dirs_is_scanned():
    scanned = {p.suffix.lower() for p in checker.public_paths(ROOT)}
    present = {p.suffix.lower() for d in checker.PUBLIC_DIRS for p in (ROOT / d).rglob("*")
               if p.is_file() and "__pycache__" not in p.parts}
    assert present - checker.BINARY_SUFFIXES <= scanned


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


# #964: multi-line HTML comments inside a word; partial CamelCase joins.
@pytest.mark.parametrize("text", ["Syn<!--\nnote\n-->thetic", "Syn<!-- a\n-->the<!--\nb -->tic",
                                  "Syn<!--\n-->the<b>tic</b>", "**Syn**<!--\n\n-->thetic"])
def test_multiline_comment_inside_a_word_is_rejoined(text):
    assert checker.matched_lines("ok\n" + text, fingerprints("synthetic")) == [2]


def test_multiline_comment_keeps_later_line_numbers():
    text = "Syn<!--\n\n-->thetic tail\nmid\nSynthetic\n<!--\n-->\nSynthetic"
    assert checker.matched_lines(text, fingerprints("synthetic")) == [1, 5, 8]


def test_multiline_comment_between_words_still_separates():
    assert checker.matched_lines("Syn <!--\n--> thetic", fingerprints("synthetic")) == []
    assert checker.matched_lines("Syn<!--\n-->\nthetic", fingerprints("synthetic")) == []


@pytest.mark.parametrize("text", ["AcmeSyntheticWidget SpareKit", "AcmeSyntheticWidget spare kit",
                                  "xAcmeSyntheticWidget spare-kit", "AcmeSyntheticWidget SpareKitPlus",
                                  "Acme_SyntheticWidget Spare_Kit"])
def test_partial_camel_case_suffix_reads_as_one_word(text):
    assert checker.matched_lines(text, fingerprints("syntheticwidget-spare-kit")) == [1]


@pytest.mark.parametrize("text", ["Spare SyntheticWidgetAcme", "spare-SyntheticWidgetAcmeKit"])
def test_partial_camel_case_prefix_reads_as_one_word(text):
    assert checker.matched_lines(text, fingerprints("spare-syntheticwidget")) == [1]


@pytest.mark.parametrize("text", ["Synthetic Widget Spare Kit", "AcmeSynthetic WidgetSpareKit",
                                  "Synthetic WidgetSpare Kit"])
def test_partial_camel_joins_do_not_merge_separate_words(text):
    # Only parts of ONE CamelCase token may be read as one word.
    assert checker.matched_lines(text, fingerprints("syntheticwidget-spare-kit")) == []


# #1002 review repros.
@pytest.mark.parametrize("text", ['Syn<!--\n--><span class="x">thetic</span>',
                                  'Syn<!--\n--><a\n href="u">the</a>tic'])
def test_multiline_comment_then_attribute_tag_inside_a_word(text):
    assert checker.matched_lines(text, fingerprints("synthetic")) == [1]


@pytest.mark.parametrize("text,lines", [
    ("<!--\n-->**Syn**thetic", [2]),
    ("Syn<!--\n-->thetic,**Syn**thetic", [1, 2]),
    ("a\n<!--\n\n-->Syn<b\n>thetic</b> x\nSynthetic", [4, 6]),
    ('<span\n  class="x">Syn</span>thetic <!--\n--> Synthetic', [2, 3]),
])
def test_joined_view_reports_each_token_on_its_source_line(text, lines):
    assert checker.matched_lines(text, fingerprints("synthetic")) == lines


@pytest.mark.parametrize("text", ["one two three SyntheticWidgetAcme", "one two-three SyntheticWidgetAcmeKit"])
def test_camel_prefix_join_counts_as_one_word_toward_the_limit(text):
    assert checker.matched_lines(text, fingerprints("one-two-three-syntheticwidget")) == [1]


def test_camel_prefix_join_still_respects_the_word_limit():
    assert checker.matched_lines("one two three four SyntheticWidgetAcme",
                                 fingerprints("one-two-three-four-syntheticwidget")) == []


# #1002 round 3: quoted ">" inside tag attributes.
@pytest.mark.parametrize("text", ['Syn<!--\n--><span title="a>b">thetic</span>',
                                  "Syn<!--\n--><span title='a>b'>thetic</span>",
                                  'Syn<span title="a>b">thetic</span>',
                                  "Syn<b data-x='>'>the</b>tic"])
def test_quoted_gt_in_tag_attribute_does_not_split_the_word(text):
    assert checker.matched_lines(text, fingerprints("synthetic")) == [1]


@pytest.mark.parametrize("text", ['<p title="a>b">Syn</p>thetic', "<div class='x>y'>Syn</div>thetic"])
def test_quoted_gt_in_block_tag_still_separates(text):
    assert checker.matched_lines(text, fingerprints("synthetic")) == []


def test_unbalanced_apostrophe_in_prose_does_not_swallow_text():
    text = "if a<b and c's\nnext line\nSynthetic here, it's fine>"
    assert checker.matched_lines(text, fingerprints("synthetic")) == [3]
