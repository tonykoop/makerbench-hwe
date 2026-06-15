"""Direct tests for ``makerbench/vector.py`` — native SVG/DXF cut-file parsing,
measurement, and the canonical ``vector_sha256`` anti-cheat anchor (#222).

Before this file the ~650-line parser was only exercised as a by-product of
oracle/ladder tests. These hand-authored fixtures pin the measured scalars
(bbox, cut area, developed area, min-web, slot widths), the order/start-point
invariance of the hash, and the levelled rejection codes that let Level-1 act as
a real structural gate.
"""

from __future__ import annotations

from makerbench import vector


# --------------------------------------------------------------------------- #
# Fixtures: a 100 x 60 mm plate with two square cut-outs.
# --------------------------------------------------------------------------- #
# Outer 100x60; cut-outs: 10x10 at (20,20) and 8x8 at (60,30).
PLATE_SVG = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" viewBox="0 0 100 60">
  <rect x="0" y="0" width="100" height="60"/>
  <rect x="20" y="20" width="10" height="10"/>
  <rect x="60" y="30" width="8" height="8"/>
</svg>
"""


def _lwpolyline(points, closed=True):
    """Emit a minimal closed LWPOLYLINE entity for the given (x, y) points."""
    out = ["0", "LWPOLYLINE", "90", str(len(points)), "70", "1" if closed else "0"]
    for x, y in points:
        out += ["10", str(x), "20", str(y)]
    return out


def _dxf(entities_lines):
    lines = [
        "0", "SECTION", "2", "HEADER",
        "9", "$INSUNITS", "70", "4",
        "0", "ENDSEC",
        "0", "SECTION", "2", "ENTITIES",
        *entities_lines,
        "0", "ENDSEC",
        "0", "EOF",
    ]
    return "\n".join(lines) + "\n"


PLATE_DXF = _dxf(
    _lwpolyline([(0, 0), (100, 0), (100, 60), (0, 60)])
    + _lwpolyline([(20, 20), (30, 20), (30, 30), (20, 30)])
    + _lwpolyline([(60, 30), (68, 30), (68, 38), (60, 38)])
)


# --------------------------------------------------------------------------- #
# SVG parsing + measurement
# --------------------------------------------------------------------------- #
def test_svg_parses_and_measures():
    pv = vector.parse_vector(PLATE_SVG)
    assert pv.ok
    assert pv.fmt == "svg"
    assert pv.width_mm == 100.0 and pv.height_mm == 60.0

    w, h = vector.outer_bbox_mm(pv)
    assert (round(w, 4), round(h, 4)) == (100.0, 60.0)
    assert vector.outer_profile_count(pv) == 1

    # Two cut-outs: 10*10 + 8*8 = 164 mm^2.
    assert round(vector.total_cut_area_mm2(pv), 4) == 164.0
    # Net solid = 100*60 - 164 = 5836 mm^2.
    assert round(vector.developed_area_mm2(pv), 4) == 5836.0
    # Slot bboxes: (10,10) and (8,8) -> smallest short side is 8.
    assert round(vector.measured_slot_width_mm(pv), 4) == 8.0


def test_svg_path_and_polygon_equivalent_to_rect():
    """A path-drawn and polygon-drawn square produce the same outer bbox."""
    path_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="40mm" height="40mm" '
        'viewBox="0 0 40 40">'
        '<path d="M 0 0 L 40 0 L 40 40 L 0 40 Z"/></svg>'
    )
    poly_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="40mm" height="40mm" '
        'viewBox="0 0 40 40">'
        '<polygon points="0,0 40,0 40,40 0,40"/></svg>'
    )
    for svg in (path_svg, poly_svg):
        pv = vector.parse_vector(svg)
        assert pv.ok
        assert round(vector.developed_area_mm2(pv), 4) == 1600.0


# --------------------------------------------------------------------------- #
# DXF parsing + measurement (must match the SVG plate scalars)
# --------------------------------------------------------------------------- #
def test_dxf_parses_and_matches_svg_measurements():
    pv = vector.parse_vector(PLATE_DXF)
    assert pv.ok
    assert pv.fmt == "dxf"
    w, h = vector.outer_bbox_mm(pv)
    assert (round(w, 4), round(h, 4)) == (100.0, 60.0)
    assert round(vector.total_cut_area_mm2(pv), 4) == 164.0
    assert round(vector.developed_area_mm2(pv), 4) == 5836.0


def test_min_web_between_cutouts_and_edge():
    """min_web is the closest spacing among cut-outs and to the outer edge."""
    pv = vector.parse_vector(PLATE_SVG)
    # cut A spans x[20,30], cut B spans x[60,68]; nearest gap A->B = 30 in x but
    # they overlap in neither row; closest hole-to-hole distance is 30.0 (x gap
    # 60-30) only if y-ranges overlap. A y[20,30], B y[30,38] touch at y=30, so
    # the planar distance is the x-gap of 30. The nearest hole-to-edge is cut A's
    # left edge at x=20 -> 20 to the outer boundary. min over all = 20.
    assert round(vector.min_web_mm(pv), 4) == 20.0


# --------------------------------------------------------------------------- #
# Canonical hash: stable, order/start-point invariant, geometry-sensitive
# --------------------------------------------------------------------------- #
def test_vector_sha256_is_stable():
    pv = vector.parse_vector(PLATE_SVG)
    h1 = vector.vector_sha256(pv)
    h2 = vector.vector_sha256(vector.parse_vector(PLATE_SVG))
    assert h1 == h2
    assert len(h1) == 64


def test_vector_sha256_invariant_to_emission_order_and_start_point():
    """Reordering subpaths and rotating a ring's start vertex must not move it."""
    reordered = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" viewBox="0 0 100 60">
  <rect x="60" y="30" width="8" height="8"/>
  <path d="M 30 20 L 30 30 L 20 30 L 20 20 Z"/>
  <path d="M 100 60 L 0 60 L 0 0 L 100 0 Z"/>
