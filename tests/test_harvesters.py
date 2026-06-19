"""Deterministic AABB evaluation harvesters (makerbench-hwe #307).

Stdlib-only: interference volume, clearance gap, and mass properties computed
analytically for box-decomposed placement components, with exact expected values.
"""

from __future__ import annotations

import math

import pytest

from makerbench.harvesters import (
    AABB,
    Component,
    aggregate_mass_properties,
    clearance_gap,
    harvest,
    interference_volume,
    mass_properties,
)


def _box(lo, hi):
    return AABB(tuple(lo), tuple(hi))


# --- AABB basics ------------------------------------------------------------

def test_aabb_rejects_inverted():
    with pytest.raises(ValueError):
        AABB((0, 0, 0), (0, -1, 0))


def test_from_center_size_and_volume():
    b = AABB.from_center_size((0, 0, 0), (10, 10, 10))
    assert b.lo == (-5, -5, -5) and b.hi == (5, 5, 5)
    assert b.volume_mm3 == 1000.0
    assert b.center == (0, 0, 0)


# --- interference volume ----------------------------------------------------

def test_interference_overlap_volume():
    a = _box((0, 0, 0), (10, 10, 10))
    b = _box((5, 0, 0), (15, 10, 10))  # overlap on x in [5,10]
    assert interference_volume(a, b) == 5 * 10 * 10


def test_interference_zero_when_disjoint():
    a = _box((0, 0, 0), (10, 10, 10))
    b = _box((15, 0, 0), (25, 10, 10))
    assert interference_volume(a, b) == 0.0


def test_interference_zero_when_touching():
    a = _box((0, 0, 0), (10, 10, 10))
    b = _box((10, 0, 0), (20, 10, 10))  # share a face, zero overlap volume
    assert interference_volume(a, b) == 0.0


# --- clearance gap ----------------------------------------------------------

def test_clearance_axis_aligned_gap():
    a = _box((0, 0, 0), (10, 10, 10))
    b = _box((15, 0, 0), (25, 10, 10))
    assert clearance_gap(a, b) == 5.0


def test_clearance_diagonal_gap():
    a = _box((0, 0, 0), (10, 10, 10))
    b = _box((13, 14, 0), (20, 20, 10))  # gap x=3, y=4, z overlap -> 5
    assert clearance_gap(a, b) == pytest.approx(5.0)


def test_clearance_zero_when_overlapping():
    a = _box((0, 0, 0), (10, 10, 10))
    b = _box((5, 5, 5), (15, 15, 15))
    assert clearance_gap(a, b) == 0.0


# --- mass properties --------------------------------------------------------

def test_mass_properties_cube():
    c = Component("cube", _box((0, 0, 0), (10, 10, 10)), density_g_per_mm3=0.001)
    mp = mass_properties(c)
    assert mp["volume_mm3"] == 1000.0
    assert mp["mass_g"] == pytest.approx(1.0)
    assert mp["centroid_mm"] == [5.0, 5.0, 5.0]
    # Ixx = m/12 (dy^2+dz^2) = 1/12*(100+100) = 16.667
    assert mp["inertia_g_mm2"]["ixx"] == pytest.approx(200.0 / 12.0)


def test_aggregate_mass_weighted_centroid():
    a = Component("a", AABB.from_center_size((0, 0, 0), (10, 10, 10)), 0.002)  # 2 g
    b = Component("b", AABB.from_center_size((30, 0, 0), (10, 10, 10)), 0.001)  # 1 g
    agg = aggregate_mass_properties([a, b])
    assert agg["total_mass_g"] == pytest.approx(3.0)
    # weighted x = (2*0 + 1*30)/3 = 10
    assert agg["combined_centroid_mm"][0] == pytest.approx(10.0)


def test_aggregate_zero_mass_safe():
    a = Component("a", _box((0, 0, 0), (1, 1, 1)))  # density 0
    agg = aggregate_mass_properties([a])
    assert agg["total_mass_g"] == 0.0
    assert agg["combined_centroid_mm"] == [0.0, 0.0, 0.0]


# --- unified harvest --------------------------------------------------------

def _placement():
    return [
        Component("bracket", _box((0, 0, 0), (10, 10, 10)), 0.001),
        Component("plate", _box((5, 0, 0), (15, 10, 10)), 0.001),     # overlaps bracket
        Component("standoff", _box((40, 0, 0), (50, 10, 10)), 0.001),  # far away
    ]


def test_harvest_reports_interference_and_clearance():
    report = harvest(_placement(), min_clearance_mm=2.0)
    # bracket/plate overlap by 500 mm^3
    assert report["interference_total_mm3"] == 500.0
    assert report["gates"]["any_interference"] is True
    assert ["bracket", "plate"] in report["gates"]["overlapping_pairs"]
    # standoff is 25 mm from plate, 30 from bracket -> clearance ok
    assert report["gates"]["clearance_ok"] is True
    assert report["aggregate"]["total_mass_g"] == pytest.approx(3.0)


def test_harvest_flags_clearance_violation():
    comps = [
        Component("a", _box((0, 0, 0), (10, 10, 10)), 0.001),
        Component("b", _box((11, 0, 0), (21, 10, 10)), 0.001),  # 1 mm gap
    ]
    report = harvest(comps, min_clearance_mm=2.0)
    assert report["gates"]["clearance_ok"] is False
    assert report["gates"]["clearance_violations"][0]["gap_mm"] == pytest.approx(1.0)


def test_harvest_is_deterministic_and_order_independent():
    comps = _placement()
    r1 = harvest(comps, min_clearance_mm=2.0)
    r2 = harvest(list(reversed(comps)), min_clearance_mm=2.0)
    assert r1 == r2  # sorted-pair emission makes output order-independent
    assert r1["components"] == ["bracket", "plate", "standoff"]


def test_harvest_min_clearance_observed():
    report = harvest(_placement(), min_clearance_mm=0.0)
    # overlapping pair contributes gap 0.0
    assert report["min_clearance_observed_mm"] == 0.0
    assert math.isfinite(report["min_clearance_observed_mm"])
