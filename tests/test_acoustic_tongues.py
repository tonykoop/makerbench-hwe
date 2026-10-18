import hashlib
import json
import zipfile

import numpy as np
import pytest
from shapely.geometry import box
from shapely.ops import unary_union

from makerbench.acoustic_tongues import extract_profiles, main, saved_panel_brep


def generated_footprint(offset=(0, 0)):
    # Two straight, uniform tongues and surrounding frame; no traced fixture data.
    plate = box(0, 0, 220, 120)
    left_cut = unary_union([box(20, 28, 100, 30), box(20, 40, 100, 42), box(95, 28, 100, 42)])
    right_cut = unary_union([box(120, 78, 200, 80), box(120, 90, 200, 92), box(120, 78, 125, 92)])
    plate = plate.difference(unary_union([left_cut, right_cut]))
    from shapely.affinity import translate

    plate = translate(plate, *offset)
    return dict(
        outer=list(plate.exterior.coords),
        holes=[list(h.coords) for h in plate.interiors],
        thickness_mm=12,
        deflection_mm=0,
    )


def test_measured_profiles_and_translation():
    for offset in ((0, 0), (31, -7)):
        r = extract_profiles(generated_footprint(offset), 20, 40)
        assert len(r["tongues"]) == 2
        for row, side, y in zip(r["tongues"], ("left", "right"), (35, 85)):
            assert row["side"] == side
            assert row["free_length_mm"] == pytest.approx(75)
            assert row["neck_width_mm"] == row["head_width_mm"] == pytest.approx(10)
            assert row["root_center_y_mm"] == pytest.approx(y + offset[1])
            assert row["widths_mm"] == pytest.approx([10] * 40)
            assert row["edges_mm"] == pytest.approx(np.linspace(0, 75, 41))
            assert row["thickness_mm"] == 12


@pytest.mark.parametrize(
    "band,n", [(0, 40), (-1, 40), (float("nan"), 40), (110, 40), (20, 0), (20, 513)]
)
def test_reject_bad_clamp_and_sampling(band, n):
    with pytest.raises(ValueError):
        extract_profiles(generated_footprint(), band, n)


def test_missing_tongues_and_wrong_thickness():
    f = dict(outer=list(box(0, 0, 220, 120).exterior.coords), holes=[], thickness_mm=12)
    with pytest.raises(ValueError, match="No single-root"):
        extract_profiles(f, 20)
    f = generated_footprint()
    f["thickness_mm"] = 11
    with pytest.raises(ValueError, match="12 mm"):
        extract_profiles(f, 20)


def native(path, name="PanelBody", shape=b"saved-geometry", link=None):
    data = (
        f'<Property name="LinkedObject"><XLink file="{link}" name="PanelBody"/></Property>'
        if link
        else '<Property name="Shape"><Part file="Panel.Shape.brp"/></Property>'
    )
    kind = "App::Link" if link else "PartDesign::Body"
    xml = (
        f'<Document><Objects><Object type="{kind}" name="{name}"/></Objects>'
        f'<ObjectData><Object name="{name}"><Properties>{data}</Properties></Object>'
        "</ObjectData></Document>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Document.xml", xml)
        if not link:
            z.writestr("Panel.Shape.brp", shape)


def test_saved_brep_and_assembly_links(tmp_path):
    (tmp_path / "parts").mkdir()
    panel = tmp_path / "parts/top.FCStd"
    native(panel)
    assembly = tmp_path / "assembly.FCStd"
    native(assembly, name="Top", link="parts/top.FCStd")
    data, prov = saved_panel_brep(assembly)
    assert data == b"saved-geometry"
    assert prov["input_sha256"] == hashlib.sha256(assembly.read_bytes()).hexdigest()
    assert prov["panel_sha256"] == hashlib.sha256(panel.read_bytes()).hexdigest()
    assert "no document recompute" in prov["shape_basis"]
    standalone = tmp_path / "panel.brep"
    standalone.write_bytes(data)
    assert saved_panel_brep(standalone)[0] == data
    panel.unlink()
    with pytest.raises(FileNotFoundError):
        saved_panel_brep(assembly)


def test_unsafe_external_links_rejected_before_read(tmp_path):
    assembly = tmp_path / "assembly.FCStd"
    native(assembly, name="Top", link="../private/anything.FCStd")
    with pytest.raises(ValueError):
        saved_panel_brep(assembly)


def test_cli_missing_input_is_labelled_skipped(tmp_path):
    output = tmp_path / "report.json"
    assert main([str(tmp_path / "absent.FCStd"), "--root-band-mm", "20", "--out", str(output)]) == 0
    r = json.loads(output.read_text())
    assert r["status"] == "skipped" and r["tongues"] == []
    assert not r["included_in_base_checks"] and not r["included_in_published_rates"]


def test_generated_native_brep_controls(tmp_path):
    import os
    from pathlib import Path
    from makerbench.acoustic_tongues import extract_native
    from makerbench.wood_assembly import _run_worker

    if os.environ.get("MAKERBENCH_ACOUSTIC_INTEGRATION") != "1":
        pytest.skip("opt-in pinned FreeCAD sandbox integration")
    driver = Path(__file__).parent / "fixtures/acoustic_native_fixture.py"
    for variant in ("through", "blind", "thin"):
        output = tmp_path / variant
        output.mkdir()
        _run_worker(driver, {}, output, variant=variant)
        if variant == "through":
            r = extract_native(output / "panel.brep", 20, 40)
            assert len(r["tongues"]) == 2
            for t in r["tongues"]:
                assert t["free_length_mm"] == pytest.approx(75, abs=0.03)
                assert t["neck_width_mm"] == pytest.approx(10, abs=0.03)
        else:
            with pytest.raises(RuntimeError):
                extract_native(output / "panel.brep", 20, 40)
