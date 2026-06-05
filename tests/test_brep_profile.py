"""Optional build123d / OCCT profile scaffold tests."""

from __future__ import annotations

import importlib.util

from makerbench.brep_profile import build123d_availability, export_smoke_step


def test_brep_profile_imports_without_build123d_dependency():
    availability = build123d_availability()

    assert availability.status in {"available", "unavailable"}
    assert availability.available is (importlib.util.find_spec("build123d") is not None)


def test_smoke_step_export_skips_cleanly_without_build123d(tmp_path):
    if importlib.util.find_spec("build123d") is not None:
        return

    output = tmp_path / "smoke.step"
    result = export_smoke_step(output)

    assert result["status"] == "skipped"
    assert result["available"] is False
    assert "optional_local" in result["reason"]
    assert not output.exists()
