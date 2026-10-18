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


def test_fifth_tongue_scale_moves_its_actual_tip_without_changing_default_curves():
    """Poles can cross the slot: ownership must follow the physical boundary."""
    import math
    from scipy.interpolate import BSpline
    from shapely.geometry import Polygon, box

    profile = json.loads((ROOT / "tests/fixtures/wood_photo_trace.json").read_text())

    def footprint(edited=False):
        points = []
        for run in profile["smooth_runs"]:
            points.append(run["root_a"])
            for piece in run["pieces"]:
                poles = []
                for x, y in piece["poles"]:
                    owner = profile["point_owners"][f"{x:.10f},{y:.10f}"]
                    if edited and owner == 4:
                        origin = 80 if profile["tongues"][owner]["side"] == "left" else 377
                        x += (x - origin) * 0.01
                    poles.append((x, y))
                points.extend(BSpline(piece["knots"], poles, 3)(np.linspace(0, 1, 200)))
            points.append(run["root_b"])
            x, y = run["root_b"]
            cy = run["cap_next"][1]
            angle = math.atan2(y - cy, 0)
            direction = (
                1 if ((run["left_next"] and y > cy) or (not run["left_next"] and y < cy)) else -1
            )
            angles = np.linspace(angle, angle + direction * math.pi, 30)
            points.extend(np.column_stack((x + 1.6 * np.cos(angles), cy + 1.6 * np.sin(angles))))
        slots = Polygon(points)
        assert slots.is_valid
        material = box(0, 0, 457, 241).difference(slots).intersection(box(80.001, 0, 376.999, 241))
        tongues = [
            g
            for g in material.geoms
            if g.bounds[1] > 0.05
            and g.bounds[3] < 240.95
            and ((abs(g.bounds[0] - 80.001) < 0.05) != (abs(g.bounds[2] - 376.999) < 0.05))
        ]

        def root_center(g):
            from shapely.geometry import LineString

            x = 85 if abs(g.bounds[0] - 80.001) < 0.05 else 372
            return g.intersection(LineString([(x, 0), (x, 241)])).centroid.y

        return sorted(tongues, key=root_center)

    baseline = footprint()
    edited = footprint(True)
    assert len(baseline) == len(edited) == 10
    assert trace.assign_point_owners(profile["smooth_runs"], baseline) == profile["point_owners"]
    before = 457 - baseline[4].bounds[0]
    after = 457 - edited[4].bounds[0]
    assert abs((after - before) - (before - 80) * 0.01) < 0.005
    assert after - before > 0.5
