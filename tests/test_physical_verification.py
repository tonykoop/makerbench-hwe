"""Physical Verification Track (mb#112): fabrication multiplier contract tests.

These cover the disclosed-not-proven honesty rules (no evidence -> no credit),
the multiplier derivation, composition onto WorkflowManifest, the .mbc certificate
carry, and the schema-export round-trip — without touching any geometry grading.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from makerbench.certificate import MbcPayload, build_certificate, verify_mbc
from makerbench.schema import (
    FABRICATION_STAGE_BONUS,
    PHYSICAL_VERIFICATION_STAGE_ORDER,
    AlphaBuild,
    BetaInspection,
    FabricationEvidence,
    PhysicalVerificationTrack,
    ProductionMaster,
    WorkflowManifest,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _alpha(disclosed: bool = True) -> AlphaBuild:
    ev = FabricationEvidence(
        stage="alpha",
        role="assembly_photo",
        format="png",
        evidence_sha256=("a" * 64) if disclosed else None,
    )
    return AlphaBuild(process="fdm", makerspace_optimized=True, evidence=[ev])


def _beta() -> BetaInspection:
    ev = FabricationEvidence(
        stage="beta",
        role="cmm_report",
        format="pdf",
        evidence_url="https://example.com/cmm.pdf",
    )
    return BetaInspection(vendor="xometry", process="cnc_milling", evidence=[ev])


def _production() -> ProductionMaster:
    ev = FabricationEvidence(
        stage="production",
        role="gdt_drawing",
        format="pdf",
        evidence_sha256="d" * 64,
    )
    return ProductionMaster(bom_finalized=True, gdt_finalized=True, hard_tooling=True, evidence=[ev])


# --------------------------------------------------------------------------- #
# Multiplier derivation                                                        #
# --------------------------------------------------------------------------- #


def test_empty_track_earns_no_credit():
    pvt = PhysicalVerificationTrack(task_id="t", seed=0)
    assert pvt.highest_verified_stage() is None
    assert pvt.attained_stages() == []
    assert pvt.fabrication_multiplier() == 1.0
    assert pvt.credited_score(3) == 3


def test_alpha_only_multiplier():
    pvt = PhysicalVerificationTrack(task_id="t", seed=0, alpha=_alpha())
    assert pvt.highest_verified_stage() == "alpha"
    assert pvt.fabrication_multiplier() == 1.05
    assert pvt.credited_score(4) == 4.2


def test_beta_only_multiplier():
    pvt = PhysicalVerificationTrack(task_id="t", seed=0, beta=_beta())
    assert pvt.fabrication_multiplier() == 1.15


def test_production_only_multiplier():
    pvt = PhysicalVerificationTrack(task_id="t", seed=0, production=_production())
    assert pvt.fabrication_multiplier() == 1.30


def test_highest_stage_wins_not_product():
    # alpha + beta + production attained -> only production's bonus, not stacked.
    pvt = PhysicalVerificationTrack(
        task_id="t", seed=0, alpha=_alpha(), beta=_beta(), production=_production()
    )
    assert pvt.attained_stages() == ["alpha", "beta", "production"]
    assert pvt.highest_verified_stage() == "production"
    assert pvt.fabrication_multiplier() == 1.30  # not 1.05*1.15*1.30


def test_stages_are_independent_skip_alpha():
    # A row can go straight to a shop without an alpha print.
    pvt = PhysicalVerificationTrack(task_id="t", seed=0, beta=_beta())
    assert pvt.attained_stages() == ["beta"]
    assert pvt.highest_verified_stage() == "beta"


# --------------------------------------------------------------------------- #
# Honesty: evidence must be disclosed to count                                 #
# --------------------------------------------------------------------------- #


def test_stage_without_disclosed_evidence_earns_nothing():
    # A stage present but with no hash/URL on its evidence is not "attained".
    pvt = PhysicalVerificationTrack(task_id="t", seed=0, alpha=_alpha(disclosed=False))
    assert pvt.alpha is not None and pvt.alpha.evidence  # the claim exists...
    assert pvt.attained_stages() == []  # ...but it is not disclosed, so no credit
    assert pvt.fabrication_multiplier() == 1.0


def test_stage_with_empty_evidence_earns_nothing():
    pvt = PhysicalVerificationTrack(
        task_id="t", seed=0, beta=BetaInspection(vendor="fictiv")
    )
    assert pvt.attained_stages() == []
    assert pvt.fabrication_multiplier() == 1.0


def test_evidence_is_disclosed_predicate():
    assert FabricationEvidence(stage="alpha", role="r", format="png", evidence_sha256="x").is_disclosed
    assert FabricationEvidence(stage="alpha", role="r", format="png", evidence_url="u").is_disclosed
    assert not FabricationEvidence(stage="alpha", role="r", format="png").is_disclosed


def test_bonus_table_matches_stage_order():
    assert set(FABRICATION_STAGE_BONUS) == set(PHYSICAL_VERIFICATION_STAGE_ORDER)
    bonuses = [FABRICATION_STAGE_BONUS[s] for s in PHYSICAL_VERIFICATION_STAGE_ORDER]
    assert bonuses == sorted(bonuses)  # higher tier never earns less


# --------------------------------------------------------------------------- #
# Composition + serialization                                                  #
# --------------------------------------------------------------------------- #


def test_attaches_to_workflow_manifest_and_round_trips():
    pvt = PhysicalVerificationTrack(task_id="vented_plate", seed=0, alpha=_alpha(), beta=_beta())
    manifest = WorkflowManifest(task_id="vented_plate", seed=0, physical_verification=pvt)
    dumped = manifest.model_dump(mode="json")
    reloaded = WorkflowManifest.model_validate(dumped)
    assert reloaded.physical_verification is not None
    assert reloaded.physical_verification.fabrication_multiplier() == 1.15


def test_manifest_without_pvt_defaults_none():
    manifest = WorkflowManifest(task_id="t", seed=0)
    assert manifest.physical_verification is None


# --------------------------------------------------------------------------- #
# .mbc certificate carries a tamper-evident multiplier                         #
# --------------------------------------------------------------------------- #


def test_certificate_carries_and_signs_fabrication_credit():
    pvt = PhysicalVerificationTrack(task_id="vented_plate", seed=0, production=_production())
    payload = MbcPayload(
        task_id="vented_plate",
        seed=0,
        score=4,
        passed=True,
        artifact_fingerprint="a" * 64,
        timestamp="2026-06-14T00:00:00Z",
        nonce="n-1",
        fabrication_stage=pvt.highest_verified_stage(),
        fabrication_multiplier=pvt.fabrication_multiplier(),
    )
    assert payload.fabrication_stage == "production"
    assert payload.fabrication_multiplier == 1.30

    key = "k"
    cert = build_certificate(payload, key)
    assert verify_mbc(cert, key)

    # Tampering with the multiplier breaks the signature.
    mutated = cert.model_dump(mode="json")
    mutated["payload"]["fabrication_multiplier"] = 1.30 * 10
    assert not verify_mbc(mutated, key)


def test_certificate_fabrication_fields_optional():
    payload = MbcPayload(
        task_id="t",
        seed=0,
        score=2,
        passed=True,
        artifact_fingerprint="a" * 64,
        timestamp="2026-06-14T00:00:00Z",
        nonce="n",
    )
    assert payload.fabrication_stage is None
    assert payload.fabrication_multiplier is None


# --------------------------------------------------------------------------- #
# Schema export is committed and current                                       #
# --------------------------------------------------------------------------- #


def test_exported_schemas_are_current():
    result = subprocess.run(
        [sys.executable, "scripts/export_workflow_schemas.py", "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(REPO_ROOT)},
    )
    assert result.returncode == 0, result.stderr


def test_example_manifest_includes_physical_verification():
    example = json.loads(
        (REPO_ROOT / "schemas" / "examples" / "workflow_manifest.example.json").read_text()
    )
    pv = example.get("physical_verification")
    assert pv is not None
    assert pv["alpha"]["makerspace_optimized"] is True
    # Round-trips through the model and yields the documented multiplier.
    pvt = PhysicalVerificationTrack.model_validate(pv)
    assert pvt.fabrication_multiplier() == 1.15  # beta is the highest evidenced stage
