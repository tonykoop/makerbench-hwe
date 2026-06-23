"""Blender BOM / lifecycle-state validation helpers.

Validates that a Blender scene's metadata-assignment report (objects mapped to
vendor parts with lifecycle states) is consistent with a reference scene
inventory and vendor-parts table.

Ported from archived tonykoop/makerbench #180 (fail-closed hardening).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LifecycleValidationResult:
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    errors: tuple[str, ...] = ()


def _malformed(reason: str) -> LifecycleValidationResult:
    """Return a fail-closed result for malformed input — never raises."""
    return LifecycleValidationResult(passed=False, checks={}, errors=(reason,))


def validate_metadata_assignment_report(
    report: object,
    scene_inventory: object,
    vendor_parts: object,
) -> LifecycleValidationResult:
    """Validate a Blender metadata-assignment report against scene inventory and vendor parts.

    Returns a fail-closed ``LifecycleValidationResult`` if any input is not a
    mapping, is missing expected keys, or contains malformed entries.  Callers
    must not rely on exceptions for error detection.

    Args:
        report: dict mapping scene-object names to assignment records.
        scene_inventory: dict with an ``"objects"`` list of scene-object stubs.
        vendor_parts: dict with a ``"parts"`` list of vendor-part records.

    Returns:
        ``LifecycleValidationResult`` with ``passed=True`` iff all checks pass.
    """
    if not isinstance(report, dict):
        return _malformed("report is not a mapping")
    if not isinstance(scene_inventory, dict):
        return _malformed("scene_inventory is not a mapping")
    if not isinstance(vendor_parts, dict):
        return _malformed("vendor_parts is not a mapping")

    raw_objects = scene_inventory.get("objects")
    if not isinstance(raw_objects, list):
        return _malformed("scene_inventory missing 'objects' list")

    raw_parts = vendor_parts.get("parts")
    if not isinstance(raw_parts, list):
        return _malformed("vendor_parts missing 'parts' list")

    known_vendor_keys: set[str] = set()
    for part in raw_parts:
        if not isinstance(part, dict):
            return _malformed("vendor_parts entry is not a mapping")
        vk = part.get("vendor_key")
        if not isinstance(vk, str):
            return _malformed("vendor_parts entry missing string 'vendor_key'")
        known_vendor_keys.add(vk)

    scene_objects: list[dict] = []
    for obj in raw_objects:
        if not isinstance(obj, dict):
            return _malformed("scene_inventory object entry is not a mapping")
        name = obj.get("name")
        if not isinstance(name, str):
            return _malformed("scene_inventory object missing string 'name'")
        scene_objects.append(obj)

    checks: dict[str, bool] = {}
    errors: list[str] = []

    for obj in scene_objects:
        name = obj["name"]
        vk = obj.get("vendor_key")
        if vk is None:
            checks[f"{name}.unmatched_ok"] = name not in report
            if name in report:
                errors.append(f"{name}: unmatched object has an assignment record")
            continue

        assignment = report.get(name)
        if assignment is None:
            checks[f"{name}.assigned"] = False
            errors.append(f"{name}: no assignment record for object with vendor_key={vk!r}")
            continue

        if not isinstance(assignment, dict):
            return _malformed(f"{name}: assignment record is not a mapping")

        checks[f"{name}.assigned"] = True

        assigned_vk = assignment.get("vendor_key")
        checks[f"{name}.vendor_key_match"] = assigned_vk == vk
        if assigned_vk != vk:
            errors.append(
                f"{name}: vendor_key mismatch — expected {vk!r}, got {assigned_vk!r}"
            )

        lifecycle_state = assignment.get("lifecycle_state")
        checks[f"{name}.lifecycle_state_present"] = isinstance(lifecycle_state, str)
        if not isinstance(lifecycle_state, str):
            errors.append(f"{name}: missing or non-string 'lifecycle_state'")

        if not isinstance(assignment.get("collection"), str):
            checks[f"{name}.collection_present"] = False
            errors.append(f"{name}: missing or non-string 'collection'")
        else:
            checks[f"{name}.collection_present"] = True

        if assigned_vk and assigned_vk not in known_vendor_keys:
            checks[f"{name}.vendor_key_known"] = False
            errors.append(f"{name}: vendor_key {assigned_vk!r} not in vendor_parts")
        elif assigned_vk:
            checks[f"{name}.vendor_key_known"] = True

    passed = len(errors) == 0
    return LifecycleValidationResult(passed=passed, checks=checks, errors=tuple(errors))
