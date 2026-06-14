"""Passive trace <-> geometry consistency check for workflow rows (mb#91).

The workflow track grades the *artifact* — geometry is ground truth — but the
surrounding process is disclosed, not reproduced (see docs/WORKFLOW_TRACK.md). A
bad actor could hand-CAD a flawless part and claim an agentic stack made it. We
never have to *trust* the process to *grade* the result, but a cheap deterrent
raises the cost of a false claim: a real process trace that asserts structural
parameters ("placed 7 pins at 12 mm spacing") should be consistent with what the
graded geometry actually measures ("6 pins at 15 mm"). When it is not, the row is
flagged for community spot-check.

This module is deliberately *disclosure-grade*, exactly like
``dossier_scoring.assess_packet_completeness``: it surfaces inconsistencies for a
reviewer, it never passes or fails a grading level and is never summed into a
score. Geometry remains the single source of truth.
"""

from __future__ import annotations

from .schema import (
    DesignDossier,
    GeometryMeasurement,
    StructuralClaim,
    TraceConsistencyReport,
    TraceFlag,
    WorkflowManifest,
)

# Default tolerances. Counts are exact (a structural element is present or not);
# spacings/dimensions carry a small slack so honest rounding or mesh-measurement
# noise does not flag a faithful trace.
DEFAULT_SPACING_TOL_MM = 0.5
DEFAULT_DIMENSION_TOL_MM = 0.5


def _normalize_feature(feature: str) -> str:
    """Canonical key for matching a claim to a measurement by feature name."""
    return feature.strip().lower().replace(" ", "_").rstrip("s")


def check_trace_consistency(
    manifest: WorkflowManifest,
    measurements: list[GeometryMeasurement],
    *,
    spacing_tol_mm: float = DEFAULT_SPACING_TOL_MM,
    dimension_tol_mm: float = DEFAULT_DIMENSION_TOL_MM,
) -> TraceConsistencyReport:
    """Cross-check a manifest's asserted structural claims against the geometry.

    Each claim is matched to a :class:`GeometryMeasurement` by normalized feature
    name. For a matched pair, any asserted measure (count, spacing, dimension) that
    is also measured is compared; counts must match exactly, spacings/dimensions
    within tolerance. A claimed feature with *no* matching measurement is flagged
    ``feature_absent_in_geometry`` — the trace asserts a structure the graded solid
    does not contain.

    Returns a :class:`TraceConsistencyReport`. ``consistent`` is False iff at least
    one flag was raised. This is a review signal only — never a grading gate.
    """
    by_feature: dict[str, GeometryMeasurement] = {
        _normalize_feature(m.feature): m for m in measurements
    }

    flags: list[TraceFlag] = []
    checked = 0

    for claim in manifest.structural_claims:
        key = _normalize_feature(claim.feature)
        measured = by_feature.get(key)
        if measured is None:
            # A claim with no measure at all is vacuous; only flag absence when the
            # trace actually asserted something structural about the feature.
            if claim.count is None and claim.spacing_mm is None and claim.dimension_mm is None:
                continue
            flags.append(
                TraceFlag(
                    feature=claim.feature,
                    kind="feature_absent_in_geometry",
                    claimed=_first_claimed(claim),
                    measured=None,
                    detail=f"Trace asserts '{claim.feature}' but the graded geometry "
                           "has no matching measured feature.",
                )
            )
            continue

        compared = False

        if claim.count is not None and measured.count is not None:
            compared = True
            if claim.count != measured.count:
                flags.append(
                    TraceFlag(
                        feature=claim.feature,
                        kind="count_mismatch",
                        claimed=float(claim.count),
                        measured=float(measured.count),
                        detail=f"Trace claims {claim.count} {claim.feature}; geometry "
                               f"shows {measured.count}.",
                    )
                )

        if claim.spacing_mm is not None and measured.spacing_mm is not None:
            compared = True
            if abs(claim.spacing_mm - measured.spacing_mm) > spacing_tol_mm:
                flags.append(
                    TraceFlag(
                        feature=claim.feature,
                        kind="spacing_mismatch",
                        claimed=claim.spacing_mm,
                        measured=measured.spacing_mm,
                        detail=f"Trace claims {claim.spacing_mm} mm spacing for "
                               f"{claim.feature}; geometry shows {measured.spacing_mm} mm "
                               f"(tol {spacing_tol_mm} mm).",
                    )
                )

        if claim.dimension_mm is not None and measured.dimension_mm is not None:
            compared = True
            if abs(claim.dimension_mm - measured.dimension_mm) > dimension_tol_mm:
                flags.append(
                    TraceFlag(
                        feature=claim.feature,
                        kind="dimension_mismatch",
                        claimed=claim.dimension_mm,
                        measured=measured.dimension_mm,
                        detail=f"Trace claims {claim.dimension_mm} mm for {claim.feature}; "
                               f"geometry shows {measured.dimension_mm} mm "
                               f"(tol {dimension_tol_mm} mm).",
                    )
                )

        if compared:
            checked += 1

    consistent = not flags
    if consistent:
        detail = (
            f"Trace structural claims consistent with graded geometry "
            f"({checked} claim(s) checked)."
        )
    else:
        kinds = ", ".join(sorted({f.kind for f in flags}))
        detail = (
            f"{len(flags)} trace/geometry inconsistency flag(s) for review ({kinds}). "
            "Disclosure-grade only; does not change the geometry score."
        )
    return TraceConsistencyReport(
        consistent=consistent,
        flags=flags,
        checked_claims=checked,
        detail=detail,
    )


def _first_claimed(claim: StructuralClaim) -> float | None:
    """The first asserted numeric measure on a claim, for flag display."""
    if claim.count is not None:
        return float(claim.count)
    if claim.spacing_mm is not None:
        return claim.spacing_mm
    if claim.dimension_mm is not None:
        return claim.dimension_mm
    return None


def harvest_claims_from_dossier(dossier: DesignDossier | None) -> list[StructuralClaim]:
    """Derive structural claims from a dossier's disclosed evidence (mb#91).

    A trace does not have to restate every structural parameter in
    ``WorkflowManifest.structural_claims`` — the attached dossier already discloses
    dimensional self-checks (``verification.self_checks`` with a numeric ``metric``
    on a ``dimensional_assertion``) and BOM ``critical_dimensions``. This helper
    lifts those into :class:`StructuralClaim` objects so the same passive checker
    can cross-check them against the geometry. Returns ``[]`` for an absent dossier.
    """
    if dossier is None:
        return []

    claims: list[StructuralClaim] = []

    verification = dossier.verification
    if verification is not None:
        for check in verification.self_checks:
            if check.category != "dimensional_assertion" or check.metric is None:
                continue
            claims.append(
                StructuralClaim(
                    feature=check.name or "dimensional_assertion",
                    dimension_mm=float(check.metric),
                    source="dossier_self_check",
                )
            )

    for item in dossier.bom:
        for dim_name, value in item.critical_dimensions.items():
            claims.append(
                StructuralClaim(
                    feature=f"{item.item_id}:{dim_name}",
                    dimension_mm=float(value),
                    source="bom",
                )
            )

    return claims