</svg>
"""
    base = vector.vector_sha256(vector.parse_vector(PLATE_SVG))
    assert vector.vector_sha256(vector.parse_vector(reordered)) == base


def test_vector_sha256_changes_with_geometry():
    bigger = PLATE_SVG.replace('width="10" height="10"', 'width="12" height="12"')
    assert vector.vector_sha256(vector.parse_vector(bigger)) != vector.vector_sha256(
        vector.parse_vector(PLATE_SVG)
    )


def test_svg_and_dxf_hash_independently_stable():
    """Cross-format hash equality is explicitly NOT required; each is stable."""
    svg_hash = vector.vector_sha256(vector.parse_vector(PLATE_SVG))
    dxf_hash = vector.vector_sha256(vector.parse_vector(PLATE_DXF))
    assert vector.vector_sha256(vector.parse_vector(PLATE_DXF)) == dxf_hash
    # No assertion that svg_hash == dxf_hash by contract.
    assert isinstance(svg_hash, str)


# --------------------------------------------------------------------------- #
# Levelled rejections: bad artifacts fail Level-1, never raise
# --------------------------------------------------------------------------- #
def _reasons(text):
    return [r.reason for r in vector.parse_vector(text).rejections]


def test_rejects_open_path():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" '
        'viewBox="0 0 10 10"><path d="M 0 0 L 10 0 L 10 10"/></svg>'
    )
    assert "open_path" in _reasons(svg)


def test_rejects_curve():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" '
        'viewBox="0 0 10 10"><path d="M 0 0 C 1 1 2 2 3 3 Z"/></svg>'
    )
    assert "curve_unsupported" in _reasons(svg)


def test_rejects_relative_coords():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" '
        'viewBox="0 0 10 10"><path d="m 0 0 l 10 0 l 0 10 z"/></svg>'
    )
    assert "relative_coords" in _reasons(svg)


def test_rejects_non_mm_units():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="10px" height="10px" '
        'viewBox="0 0 10 10"><rect x="0" y="0" width="10" height="10"/></svg>'
    )
    assert "ambiguous_units" in _reasons(svg)


def test_rejects_transform():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" '
        'viewBox="0 0 10 10">'
        '<rect transform="translate(1,1)" x="0" y="0" width="5" height="5"/></svg>'
    )
    assert "transform_unsupported" in _reasons(svg)


def test_rejects_rounded_rect():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" '
        'viewBox="0 0 10 10"><rect x="0" y="0" width="5" height="5" rx="1"/></svg>'
    )
    assert "rounded_rect_unsupported" in _reasons(svg)


def test_dxf_rejects_curve_entity():
    dxf = _dxf(["0", "CIRCLE", "10", "5", "20", "5", "40", "2"])
    assert "curve_unsupported" in _reasons(dxf)


def test_dxf_rejects_wrong_units():
    dxf = (
        "0\nSECTION\n2\nHEADER\n9\n$INSUNITS\n70\n1\n0\nENDSEC\n"
        "0\nSECTION\n2\nENTITIES\n"
        + "\n".join(_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)]))
        + "\n0\nENDSEC\n0\nEOF\n"
    )
    assert "ambiguous_units" in _reasons(dxf)


def test_dxf_rejects_open_polyline():
    dxf = _dxf(_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], closed=False))
    assert "open_path" in _reasons(dxf)


def test_malformed_artifact_returns_rejection_not_exception():
    pv = vector.parse_vector("this is neither svg nor dxf")
    assert not pv.ok
    assert pv.rejections  # totality: a reason, not a raise
