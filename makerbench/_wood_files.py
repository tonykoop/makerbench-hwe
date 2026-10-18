"""Public CAD delivery names and role manifests; no FreeCAD dependency."""

import re

PARTS = ("top", "front", "back", "left", "right", "bottom")
SCHEMA = "makerbench-cad-delivery-v1"
STEM = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*_[a-z0-9]+(?:-[a-z0-9]+)*_[a-z0-9]+(?:-[a-z0-9]+)*")


def delivery_files(spec, revision="v2"):
    length, width, _ = spec["size_mm"]
    stem = f"tongue-drum_10t-{length:g}x{width:g}_{revision}"
    return {
        "assembly": f"{stem}_assembly.FCStd",
        **{part: f"parts/{stem}_{part}.FCStd" for part in PARTS},
        "cut-list": f"{stem}_cut-list.csv",
        "joints": "joints.json",
    }


def validate_files(files):
    if not isinstance(files, dict) or set(files) != {*PARTS, "assembly", "cut-list", "joints"}:
        raise ValueError(
            "CAD manifest must assign exactly assembly, six part roles, cut-list and joints"
        )
    assembly = files.get("assembly")
    if not isinstance(assembly, str) or not assembly.endswith("_assembly.FCStd"):
        raise ValueError("CAD naming: expected <instrument>_<variant>_<revision>_assembly.FCStd")
    stem = assembly.removesuffix("_assembly.FCStd")
    if not STEM.fullmatch(stem):
        raise ValueError(
            "CAD naming: use lowercase instrument_variant_revision fields with hyphens inside fields"
        )
    expected = {
        "assembly": assembly,
        **{part: f"parts/{stem}_{part}.FCStd" for part in PARTS},
        "cut-list": f"{stem}_cut-list.csv",
        "joints": "joints.json",
    }
    if files != expected:
        raise ValueError(
            "CAD naming: all part and cut-list files must share the assembly stem and declared role suffix"
        )
    return dict(files)


def manifest(files):
    return {"schema": SCHEMA, "files": validate_files(files)}
