"""The #1134 CAD file naming rule."""

import pytest

from makerbench import cad_names


def test_stem_and_names_follow_the_convention():
    stem = cad_names.stem("tongue_drum", "10T 457x241", "v2-traced")
    assert stem == "tongue-drum_10t-457x241_v2-traced"
    assert cad_names.name(stem, "Half A", "FCStd") == "tongue-drum_10t-457x241_v2-traced_half-a.FCStd"
    assert cad_names.name(cad_names.stem("sambuca"), cad_names.MODEL_PART, ".stl") == "sambuca_base_r1_model.stl"
    assert cad_names.follows("sambuca_base_r1_model.SLDPRT", "sambuca_base_r1")
    assert not cad_names.follows("assembly.FCStd", "sambuca_base_r1")
    with pytest.raises(ValueError, match="empty CAD name field"):
        cad_names.stem("sambuca", "--")
