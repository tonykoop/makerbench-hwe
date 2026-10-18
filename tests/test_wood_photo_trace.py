"""Public photo trace controls; native generation is exercised in integration."""

import importlib.util
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "wood_photo_trace", ROOT / "scripts/trace_wood_photo.py"
)
trace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace)


def test_thinning_preserves_a_connected_bent_slit_and_its_end_regions():
    mask = np.zeros((35, 35), dtype=bool)
    mask[3:28, 8:13] = True
    mask[23:28, 8:29] = True
    skeleton = trace.skeletonize(mask)
    assert skeleton.sum() < mask.sum() / 2
    assert skeleton[3:9, 8:13].any() and skeleton[23:28, 24:29].any()
    # Every retained centerline pixel belongs to the thresholded cut.
    assert not (skeleton & ~mask).any()


def test_ten_tongue_adaptation_is_local_to_the_extra_top_tongue():
    points = np.array([[270.0, 200.0], [353.0, 200.0], [270.0, 900.0], [353.0, 900.0]])
    adapted = trace.adapt_ten(points)
    assert adapted[0, 0] == adapted[1, 0]
    assert np.array_equal(adapted[2:], points[2:])


def test_traced_control_has_ten_owned_tongues_and_a_closed_arc_line_chain():
    profile = json.loads((ROOT / "tests/fixtures/wood_photo_trace.json").read_text())
    assert len(profile["tongues"]) == 10
    assert sorted(row["side"] for row in profile["tongues"]) == ["left"] * 5 + ["right"] * 5
    assert len(profile["source_photo_sha256"]) == 64
    assert profile["nominal_kerf_mm"] == 3.2
    chain = profile["arc_chain"]
    assert any(segment[0] == "arc" for segment in chain)
    assert any(segment[0] == "line" for segment in chain)
    for a, b in zip(chain, chain[1:] + chain[:1]):
        assert np.linalg.norm(np.array(a[-1]) - b[1]) < 1e-8
    assert {owner for owner, weight in profile["owners"] if weight > 0.5} == set(range(10))
