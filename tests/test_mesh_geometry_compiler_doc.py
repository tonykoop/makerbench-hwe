"""Contract checks for the MeshFlow geometry-compiler evaluation note (issue #164).

Issue #164 asks MakerBench to *evaluate* whether a MeshFlow-style universal
geometry compiler (text / image / point-cloud / scan -> mesh) is a useful
benchmark input, against a four-part acceptance sketch: define the input
modalities, define the output checks, compare against scripted baselines, and
record latency + iteration count for agentic loops. The deliverable is a design
note only -- so, like ``test_kernel_interop.py``, these tests enforce both that
the note covers the acceptance themes AND that it has NOT smuggled a mesh-gen /
GPU dependency into the core or dev dependency sets.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "MESH_GEOMETRY_COMPILER.md"


def _declared_dependencies() -> list[str]:
    """Every dependency string in pyproject (core + every optional group).

    Parsed without ``tomllib`` so the test runs on the repo's Python 3.10 floor.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    specs: list[str] = []
    for match in re.finditer(r"(?:^|\n)\s*[\w.-]+\s*=\s*(\[.*?\])", text, re.DOTALL):
        try:
            value = ast.literal_eval(match.group(1))
        except (ValueError, SyntaxError):
            continue
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            specs.extend(value)
    return specs


def test_note_exists_and_is_a_design_note_only():
    text = DOC.read_text(encoding="utf-8")
    lower = text.lower()
    assert "design note only" in lower
    assert "no core dependency" in lower
    # The producer/consumer seam framing inherited from KERNEL_INTEROP.
    assert "grade-the-export seam" in text
    assert "producer" in lower and "consumer" in lower


def test_note_covers_the_four_named_input_modalities():
    text = DOC.read_text(encoding="utf-8").lower()
    for needle in (
        "text prompt",
        "concept image",
        "point cloud",
        "scanned object",
    ):
        assert needle in text, f"missing input modality: {needle!r}"


def test_note_covers_the_five_named_output_checks_and_maps_them_to_real_graders():
    text = DOC.read_text(encoding="utf-8")
    # The five acceptance-sketch output checks.
    for needle in (
        "Manifoldness",
        "Topology stability",
        "Decimation behavior",
        "Feature recoverability",
        "CAD variables",
    ):
        assert needle in text, f"missing output check: {needle!r}"
    # Each must tie to a real symbol in the public stack, not hand-wave.
    for symbol in (
        "is_watertight",
        "canonical_sha256",
        "scan_to_brep_parametric",
        "bounding_box_mm",
    ):
        assert symbol in text, f"output check not tied to real grader symbol: {symbol!r}"


def test_note_defines_baseline_comparison_and_loop_telemetry():
    text = DOC.read_text(encoding="utf-8")
    # Acceptance item 3: compare against OpenSCAD/Fusion scripted baselines.
    assert "OpenSCAD" in text
    assert "Fusion" in text
    # Acceptance item 4: latency + iteration count for agentic optimization loops.
    assert "Latency" in text or "latency" in text
    assert "iteration" in text.lower()
    assert "iterations-to-manufacturable" in text


def test_note_is_linked_from_canonical_docs():
    for relative_path in (
        "docs/REVERSE_ENGINEERING.md",
        "docs/KERNEL_INTEROP.md",
        "docs/DOMAIN_MATRIX.md",
        "docs/ROADMAP.md",
    ):
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "MESH_GEOMETRY_COMPILER.md" in text, relative_path


def test_core_profile_adds_no_mesh_generation_or_gpu_dependency():
    """The load-bearing acceptance boundary, enforced against pyproject.

    The note must not have introduced a real mesh-generation / diffusion / GPU
    dependency into the core or dev dependency sets; MeshFlow stays a producer
    behind the seam.
    """
    declared = _declared_dependencies()
    # Sanity: the parse actually found the real dependency list.
    assert any(spec.startswith("trimesh") for spec in declared)

    forbidden = ("meshflow", "torch", "diffusers", "tensorflow", "cuda", "open3d")
    for spec in declared:
        name = re.split(r"[<>=!~\[ ]", spec, maxsplit=1)[0].strip().lower()
        assert name not in forbidden, (
            f"core/dev dependency {spec!r} pulls a mesh-gen/GPU stack the #164 "
            f"evaluation note explicitly forbids; it must stay a producer behind "
            f"the grade-the-export seam"
        )
