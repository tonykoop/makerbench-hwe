"""Parts catalog + tool sanity checks (no OpenSCAD required)."""

from makerbench.parts import PartsLibrary, as_tool


def test_catalog_loads_and_has_expected_categories():
    lib = PartsLibrary()
    cats = {p["category"] for p in lib.catalog["parts"]}
    assert "socket_head_cap_screw" in cats
    assert "heat_set_insert" in cats
    assert lib.catalog["tolerances"]["clearance_hole_dia_mm"] > 0
    assert lib.catalog["tolerances"]["boss_hole_dia_mm"] > 0


def test_search_filters_by_thread_and_length():
    lib = PartsLibrary()
    m3_long = lib.search(category="socket_head_cap_screw", thread="M3",
                         min_length_mm=10)
    assert m3_long, "expected some M3 screws >= 10 mm"
    assert all(p["thread"] == "M3" and p["length_mm"] >= 10 for p in m3_long)


def test_tool_callable_returns_serializable():
    search = as_tool()
    hits = search(thread="M4")
    assert isinstance(hits, list) and hits
    assert all("clearance_hole_normal_mm" in p for p in hits
               if p["category"] == "socket_head_cap_screw")


def test_engagement_math_has_a_valid_screw_for_every_seed_combo():
    """For lid in {2,3} and the M3 insert, MB-SHCS-M3-08 must be valid."""
    lib = PartsLibrary()
    insert = lib.get("MB-HSI-M3")
    screw = lib.get("MB-SHCS-M3-08")
    for lid_t in (2.0, 3.0):
        need = lid_t + insert["length_mm"]
        assert screw["length_mm"] >= need
        assert screw["length_mm"] <= need + 3.0
