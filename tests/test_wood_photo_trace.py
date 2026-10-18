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


def test_traced_control_preserves_the_missing_observed_top_roots():
    profile = json.loads((ROOT / "tests/fixtures/wood_photo_trace.json").read_text())
    roots = sorted(row["cap_next"][1] * 3 for row in profile["smooth_runs"] if row["left_next"])
    assert len(roots) == 6
    for observed in (116, 187, 353, 440, 529, 610):
        assert min(abs(root - observed) for root in roots) < 6
    assert all(abs(root - 270) > 20 for root in roots)


def test_traced_control_has_ten_owners_and_small_smooth_spline_sections():
    profile = json.loads((ROOT / "tests/fixtures/wood_photo_trace.json").read_text())
    assert len(profile["tongues"]) == 10
    assert sorted(row["side"] for row in profile["tongues"]) == ["left"] * 5 + ["right"] * 5
    assert len(profile["source_photo_sha256"]) == 64
    assert profile["nominal_kerf_mm"] == 3.2
    assert set(profile["point_owners"].values()) == set(range(10))
    runs = profile["smooth_runs"]
    assert len(runs) == 12
    assert sum(len(run["pieces"]) for run in runs) < 50
    for run, following in zip(runs, runs[1:] + runs[:1]):
        for piece in run["pieces"]:
            assert 8 <= len(piece["poles"]) <= 12
            assert len(piece["knots"]) == len(piece["poles"]) + 4
            assert piece["knots"][:4] == [0] * 4
            assert piece["knots"][-4:] == [1] * 4
            assert piece["error"] <= 1.2
            assert all(
                ",".join(f"{v:.10f}" for v in pole) in profile["point_owners"]
                for pole in piece["poles"]
            )
        for a, b in zip(run["pieces"], run["pieces"][1:]):
            assert np.linalg.norm(np.array(a["poles"][-1]) - b["poles"][0]) < 1e-8
        # A single nominal-width semicircle joins each pair of shared slit edges.
        assert abs(abs(run["root_b"][1] - following["root_a"][1]) - 3.2) < 1e-8
        assert abs(run["root_b"][0] - following["root_a"][0]) < 1e-8
