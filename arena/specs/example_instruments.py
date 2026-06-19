"""
Inline example instrument specs for the arena prototype.

These mirror the structure of instrument-maker ``registry.yaml`` entries.
When the real registry is available, replace this module with a YAML loader:

    import yaml
    from pathlib import Path

    def load_registry(path: Path) -> list[InstrumentSpec]:
        with path.open() as f:
            entries = yaml.safe_load(f)
        return [InstrumentSpec(**e) for e in entries["instruments"]]

DoE note
--------
The instrument spec + seed pair is HELD CONSTANT across all entrants in one
trial round.  The only variable is the entrant (model or human).  These specs
are designed to span a range of complexity levels so the arena surfaces
skill-differentiation across models:

* dulcimer-3string-v1  — simple chordophone, few parameters
* ukulele-soprano-v1   — mid-complexity chordophone with bracing
* clarinet-bb-v1       — aerophone, cylindrical bore (harder geometry)
"""

from __future__ import annotations

from arena.models import InstrumentSpec


EXAMPLE_SPECS: list[InstrumentSpec] = [
    # ------------------------------------------------------------------
    # 1. Appalachian dulcimer — simple chordophone
    # ------------------------------------------------------------------
    InstrumentSpec(
        spec_id="dulcimer-3string-v1",
        instrument_family="chordophone",
        description=(
            "Appalachian mountain dulcimer, 3-string diatonic fretboard. "
            "Hourglass body, flat soundboard, drone + melody strings."
        ),
        parameters={
            # Body
            "body_length_mm": 660,
            "body_width_mm": 155,
            "body_depth_mm": 55,
            "wall_thickness_mm": 3.5,
            # Neck / fretboard
            "neck_length_mm": 580,
            "neck_width_mm": 38,
            "scale_length_mm": 660,
            # Strings
            "string_count": 3,
            "string_spacing_mm": 10,
            # Sound hole
            "sound_hole_diameter_mm": 50,
            "sound_hole_position_frac": 0.5,
            # Material hints (for DFM gate)
            "material": "birch_ply_6mm",
            "print_orientation": "flat",
        },
    ),

    # ------------------------------------------------------------------
    # 2. Soprano ukulele — mid-complexity chordophone
    # ------------------------------------------------------------------
    InstrumentSpec(
        spec_id="ukulele-soprano-v1",
        instrument_family="chordophone",
        description=(
            "Standard soprano ukulele (GCEA). Figure-8 spruce top, "
            "mahogany sides/back, fan bracing pattern."
        ),
        parameters={
            # Body
            "body_length_mm": 280,
            "body_width_mm": 175,
            "body_depth_mm": 60,
            "wall_thickness_mm": 2.8,
            # Bracing
            "brace_count": 3,
            "brace_width_mm": 6,
            "brace_height_mm": 8,
            # Neck
            "neck_length_mm": 215,
            "neck_width_mm": 35,
            "neck_taper_frac": 0.08,
            "scale_length_mm": 340,
            # Headstock
            "headstock_length_mm": 70,
            "tuner_count": 4,
            # Strings
            "string_count": 4,
            "nut_width_mm": 35,
            "saddle_width_mm": 33,
            # Sound hole
            "sound_hole_diameter_mm": 45,
            "sound_hole_position_frac": 0.48,
            # Material
            "material": "pla_or_spruce",
            "print_orientation": "upright_body",
        },
    ),

    # ------------------------------------------------------------------
    # 3. Bb clarinet body — aerophone (hardest geometry)
    # ------------------------------------------------------------------
    InstrumentSpec(
        spec_id="clarinet-bb-v1",
        instrument_family="aerophone",
        description=(
            "Bb clarinet upper joint. Cylindrical bore with tone holes, "
            "register key hole, and tenon joints for assembly. ABS-printable."
        ),
        parameters={
            # Bore
            "bore_diameter_mm": 14.65,
            "body_outer_diameter_mm": 30,
            "upper_joint_length_mm": 295,
            "wall_thickness_mm": 7.7,
            # Tone holes (simplified — real registry has per-hole positions)
            "tone_hole_count": 8,
            "tone_hole_diameter_mm": 9.5,
            "tone_hole_first_position_mm": 50,
            "tone_hole_spacing_mm": 28,
            # Register key
            "register_hole_diameter_mm": 4,
            "register_hole_position_mm": 20,
            # Tenons
            "tenon_length_mm": 15,
            "tenon_outer_diameter_mm": 24,
            "tenon_inner_diameter_mm": 14.65,
            # Material
            "material": "abs",
            "print_orientation": "vertical",
        },
    ),
]

# Quick-lookup by spec_id
_SPEC_MAP: dict[str, InstrumentSpec] = {s.spec_id: s for s in EXAMPLE_SPECS}


def get_spec(spec_id: str) -> InstrumentSpec:
    """Return an example spec by id, or raise ``KeyError`` if not found.

    Parameters
    ----------
    spec_id:
        One of the spec ids defined in this module.
    """
    if spec_id not in _SPEC_MAP:
        available = ", ".join(sorted(_SPEC_MAP))
        raise KeyError(
            f"Unknown spec_id {spec_id!r}. Available: {available}"
        )
    return _SPEC_MAP[spec_id]
