"""'Built with MakerBench: NN/100' badge generator (makerbench-hwe #99).

Stdlib-only: deterministic SVG + markdown generation, score conversion, color
bands, clamping, and the n/a path for unscored tracks.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from makerbench.badge import (
    DEFAULT_LABEL,
    MAX_LEVEL,
    badge_from_track,
    badge_markdown,
    clamp_percent,
    color_for,
    percent_from_level_mean,
    render_score_badge,
)


# --- conversion + clamping --------------------------------------------------

def test_percent_from_level_mean_uses_ladder_ceiling():
    assert percent_from_level_mean(4.0) == 100.0
    assert percent_from_level_mean(2.0) == 50.0
    assert percent_from_level_mean(0.0) == 0.0


def test_percent_from_level_mean_custom_ceiling():
    assert percent_from_level_mean(1.0, max_level=2.0) == 50.0


def test_percent_from_level_mean_rejects_bad_ceiling():
    with pytest.raises(ValueError):
        percent_from_level_mean(1.0, max_level=0.0)


def test_clamp_percent_bounds_and_rounds():
    assert clamp_percent(-5) == 0.0
    assert clamp_percent(140) == 100.0
    assert clamp_percent(82.349) == 82.3


# --- color bands ------------------------------------------------------------

@pytest.mark.parametrize("pct,color", [
    (0, "#e05d44"), (39.9, "#e05d44"),
    (40, "#fe7d37"), (54, "#fe7d37"),
    (55, "#dfb317"), (69, "#dfb317"),
    (70, "#a4a61d"), (84, "#a4a61d"),
    (85, "#4c1"), (100, "#4c1"),
])
def test_color_bands(pct, color):
    assert color_for(pct) == color


# --- SVG rendering ----------------------------------------------------------

def test_svg_is_well_formed_and_carries_score():
    svg = render_score_badge(82.5)
    root = ET.fromstring(svg)  # raises on malformed XML
    assert root.tag.endswith("svg")
    assert "82.5/100" in svg
    assert DEFAULT_LABEL in svg
    # value rect uses the score-band color
    assert color_for(82.5) in svg


def test_svg_clamps_and_is_deterministic():
    assert render_score_badge(150) == render_score_badge(100)
    assert render_score_badge(73.0) == render_score_badge(73.0)
    assert "100/100" in render_score_badge(150)


def test_svg_escapes_label():
    svg = render_score_badge(50, label="A & B <x>")
    ET.fromstring(svg)  # must remain well-formed
    assert "&amp;" in svg and "&lt;x&gt;" in svg


# --- markdown ---------------------------------------------------------------

def test_markdown_text_fallback_without_image():
    md = badge_markdown(82.5)
    assert md == "[Built with MakerBench: 82.5/100](https://github.com/tonykoop/makerbench-hwe)"


def test_markdown_image_form():
    md = badge_markdown(90, image_url="docs/assets/badge.svg", link="https://example.com")
    assert md == "[![Built with MakerBench: 90/100](docs/assets/badge.svg)](https://example.com)"


# --- from a result track ----------------------------------------------------

def test_badge_from_track_converts_overall_mean():
    out = badge_from_track({"overall_mean": 3.0})
    assert out["percent"] == 75.0
    assert "75/100" in out["svg"]
    assert MAX_LEVEL == 4.0


def test_badge_from_track_handles_missing_score():
    out = badge_from_track({"n_seeds_total": 0})  # no overall_mean
    assert out["percent"] is None
    assert "n/a" in out["svg"]
    assert "n/a" in out["markdown"]
    ET.fromstring(out["svg"])  # still well-formed
