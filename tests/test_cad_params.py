"""Parameter extraction and rewriting for OpenSCAD and CadQuery sources (#788 W1).

Nothing here runs OpenSCAD or Python candidate code. The sweep over the real
instrument masters runs only when ``MAKERBENCH_INSTRUMENTS_ROOT`` points at a
checkout; synthetic fixtures cover every pattern the masters use.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from makerbench import cad_params
from makerbench.cad_params import (
    ParameterError,
    apply_parameters,
    changed_values,
    extract_parameters,
)

UKULELE_LIKE = '''// Ukulele parametric master.
//
// Units: inches.

/* [Variant] */
// 0=soprano, 1=concert, 2=tenor, 3=baritone
variant = 1;

// Scale lengths per size family.
scale_lengths_in = [13.5, 15.0, 17.0, 19.0];
scale_length_in  = scale_lengths_in[variant];

/* [Strings and fretboard] */
string_count       = 4;      // G4-C4-E4-A4 reentrant
fret_count         = 18;     // [12:24]
nut_width_in       = 1.4375; // design table row 20
tuning             = "gCEA"; // [gCEA, ADF#B]
show_frets         = true;
label = "semi;colon \\"quoted\\""; // strings may hold ; and =
neck_angle_deg     = 1.5;    // [0:0.25:5] back tilt
wall_mm = 3; // unit: mm  shell wall

/* [Neck] */
neck_joint_fret    = 14;     // body-joint fret

function fret_from_nut(n) = scale_length_in * (1 - pow(2, -n / 12));

neck_length_in = fret_from_nut(neck_joint_fret);       // nut to body joint

module body_shell() {
  inner = 5;  // not top level
  difference() {
    cube([body_length_in, 7.25, 2.75], center = true);
  }
}

module bridge() cube(1);

body_length_in = 11.25;
body_length_in = 12.0; // reassigned: OpenSCAD warns and keeps the last
$fn = 96;
mixed = [1, "a"];
nested = [[1, 2], [3, 4]];
neg_mm = -2.5e-1;

translate([0, 0, 0]) body_shell();
for (n = [1 : fret_count])
  echo(str("fret_", n, "=", fret_from_nut(n)));
'''


def _names(model):
    return [p.name for p in model.parameters]


class TestOpenSCADExtraction:
    def test_finds_only_top_level_assignments(self):
        model = extract_parameters(UKULELE_LIKE, "openscad")
        names = _names(model)
        assert "inner" not in names, "module-body assignment leaked"
        assert "n" not in names
        assert names[:3] == ["variant", "scale_lengths_in", "scale_length_in"]
        assert "body_length_in" in names and "$fn" in names
        assert model.limitations == []

    def test_kinds_values_and_editability(self):
        by = extract_parameters(UKULELE_LIKE, "openscad").by_name()
        assert by["variant"].kind == "number" and by["variant"].value == 1
        assert by["nut_width_in"].value == 1.4375
        assert by["scale_lengths_in"].kind == "vector"
        assert by["scale_lengths_in"].value == [13.5, 15.0, 17.0, 19.0]
        assert by["tuning"].kind == "string" and by["tuning"].value == "gCEA"
        assert by["label"].value == 'semi;colon "quoted"'
        assert by["show_frets"].kind == "bool" and by["show_frets"].value is True
        assert by["neg_mm"].value == -0.25
        for name in ("scale_length_in", "neck_length_in", "mixed", "nested"):
            assert by[name].kind == "derived" and not by[name].editable, name
            assert "derived" in by[name].notes[0]
        assert by["scale_length_in"].raw == "scale_lengths_in[variant]"

    def test_reassigned_names_are_reported_and_not_editable(self):
        by = extract_parameters(UKULELE_LIKE, "openscad").by_name()
        assert by["body_length_in"].state == "reassigned"
        assert by["body_length_in"].editable is False
        assert any("more than once" in n for n in by["body_length_in"].notes)

    def test_special_variables_are_editable_and_flagged(self):
        by = extract_parameters(UKULELE_LIKE, "openscad").by_name()
        assert by["$fn"].state == "special" and by["$fn"].editable and by["$fn"].value == 96

    def test_groups_docs_units_ranges_and_options(self):
        by = extract_parameters(UKULELE_LIKE, "openscad").by_name()
        assert by["variant"].group == "Variant"
        assert by["variant"].doc == "0=soprano, 1=concert, 2=tenor, 3=baritone"
        assert by["string_count"].group == "Strings and fretboard"
        assert by["string_count"].doc == "G4-C4-E4-A4 reentrant"
        # a trailing comment on the previous line is not this line's doc
        assert by["fret_count"].doc is None
        assert by["fret_count"].range == {"min": 12, "max": 24, "step": None}
        assert by["neck_angle_deg"].range == {"min": 0, "max": 5, "step": 0.25}
        assert by["neck_angle_deg"].doc == "back tilt"
        assert by["tuning"].options == ["gCEA", "ADF#B"]
        assert by["scale_lengths_in"].unit == "in"
        assert by["string_count"].unit == "count"
        assert by["neck_angle_deg"].unit == "deg"
        assert by["wall_mm"].unit == "mm"
        assert by["neck_joint_fret"].group == "Neck"

    def test_honest_states_when_nothing_is_declared(self):
        by = extract_parameters(UKULELE_LIKE, "openscad").by_name()
        assert by["variant"].range is None and by["variant"].unit is None
        assert set(by["variant"].notes) == {"range not declared", "unit unknown"}
        assert by["nut_width_in"].notes == ("range not declared",)
        assert by["fret_count"].notes == ()

    def test_includes_are_a_stated_limitation(self):
        src = "include <MCAD/involute_gears.scad>\nuse <lib.scad>\nteeth = 12;\n"
        model = extract_parameters(src, "openscad")
        assert _names(model) == ["teeth"]
        assert len(model.limitations) == 2
        assert "MCAD/involute_gears.scad" in model.limitations[0]

    def test_empty_and_comment_only_sources(self):
        assert extract_parameters("", "openscad").parameters == []
        assert extract_parameters("// nothing\n/* [G] */\n", "openscad").parameters == []

    def test_crlf_sources_keep_offsets(self):
        src = "a = 1;\r\nb = 2; // [0:5]\r\n"
        by = extract_parameters(src, "openscad").by_name()
        assert src[by["b"].span[0] : by["b"].span[1]] == "2"
        assert by["b"].range == {"min": 0, "max": 5, "step": None}

    def test_equality_operator_is_not_an_assignment(self):
        src = "x = 1;\nif (x == 1) cube(1);\ny = x == 1;\n"
        by = extract_parameters(src, "openscad").by_name()
        assert set(by) == {"x", "y"}
        assert by["y"].kind == "derived"

    def test_parameter_cap_is_a_stated_limitation(self, monkeypatch):
        monkeypatch.setattr(cad_params, "MAX_PARAMETERS", 3)
        src = "".join(f"p{i} = {i};\n" for i in range(6))
        model = extract_parameters(src, "openscad")
        assert len(model.parameters) == 3
        assert any("more than 3" in lim for lim in model.limitations)

    def test_to_dict_is_json_shaped(self):
        payload = extract_parameters(UKULELE_LIKE, "openscad").to_dict()
        assert payload["schema"] == cad_params.SCHEMA
        assert payload["backend"] == "openscad"
        assert payload["editable_count"] == sum(1 for p in payload["parameters"] if p["editable"])
        assert set(payload["parameters"][0]) >= {
            "name", "kind", "value", "raw", "span", "line", "editable", "state", "group",
            "doc", "unit", "range", "options", "notes",
        }


class TestOpenSCADApply:
    def test_noop_apply_is_byte_identical(self):
        model = extract_parameters(UKULELE_LIKE, "openscad")
        values = {p.name: p.value for p in model.parameters if p.editable}
        assert apply_parameters(UKULELE_LIKE, values, "openscad") == UKULELE_LIKE

    def test_rewrites_only_the_literal_span(self):
        out = apply_parameters(
            UKULELE_LIKE,
            {"variant": 2, "nut_width_in": 1.5, "tuning": "ADF#B", "show_frets": False,
             "scale_lengths_in": [13.5, 15, 17, 20]},
            "openscad",
        )
        assert "variant = 1;" not in out and "variant = 2;" in out
        assert "nut_width_in       = 1.5; // design table row 20" in out
        assert 'tuning             = "ADF#B"; // [gCEA, ADF#B]' in out
        assert "show_frets         = false;" in out
        assert "scale_lengths_in = [13.5, 15, 17, 20];" in out
        # everything else survives untouched
        before = UKULELE_LIKE.splitlines()
        after = out.splitlines()
        assert len(before) == len(after)
        changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        assert len(changed) == 5
        # and the result re-extracts to the applied values
        by = extract_parameters(out, "openscad").by_name()
        assert by["variant"].value == 2 and by["tuning"].value == "ADF#B"

    def test_non_ascii_lines_keep_character_spans(self):
        src = 'label = "\u00e9\U0001F3B5"; x_mm = 1; // \u00b0\ny_mm = 2;\n'
        model = extract_parameters(src, "openscad")
        for p in model.parameters:
            assert src[p.span[0] : p.span[1]] == p.raw
        assert apply_parameters(src, {"x_mm": 12, "y_mm": 3}, "openscad") == (
            'label = "\u00e9\U0001F3B5"; x_mm = 12; // \u00b0\ny_mm = 3;\n'
        )

    def test_escapes_strings(self):
        out = apply_parameters(UKULELE_LIKE, {"label": 'say "hi"; x=1'}, "openscad")
        by = extract_parameters(out, "openscad").by_name()
        assert by["label"].value == 'say "hi"; x=1'

    @pytest.mark.parametrize(
        "values, message",
        [
            ({"scale_length_in": 15.0}, "derived"),
            ({"body_length_in": 12.0}, "reassigned"),
            ({"nope": 1}, "no parameter named"),
            ({"variant": "1"}, "needs a number"),
            ({"variant": float("inf")}, "finite"),
            ({"variant": float("nan")}, "finite"),
            ({"variant": True}, "needs a number"),
            ({"fret_count": 30}, "between 12 and 24"),
            ({"tuning": "DGBE"}, "must be one of"),
            ({"tuning": "a\nb"}, "single line"),
            ({"show_frets": 1}, "true or false"),
            ({"scale_lengths_in": [1, 2]}, "keeps its length"),
            ({"scale_lengths_in": [1, 2, 3, "x"]}, "finite numbers only"),
            ({"scale_lengths_in": 5}, "list of numbers"),
        ],
    )
    def test_rejects_bad_values_and_changes_nothing(self, values, message):
        with pytest.raises(ParameterError, match=message):
            apply_parameters(UKULELE_LIKE, values, "openscad")

    def test_changed_values_delta(self):
        before = extract_parameters(UKULELE_LIKE, "openscad")
        out = apply_parameters(UKULELE_LIKE, {"variant": 3, "wall_mm": 4.5}, "openscad")
        after = extract_parameters(out, "openscad")
        assert changed_values(before, after) == {"variant": [1, 3], "wall_mm": [3, 4.5]}


CADQUERY_SCRIPT = '''"""Tongue drum shell."""
import cadquery as cq

# [Shell]
# Outer diameter of the shell.
DIAMETER_MM = 280.0
WALL_MM = 3.0  # [1:0.5:8] shell wall
HEIGHT_MM = 120  # unit: mm
tongue_count = 8  # [6, 8, 10]
NAME = "tongue-drum"
HOLLOW = True
OFFSETS = [1.5, -2.0, 3]
RATIO: float = 0.5
NEG = -4
radius_mm = DIAMETER_MM / 2
label = f"{NAME}-x"
__version__ = "1"

def build():
    inner = 5  # not module level
    return cq.Workplane("XY").circle(radius_mm).extrude(HEIGHT_MM)

WALL_MM = 4.0  # reassigned
result = build()
'''


class TestCadQuery:
    def test_module_level_literals_only(self):
        model = extract_parameters(CADQUERY_SCRIPT, "cadquery")
        names = _names(model)
        assert "inner" not in names and "__version__" not in names
        by = model.by_name()
        assert by["DIAMETER_MM"].value == 280.0 and by["DIAMETER_MM"].unit == "mm"
        assert by["DIAMETER_MM"].group == "Shell"
        assert by["DIAMETER_MM"].doc == "Outer diameter of the shell."
        assert by["WALL_MM"].state == "reassigned" and not by["WALL_MM"].editable
        assert by["HEIGHT_MM"].value == 120 and by["HEIGHT_MM"].unit == "mm"
        assert by["tongue_count"].options == [6, 8, 10] and by["tongue_count"].unit == "count"
        assert by["NAME"].kind == "string" and by["HOLLOW"].value is True
        assert by["OFFSETS"].kind == "vector" and by["OFFSETS"].value == [1.5, -2.0, 3]
        assert by["RATIO"].value == 0.5 and by["RATIO"].editable
        assert by["NEG"].value == -4
        assert by["radius_mm"].kind == "derived" and by["radius_mm"].raw == "DIAMETER_MM / 2"
        assert by["label"].kind == "derived"
        assert by["result"].kind == "derived"
        assert model.limitations == []

    def test_apply_rewrites_python_literals(self):
        out = apply_parameters(
            CADQUERY_SCRIPT,
            {"DIAMETER_MM": 300, "NAME": "it's", "HOLLOW": False, "OFFSETS": [0, 0, 0], "NEG": 2},
            "cadquery",
        )
        assert "DIAMETER_MM = 300\n" in out
        assert 'NAME = "it\'s"\n' in out
        assert "HOLLOW = False\n" in out
        assert "OFFSETS = [0, 0, 0]\n" in out
        assert "NEG = 2\n" in out
        assert "inner = 5" in out
        by = extract_parameters(out, "cadquery").by_name()
        assert by["DIAMETER_MM"].value == 300 and by["NAME"].value == "it's"

    def test_noop_apply_is_byte_identical(self):
        model = extract_parameters(CADQUERY_SCRIPT, "cadquery")
        values = {p.name: p.value for p in model.parameters if p.editable}
        assert apply_parameters(CADQUERY_SCRIPT, values, "cadquery") == CADQUERY_SCRIPT

    def test_syntax_error_is_a_stated_limitation_not_a_crash(self):
        model = extract_parameters("x = (1\n", "cadquery")
        assert model.parameters == []
        assert model.limitations and model.limitations[0].startswith("parse limitation")

    def test_nothing_is_executed(self, tmp_path, monkeypatch):
        marker = tmp_path / "executed"
        src = f"import pathlib\npathlib.Path({str(marker)!r}).write_text('x')\nX = 1\n"
        model = extract_parameters(src, "cadquery")
        assert model.by_name()["X"].value == 1
        assert not marker.exists()

    @pytest.mark.parametrize(
        "src, name, raw, new, expected",
        [
            # 2-byte char before the target on the same line (Sol's repro)
            ('LABEL = "\u00e9"; X = 1\n', "X", "1", 12, 'LABEL = "\u00e9"; X = 12\n'),
            # 4-byte char (emoji) before the target
            ('TAG = "\U0001F3B5\U0001F3B5"; W = 2.5\n', "W", "2.5", 3.0, 'TAG = "\U0001F3B5\U0001F3B5"; W = 3.0\n'),
            # the value itself is non-ASCII and is followed by another target
            ('NAME = "na\u00efve"; N = 7\n', "NAME", '"na\u00efve"', "plain", "NAME = 'plain'; N = 7\n"),
            # non-ASCII in a trailing comment on an earlier line must not leak
            ('A = 1  # \u00fcber\nB = 2\n', "B", "2", 3, 'A = 1  # \u00fcber\nB = 3\n'),
        ],
    )
    def test_non_ascii_lines_keep_character_spans(self, src, name, raw, new, expected):
        model = extract_parameters(src, "cadquery")
        param = model.by_name()[name]
        assert param.raw == raw
        assert src[param.span[0] : param.span[1]] == raw
        assert apply_parameters(src, {name: new}, "cadquery") == expected
        # every span in the model slices back to its own raw text
        for p in model.parameters:
            assert src[p.span[0] : p.span[1]] == p.raw

    def test_non_ascii_source_round_trips_byte_identically(self):
        src = 'T = "\u00e9\u00e8\u00ea"; X = 1\nY = [1, 2]  # \u00b0\n'
        model = extract_parameters(src, "cadquery")
        values = {p.name: p.value for p in model.parameters if p.editable}
        assert apply_parameters(src, values, "cadquery") == src

    def test_tuple_literal_keeps_its_shape(self):
        out = apply_parameters("SIZE = (1, 2)\nONE = (5,)\n", {"SIZE": [3, 4], "ONE": [6]}, "cadquery")
        assert out == "SIZE = (3, 4)\nONE = (6,)\n"


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError, match="unknown backend"):
        extract_parameters("x = 1;", "blender")


#: Repos whose master directory is spelled ``CAD/`` in the instrument
#: checkout. A case-sensitive ``cad/*.scad`` glob silently skipped all eight.
UPPERCASE_CAD_REPOS = frozenset({
    "ashiko-drum-workshop", "conga", "djembe", "dundun",
    "didgeridoo", "flutes", "fujara", "pistalka",
})


class TestFindMasters:
    def test_finds_cad_and_CAD_case_insensitively_and_sorted(self, tmp_path):
        lower = tmp_path / "strings" / "ukulele" / "cad"
        upper = tmp_path / "percussion" / "conga" / "CAD"
        mixed = tmp_path / "woodwind" / "flutes" / "Cad"
        for d in (lower, upper, mixed):
            d.mkdir(parents=True)
        (lower / "ukulele.scad").write_text("a = 1;\n")
        (lower / "notes.md").write_text("not a master\n")
        (upper / "conga.SCAD").write_text("b = 2;\n")
        (mixed / "flute.scad").write_text("c = 3;\n")
        (tmp_path / "strings" / "ukulele" / "docs").mkdir()
        (tmp_path / "strings" / "ukulele" / "docs" / "x.scad").write_text("not under cad\n")
        (tmp_path / ".git").mkdir()
        found = cad_params.find_masters(tmp_path)
        assert found == sorted([lower / "ukulele.scad", upper / "conga.SCAD", mixed / "flute.scad"])

    def test_symlinked_repo_dirs_are_not_followed_and_missing_root_raises(self, tmp_path):
        real = tmp_path / "elsewhere" / "cad"
        real.mkdir(parents=True)
        (real / "m.scad").write_text("x = 1;\n")
        (tmp_path / "strings").mkdir()
        (tmp_path / "strings" / "link").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
        assert cad_params.find_masters(tmp_path) == []
        with pytest.raises(FileNotFoundError):
            cad_params.find_masters(tmp_path / "nope")


@pytest.mark.skipif(
    not os.environ.get("MAKERBENCH_INSTRUMENTS_ROOT"),
    reason="set MAKERBENCH_INSTRUMENTS_ROOT to sweep the instrument masters",
)
def test_every_instrument_master_round_trips_byte_identically():
    root = Path(os.environ["MAKERBENCH_INSTRUMENTS_ROOT"])
    masters = cad_params.find_masters(root)
    assert masters, f"no cad/*.scad under {root}"
    # Independent count: walk every ``<family>/<repo>`` and count ``.scad``
    # files in any directory spelled ``cad`` in any case. Discovery must
    # match it exactly, so an omission cannot stay green.
    independent = 0
    for family in root.iterdir():
        if not family.is_dir() or family.name.startswith("."):
            continue
        for repo in family.iterdir():
            if not repo.is_dir() or repo.name.startswith("."):
                continue
            for child in repo.iterdir():
                if child.name.lower() == "cad" and child.is_dir():
                    independent += sum(
                        1 for f in child.iterdir() if f.is_file() and f.suffix.lower() == ".scad"
                    )
    assert len(masters) == independent, (len(masters), independent)
    covered_repos = {p.parent.parent.name for p in masters}
    missing_upper = UPPERCASE_CAD_REPOS - covered_repos
    assert not missing_upper, f"CAD/ repos not discovered: {sorted(missing_upper)}"
    expected = os.environ.get("MAKERBENCH_INSTRUMENTS_MASTER_COUNT")
    if expected:
        assert len(masters) == int(expected), (len(masters), expected)
    mismatches = []
    total = 0
    for path in masters:
        src = path.read_text(encoding="utf-8", errors="replace")
        model = extract_parameters(src, "openscad")
        total += len(model.parameters)
        values = {p.name: p.value for p in model.parameters if p.editable}
        if apply_parameters(src, values, "openscad") != src:
            mismatches.append(path)
    assert not mismatches, mismatches[:5]
    assert total > 0
