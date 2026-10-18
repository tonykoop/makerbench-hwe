"""Focused tests for the one-plank nesting engine (#1098)."""

import csv
import json

from makerbench import plank_nesting as pn

# ILLUSTRATIVE half-blank size (inches) for plumbing tests only; real sizes come
# from the flute family generator (#1097) once the cleaned tables land.
B4 = pn.BlankType("B4", 14.0, 1.75)


def _overlap(a, b, gap):
    return not (
        a.x + a.length + gap <= b.x + 1e-9 or b.x + b.length + gap <= a.x + 1e-9
        or a.y + a.width + gap <= b.y + 1e-9 or b.y + b.width + gap <= a.y + 1e-9
    )


def test_crosscut_96in_into_two_segments_fitting_bed():
    plank = pn.PlankSpec()
    segs = pn.crosscut_plan(plank)
    assert len(segs) == 3  # 96 in cannot fit two 46 in segments
    assert all(s.length <= plank.max_segment_length + 1e-9 for s in segs)
    total = sum(s.length for s in segs) + plank.crosscut_kerf * (len(segs) - 1)
    assert abs(total - 96.0) < 1e-6


def test_no_overlap_margins_and_grain_alignment():
    plank = pn.PlankSpec(tabs_per_blank=2)
    segs, pl, unplaced, oid, rows = pn.nest(plank, {"B4": B4}, [{"key": "B4", "count": 6}])
    assert not unplaced and len(pl) == 12
    for p in pl:
        s = segs[p.segment]
        assert p.length >= p.width  # grain along X
        assert p.x >= plank.edge_margin_x - 1e-9
        assert p.x + p.length <= s.length - plank.edge_margin_x + 1e-9
        assert p.y >= plank.edge_margin_y - 1e-9
        assert p.y + p.width <= plank.width - plank.edge_margin_y + 1e-9
        assert len(p.tabs_x) == 2
    for i, a in enumerate(pl):
        for b in pl[i + 1:]:
            if a.segment == b.segment:
                assert not _overlap(a, b, plank.spacing - 1e-6)


def test_oversize_blank_reported_not_dropped():
    big = pn.BlankType("D4", 60.0, 2.0)
    _, pl, unplaced, _, _ = pn.nest(pn.PlankSpec(), {"D4": big}, [{"key": "D4", "count": 1}])
    assert not pl and len(unplaced) == 2


def test_unknown_key_errors():
    try:
        pn.nest(pn.PlankSpec(), {"B4": B4}, [{"key": "C9", "count": 1}])
    except pn.NestingError:
        return
    raise AssertionError("expected NestingError")


def test_outputs_named_per_1134_and_deterministic(tmp_path):
    order = {
        "blanks": {"B4": {"length": 14.0, "width": 1.75}, "D5": {"length": 9.0, "width": 1.5}},
        "order": [{"key": "B4", "count": 3}, {"key": "D5", "count": 1}],
    }
    f = tmp_path / "order.json"
    f.write_text(json.dumps(order))
    folder, rep = pn.run(f, tmp_path / "out")
    stem = folder.name
    assert stem.startswith("naf-flute-plank_o") and stem.endswith("_r1")
    names = sorted(p.name for p in folder.iterdir())
    assert names == [f"{stem}_layout.svg", f"{stem}_serial-map.csv", f"{stem}_utilisation.json"]
    rows = list(csv.DictReader(open(folder / f"{stem}_serial-map.csv")))
    assert len(rows) == 8 and {r["half"] for r in rows} == {"half-a", "half-b"}
    assert (folder / f"{stem}_layout.svg").read_text().startswith("<svg")
    assert 0 < rep["utilisation"] < 1
    folder2, _ = pn.run(f, tmp_path / "out2")
    assert folder2.name == stem


def test_fill_suggestions_list_shorter_keys():
    blanks = {"B4": B4, "D5": pn.BlankType("D5", 9.0, 1.5)}
    plank = pn.PlankSpec(length=40.0)
    segs, pl, un, oid, rows = pn.nest(plank, blanks, [{"key": "B4", "count": 1}])
    rep = pn.report(plank, segs, pl, un, rows, blanks)
    assert any("D5" in s["keys_that_fit"] for s in rep["fill_suggestions"])
