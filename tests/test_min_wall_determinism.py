"""Determinism + shared-tolerance tests for the min-wall DFM gate (#219).

`estimate_min_wall_mm` draws surface samples; an unseeded draw used NumPy's
global RNG and fed a hard pass/fail, so a part at the wall floor could flip
pass/fail on re-grade or across machines. The fix threads a fixed seed and
routes every floor comparison through the shared `printable_wall` band. These
tests pin both: identical measurements across N re-grades, and one consistent
tolerance band across graders.
"""

from __future__ import annotations

import numpy as np
import trimesh

from makerbench import geometry as geo


def _thin_box(wall_mm: float = 2.0):
    """A watertight box whose smallest extent is the wall under test."""
    return trimesh.creation.box(extents=[30.0, 30.0, wall_mm])


# --------------------------------------------------------------------------- #
# Determinism: a seeded estimate is identical across re-grades
# --------------------------------------------------------------------------- #
def test_seeded_min_wall_is_repeatable():
    box = _thin_box(2.0)
    values = [geo.estimate_min_wall_mm(box, seed=0) for _ in range(8)]
    assert len(set(values)) == 1, f"seeded min-wall drifted across regrades: {values}"


def test_seeded_min_wall_is_isolated_from_global_rng():
    """A seeded estimate must not depend on (or perturb) the global RNG."""
    box = _thin_box(2.0)

    np.random.seed(123)
    a = geo.estimate_min_wall_mm(box, seed=42)
    # Disturb the global RNG between calls; a properly-seeded estimate ignores it.
    np.random.seed(999)
    _ = np.random.rand(1000)
    b = geo.estimate_min_wall_mm(box, seed=42)
    assert a == b


def test_different_seeds_share_the_same_floor_verdict():
    """Even if two seeds give slightly different samples, both clear the floor.

    The whole point of the tolerance band is that seed-to-seed jitter never
    changes the pass/fail verdict for a part at the nominal wall.
    """
    box = _thin_box(2.0)
    verdicts = {
        geo.printable_wall(geo.estimate_min_wall_mm(box, seed=s), 2.0)
        for s in range(6)
    }
    assert verdicts == {True}


# --------------------------------------------------------------------------- #
# Shared tolerance band: printable_wall semantics
# --------------------------------------------------------------------------- #
def test_printable_wall_accepts_part_at_floor():
    # The estimator undershoots: a true 2.0 mm wall measures ~1.999.
    assert geo.printable_wall(1.999, 2.0) is True
    # Exactly on the floor passes.
    assert geo.printable_wall(2.0, 2.0) is True


def test_printable_wall_rejects_genuinely_thin_wall():
    # 0.1 mm under the floor is well beyond the 0.05 mm measurement band.
    assert geo.printable_wall(1.9, 2.0) is False


def test_printable_wall_respects_band_edges():
    floor, tol = 2.0, geo.WALL_MEAS_TOL_MM
    assert geo.printable_wall(floor - tol, floor) is True       # at the band edge
    assert geo.printable_wall(floor - tol - 1e-6, floor) is False  # just past it


def test_printable_wall_custom_tolerance():
    assert geo.printable_wall(1.8, 2.0, tol_mm=0.25) is True
    assert geo.printable_wall(1.8, 2.0, tol_mm=0.1) is False


def test_wall_meas_tol_is_documented_default():
    assert geo.WALL_MEAS_TOL_MM == 0.05
