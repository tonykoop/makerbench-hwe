"""CADCLAW micro-DFM adapter (mb#78).

CADCLAW/MARB grades *macro-assembly* readiness with black-box STEP gates
(inventory, interference, adjacency, floating parts) as assemblies climb their
L1–L7 ladder. It does **not** grade the micro-fabrication layer: whether each
placed part is actually manufacturable (public-safe, structurally scoreable
exchange geometry). MakerBench-HWE's deterministic component scorer
(``makerbench_core.score_file``) does exactly that.

This adapter is the seam. It takes a CADCLAW assembly — a set of per-part STEP
(or other exchange) artifacts — runs MakerBench's portable component score on
each part, and rolls the per-part results up into a single assembly-level
sub-grade that slots into CADCLAW's pytest-style gate model: a boolean
``gate_passed`` plus a numeric score and per-part evidence.

Design properties (inherited from :class:`~makerbench.adapters.base.BaseAdapter`
and enforced here):

* **Oracle-free** — only calls the public ``score_file`` scorer; never reads
  ``private/oracles/`` or any held-out seed.
* **No LLM/VLM judge** — every rule is a deterministic structural check.
* **No task generation** — a MARB user runs micro-DFM on parts they already
  have; they do not adopt MakerBench task generation.

Because the only dependency is the portable ``makerbench_core`` component, the
adapter lives in **core** rather than an optional plugin: there is no heavy CAD
kernel or oracle coupling to quarantine.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from makerbench_core import score_file as _default_score_file

from .base import BaseAdapter

#: Per-part pass threshold. Mirrors ``makerbench_core``'s own ``passed`` gate
#: (required rules pass AND score >= 80) so a part that the component scorer
#: calls "passed" is the same part this adapter counts as passing.
DEFAULT_PART_PASS_THRESHOLD = 80.0
#: Assembly weighted-mean threshold used by the soft (``require_all_parts=False``)
#: rollup. With the default strict rollup the gate is governed by per-part passes.
DEFAULT_ASSEMBLY_PASS_THRESHOLD = 80.0


@dataclass(frozen=True)
class AssemblyPart:
    """One part of a CADCLAW assembly to be micro-DFM scored.

    ``artifact_path`` points at a public exchange artifact (STEP/STL/OBJ/OFF/
    SCAD/SVG/DXF) — the same surface CADCLAW already exports per part. ``quantity``
    weights the part in the assembly-score rollup (4 identical brackets count 4x).
    ``metadata`` is opaque passthrough (e.g. CADCLAW node id, material) echoed
    into the report for traceability; it is never interpreted as a grade input.
    """

    part_id: str
    artifact_path: str
    quantity: int = 1
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.part_id).strip():
            raise ValueError("part_id must be a non-empty string")
        if not str(self.artifact_path).strip():
            raise ValueError("artifact_path must be a non-empty string")
        if int(self.quantity) < 1:
            raise ValueError("quantity must be >= 1")


@dataclass(frozen=True)
class PartComponentReport:
    """Per-part micro-DFM result rolled up into the assembly sub-grade."""

    part_id: str
    quantity: int
    artifact_path: str
    artifact_format: str
    makerbench_dfm_score: float
    passed: bool
    profile: str
    sha256: str | None
    failed_rules: list[dict[str, str]]
    metadata: dict[str, Any]
    component_score: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AssemblySubGrade:
    """Assembly-level sub-grade CADCLAW consumes in its pytest-style gate model.

    ``gate_passed`` is the single boolean a CADCLAW gate asserts on;
    ``assembly_score`` is the quantity-weighted mean micro-DFM percentage for
    display/ranking. The ``parts`` list carries the per-part evidence so a failing
    assembly points straight at the un-manufacturable part(s).
    """

    schema_version: str
    adapter_id: str
    ecosystem: str
    assembly_id: str
    profile: str
    part_pass_threshold: float
    assembly_pass_threshold: float
    require_all_parts: bool
    assembly_score: float
    min_part_score: float
    parts_total: int
    parts_passed: int
    parts_failed: int
    failed_part_ids: list[str]
    gate_passed: bool
    gate_reason: str
    parts: list[PartComponentReport]
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["assembly_score"] = round(float(self.assembly_score), 1)
        payload["min_part_score"] = round(float(self.min_part_score), 1)
        return payload

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    def as_cadclaw_gate(self, *, gate_id: str = "makerbench.microdfm") -> dict[str, Any]:
        """Project the sub-grade into CADCLAW's pytest-style gate contract.

        Returns the minimal shape a CADCLAW gate consumes: a stable ``gate_id``,
        the boolean ``passed``, the numeric ``score``/``max_score``, a one-line
        ``reason``, and the failing ``parts`` to surface in the gate report.
        """

        return {
            "gate_id": gate_id,
            "adapter_id": self.adapter_id,
            "schema_version": self.schema_version,
            "passed": self.gate_passed,
            "score": round(float(self.assembly_score), 1),
            "max_score": 100.0,
            "reason": self.gate_reason,
            "failed_part_ids": list(self.failed_part_ids),
            "deterministic": True,
            "oracle_access": False,
            "llm_judge": False,
        }


class CADCLAWAdapter(BaseAdapter):
    """Expose MakerBench micro-DFM as an assembly sub-grade gate for CADCLAW/MARB.

    Construct once with the desired thresholds and rollup policy, then call
    :meth:`evaluate` with the assembly's parts. The scorer is injectable
    (``scorer=``) purely so tests can run without writing real CAD files; the
    default is the portable :func:`makerbench_core.score_file`.
    """

    adapter_id = "cadclaw-microdfm-v1"
    ecosystem = "cadclaw"
    schema_version = "makerbench-adapter-report-v1"

    def __init__(
        self,
        *,
        part_pass_threshold: float = DEFAULT_PART_PASS_THRESHOLD,
        assembly_pass_threshold: float = DEFAULT_ASSEMBLY_PASS_THRESHOLD,
        require_all_parts: bool = True,
        profile: str = "portable-dfm-v1",
        scorer: Callable[..., Any] = _default_score_file,
    ) -> None:
        if not 0.0 <= part_pass_threshold <= 100.0:
            raise ValueError("part_pass_threshold must be within [0, 100]")
        if not 0.0 <= assembly_pass_threshold <= 100.0:
            raise ValueError("assembly_pass_threshold must be within [0, 100]")
        self.part_pass_threshold = float(part_pass_threshold)
        self.assembly_pass_threshold = float(assembly_pass_threshold)
        self.require_all_parts = bool(require_all_parts)
        self.profile = profile
        self._scorer = scorer

    def evaluate(
        self,
        parts: Iterable[AssemblyPart | Mapping[str, Any] | str | os.PathLike[str]],
        *,
        assembly_id: str = "assembly",
    ) -> AssemblySubGrade:
        """Score every part and roll the results into an assembly sub-grade.

        ``parts`` accepts :class:`AssemblyPart` instances, plain dict specs, or
        bare artifact paths (str/``PathLike``) — the bare-path form derives a
        ``part_id`` from the file stem so CADCLAW's STEP-in list maps in directly.
        Raises :class:`ValueError` on an empty assembly; a missing/unreadable part
        file does *not* raise — the component scorer deterministically fails it.
        """

        coerced = [self._coerce_part(part) for part in parts]
        if not coerced:
            raise ValueError("assembly has no parts to score")

        reports = [self._score_part(part) for part in coerced]

        total_qty = sum(report.quantity for report in reports)
        weighted = sum(report.makerbench_dfm_score * report.quantity for report in reports)
        assembly_score = weighted / total_qty if total_qty else 0.0
        min_part_score = min(report.makerbench_dfm_score for report in reports)
        failed = [report for report in reports if not report.passed]
        failed_ids = [report.part_id for report in failed]

        if self.require_all_parts:
            gate_passed = not failed
            if gate_passed:
                gate_reason = (
                    f"all {len(reports)} part(s) passed micro-DFM "
                    f"(>= {self.part_pass_threshold:.0f}%)"
                )
            else:
                gate_reason = (
                    f"{len(failed)}/{len(reports)} part(s) failed micro-DFM: "
                    + ", ".join(failed_ids)
                )
        else:
            gate_passed = assembly_score >= self.assembly_pass_threshold
            gate_reason = (
                f"assembly score {assembly_score:.1f}% "
                f"{'>=' if gate_passed else '<'} {self.assembly_pass_threshold:.0f}% "
                f"(weighted mean over {len(reports)} part(s))"
            )

        return AssemblySubGrade(
            schema_version=self.schema_version,
            adapter_id=self.adapter_id,
            ecosystem=self.ecosystem,
            assembly_id=assembly_id,
            profile=self.profile,
            part_pass_threshold=self.part_pass_threshold,
            assembly_pass_threshold=self.assembly_pass_threshold,
            require_all_parts=self.require_all_parts,
            assembly_score=round(assembly_score, 1),
            min_part_score=round(min_part_score, 1),
            parts_total=len(reports),
            parts_passed=len(reports) - len(failed),
            parts_failed=len(failed),
            failed_part_ids=failed_ids,
            gate_passed=gate_passed,
            gate_reason=gate_reason,
            parts=reports,
            provenance={
                "oracle_access": False,
                "llm_judge": False,
                "generates_tasks": False,
                "deterministic": True,
                "scorer": "makerbench_core.score_file",
                "profile": self.profile,
            },
        )

    def _score_part(self, part: AssemblyPart) -> PartComponentReport:
        result = self._scorer(part.artifact_path, profile=self.profile)
        score_dict = result.to_dict()
        score = float(score_dict["makerbench_dfm_score"])
        # A part passes when the component scorer's own gate passes *and* the
        # score clears this adapter's (possibly stricter) part threshold.
        passed = bool(score_dict.get("passed")) and score >= self.part_pass_threshold
        failed_rules = [
            {"id": rule["id"], "detail": rule.get("detail", "")}
            for rule in score_dict.get("rules", [])
            if not rule.get("passed")
        ]
        return PartComponentReport(
            part_id=part.part_id,
            quantity=part.quantity,
            artifact_path=part.artifact_path,
            artifact_format=score_dict.get("input", {}).get("format", "unknown"),
            makerbench_dfm_score=score,
            passed=passed,
            profile=score_dict.get("profile", self.profile),
            sha256=score_dict.get("input", {}).get("sha256"),
            failed_rules=failed_rules,
            metadata=dict(part.metadata),
            component_score=score_dict,
        )

    @staticmethod
    def _coerce_part(
        part: AssemblyPart | Mapping[str, Any] | str | os.PathLike[str],
    ) -> AssemblyPart:
        if isinstance(part, AssemblyPart):
            return part
        if isinstance(part, Mapping):
            return AssemblyPart(
                part_id=str(part["part_id"]),
                artifact_path=str(part["artifact_path"]),
                quantity=int(part.get("quantity", 1)),
                metadata=dict(part.get("metadata", {})),
            )
        if isinstance(part, (str, os.PathLike)):
            path = Path(os.fspath(part))
            return AssemblyPart(part_id=path.stem or str(path), artifact_path=str(path))
        raise TypeError(f"unsupported part spec: {type(part).__name__}")

    def describe(self) -> dict[str, Any]:
        meta = super().describe()
        meta.update(
            {
                "consumes": "cadclaw-assembly-parts",
                "emits": "assembly-sub-grade",
                "part_pass_threshold": self.part_pass_threshold,
                "assembly_pass_threshold": self.assembly_pass_threshold,
                "require_all_parts": self.require_all_parts,
                "profile": self.profile,
            }
        )
        return meta
