"""Stub adapter: CADTestBench → CommonResult (#242).

CADTestBench focuses on parametric CAD generation and editability: it tests
whether an LLM-generated CAD program (OpenSCAD/CadQuery/build123d) is:
(a) executable, (b) geometrically correct against a reference, and
(c) editable (parameters can be modified and the model regenerates correctly).

Reference schema (CADTestBench result, v1)
-------------------------------------------
{
  "submission_id": "<string>",
  "language": "openscad" | "cadquery" | "build123d",
  "tests": [
    {
      "test_id": "<string>",
      "executable": <bool>,
      "geometry_match": <float 0-1 | null>,
      "editability": <float 0-1 | null>
    }
  ],
  "overall_score": <float 0-1 | null>
}
"""

from __future__ import annotations

from typing import Any

from .base import BaseInteropAdapter, CommonResult


_LANG_AXIS = {
    "openscad": "code-cad",
    "cadquery": "code-cad",
    "build123d": "code-cad",
}


class CADTestBenchAdapter(BaseInteropAdapter):
    """Map a CADTestBench result dict → :class:`~landscape.adapters.CommonResult`."""

    source_benchmark = "CADTestBench"
    description = (
        "Maps a CADTestBench parametric-CAD result (executability, geometry_match, "
        "editability per test, overall_score) to CommonResult."
    )
    axis_coverage = ["code-cad", "spatial-intelligence"]

    _MANDATORY = ("submission_id",)

    def adapt(self, raw_result: dict[str, Any]) -> CommonResult:
        self._validate(raw_result)

        artifact_id = str(raw_result["submission_id"])
        overall = _to_float(raw_result.get("overall_score"))

        tests = raw_result.get("tests", [])
        n = len(tests)
        executable_count = sum(1 for t in tests if t.get("executable", False))

        geo_scores = [_to_float(t.get("geometry_match")) for t in tests]
        geo_scores = [s for s in geo_scores if s is not None]
        avg_geo = sum(geo_scores) / len(geo_scores) if geo_scores else None

        edit_scores = [_to_float(t.get("editability")) for t in tests]
        edit_scores = [s for s in edit_scores if s is not None]
        avg_edit = sum(edit_scores) / len(edit_scores) if edit_scores else None

        if overall is None:
            sub = [s for s in [avg_geo, avg_edit] if s is not None]
            overall = sum(sub) / len(sub) if sub else None

        language = raw_result.get("language", "unknown")
        dimensions: dict[str, Any] = {
            "language": language,
            "test_count": n,
            "executable_count": executable_count,
            "executability_rate": executable_count / n if n else None,
            "avg_geometry_match": avg_geo,
            "avg_editability": avg_edit,
            "overall_score": overall,
        }

        axis = set(self.axis_coverage)
        mapped = _LANG_AXIS.get(language)
        if mapped:
            axis.add(mapped)

        return CommonResult(
            source_benchmark=self.source_benchmark,
            artifact_id=artifact_id,
            score=overall,
            dimensions=dimensions,
            axis_coverage=sorted(axis),
            grading_transparency=True,   # executability + geometry match are deterministic
            raw=raw_result,
        )

    # ------------------------------------------------------------------

    def _validate(self, raw: dict[str, Any]) -> None:
        for f in self._MANDATORY:
            if f not in raw:
                raise ValueError(
                    f"CADTestBenchAdapter: required field {f!r} missing from result"
                )


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
