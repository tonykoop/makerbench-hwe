"""Deterministic local manufacturing-cost estimates.

This module prices the manufactured part, not the LLM run that produced it.
It deliberately stops at transparent formula estimates: no live quoting APIs,
no vendor promises, and no leaderboard score semantics.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, fields
from typing import Any, Literal, TypeVar

ManufacturingProcess = Literal["cnc_milling", "sheet_laser", "additive_3d_print"]

_T = TypeVar("_T")


def _from_mapping(cls: type[_T], data: dict[str, Any]) -> _T:
    """Build a dataclass from a JSON-style mapping, single-source friendly.

    Only declared ``init`` fields are read, so a caller (e.g. an HWE-Pipeline
    profile registry) may carry extra annotation keys without breaking. Tuple
    fields are restored from JSON lists.
    """

    tuple_fields = {f.name for f in fields(cls) if f.type == "tuple[str, ...]"}
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if not f.init or f.name not in data:
            continue
        value = data[f.name]
        if f.name in tuple_fields and isinstance(value, list):
            value = tuple(value)
        kwargs[f.name] = value
    return cls(**kwargs)


def _to_mapping(obj: object) -> dict[str, Any]:
    """JSON-friendly mapping: tuples become lists; everything else verbatim."""

    payload: dict[str, Any] = {}
    for f in fields(obj):  # type: ignore[arg-type]
        value = getattr(obj, f.name)
        payload[f.name] = list(value) if isinstance(value, tuple) else value
    return payload


@dataclass(frozen=True)
class GeometryCostMetrics:
    """Public geometry/feature metrics a local costing pass can consume.

    Callers may derive these from meshes, vectors, B-rep summaries, or dossier
    metadata. Unknown metrics should stay at zero rather than being guessed.
    """

    material_volume_mm3: float = 0.0
    removed_volume_mm3: float = 0.0
    cut_length_mm: float = 0.0
    bend_count: int = 0
    hole_count: int = 0
    setup_count: int = 1
    tool_change_count: int = 0
    sheet_area_mm2: float = 0.0
    sheet_nesting_yield: float = 1.0
    print_time_minutes: float = 0.0
    support_material_volume_mm3: float = 0.0

    def __post_init__(self) -> None:
        for name, value in _public_numbers(self).items():
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if not 0 < self.sheet_nesting_yield <= 1:
            raise ValueError("sheet_nesting_yield must be > 0 and <= 1")

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly mapping for single-sourcing across tools/repos."""
        return _to_mapping(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GeometryCostMetrics":
        """Rebuild metrics from a mapping, ignoring unknown annotation keys."""
        return _from_mapping(cls, data)


@dataclass(frozen=True)
class ProcessRateProfile:
    """Rates and formula knobs for one local manufacturing-cost profile."""

    process_id: ManufacturingProcess
    profile_id: str
    material_id: str
    currency: str = "USD"
    source: str = "local-estimate"
    material_usd_per_cm3: float = 0.0
    sheet_material_usd_per_cm2: float = 0.0
    machine_usd_per_hour: float = 0.0
    removal_rate_cm3_per_min: float | None = None
    setup_fee_usd: float = 0.0
    setup_time_minutes: float = 0.0
    tool_change_time_minutes: float = 0.0
    cut_speed_mm_per_min: float | None = None
    bend_usd_each: float = 0.0
    hole_usd_each: float = 0.0
    print_usd_per_hour: float = 0.0
    consumable_usd_per_cm3: float = 0.0
    min_job_fee_usd: float = 0.0
    overhead_multiplier: float = 1.0
    assumptions: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise ValueError("profile_id is required")
        if not self.material_id:
            raise ValueError("material_id is required")
        if not self.currency:
            raise ValueError("currency is required")
        for name, value in _public_numbers(self).items():
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.overhead_multiplier < 1:
            raise ValueError("overhead_multiplier must be >= 1")
        if self.removal_rate_cm3_per_min is not None and self.removal_rate_cm3_per_min <= 0:
            raise ValueError("removal_rate_cm3_per_min must be positive when set")
        if self.cut_speed_mm_per_min is not None and self.cut_speed_mm_per_min <= 0:
            raise ValueError("cut_speed_mm_per_min must be positive when set")

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly mapping a downstream pipeline can persist/single-source."""
        return _to_mapping(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProcessRateProfile":
        """Rebuild a rate profile from a mapping, ignoring unknown keys."""
        return _from_mapping(cls, data)


@dataclass(frozen=True)
class CostLineItem:
    """One transparent cost formula contribution."""

    name: str
    category: str
    quantity: float
    unit: str
    unit_cost_usd: float
    subtotal_usd: float
    formula: str
    assumptions: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name, value in _public_numbers(self).items():
            if value < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True)
class ManufacturingCostEstimate:
    """Transparent local estimate output from a CostingAdapter."""

    process_id: ManufacturingProcess
    profile_id: str
    material_id: str
    currency: str
    line_items: tuple[CostLineItem, ...]
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    estimate_not_quote: bool = True
    total_usd: float = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "total_usd",
            _round_money(sum(item.subtotal_usd for item in self.line_items)),
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation for CLIs or future manifests."""
        return {
            "process_id": self.process_id,
            "profile_id": self.profile_id,
            "material_id": self.material_id,
            "currency": self.currency,
            "estimate_not_quote": self.estimate_not_quote,
            "total_usd": self.total_usd,
            "assumptions": list(self.assumptions),
            "line_items": [
                {
                    "name": item.name,
                    "category": item.category,
                    "quantity": item.quantity,
                    "unit": item.unit,
                    "unit_cost_usd": item.unit_cost_usd,
                    "subtotal_usd": item.subtotal_usd,
                    "formula": item.formula,
                    "assumptions": list(item.assumptions),
                }
                for item in self.line_items
            ],
        }


class CostingAdapter(ABC):
    """Interface for deterministic local manufacturing-cost estimators."""

    process_id: ManufacturingProcess

    @abstractmethod
    def estimate(
        self,
        metrics: GeometryCostMetrics,
        profile: ProcessRateProfile,
    ) -> ManufacturingCostEstimate:
        """Map geometry metrics and rates to an itemized local estimate."""

    def _check_profile(self, profile: ProcessRateProfile) -> None:
        if profile.process_id != self.process_id:
            raise ValueError(
                f"{self.__class__.__name__} requires process_id={self.process_id!r}, "
                f"got {profile.process_id!r}"
            )

    def _finish(
        self,
        profile: ProcessRateProfile,
        items: list[CostLineItem],
        assumptions: tuple[str, ...],
    ) -> ManufacturingCostEstimate:
        if profile.overhead_multiplier > 1:
            subtotal = sum(item.subtotal_usd for item in items)
            overhead = subtotal * (profile.overhead_multiplier - 1)
            items.append(_item(
                "overhead multiplier",
                "overhead",
                profile.overhead_multiplier - 1,
                "multiplier_delta",
                subtotal,
                overhead,
                "subtotal * (overhead_multiplier - 1)",
            ))
        subtotal = sum(item.subtotal_usd for item in items)
        if profile.min_job_fee_usd > subtotal:
            items.append(_item(
                "minimum job fee adjustment",
                "minimum",
                1,
                "job",
                profile.min_job_fee_usd - subtotal,
                profile.min_job_fee_usd - subtotal,
                "max(min_job_fee_usd - subtotal, 0)",
            ))
        return ManufacturingCostEstimate(
            process_id=profile.process_id,
            profile_id=profile.profile_id,
            material_id=profile.material_id,
            currency=profile.currency,
            line_items=tuple(items),
            assumptions=(
                "local deterministic formula estimate; not a vendor quote",
                *profile.assumptions,
                *assumptions,
            ),
        )


class CncCostingAdapter(CostingAdapter):
    """CNC-style estimate from material, removal time, setup, and tool changes."""

    process_id: ManufacturingProcess = "cnc_milling"

    def estimate(
        self,
        metrics: GeometryCostMetrics,
        profile: ProcessRateProfile,
    ) -> ManufacturingCostEstimate:
        self._check_profile(profile)
        items: list[CostLineItem] = []
        material_cm3 = metrics.material_volume_mm3 / 1000.0
        removed_cm3 = metrics.removed_volume_mm3 / 1000.0
        items.append(_item(
            "material",
            "material",
            material_cm3,
            "cm3",
            profile.material_usd_per_cm3,
            material_cm3 * profile.material_usd_per_cm3,
            "material_volume_cm3 * material_usd_per_cm3",
        ))
        if removed_cm3 and profile.removal_rate_cm3_per_min is None:
            raise ValueError("removal_rate_cm3_per_min is required when removed volume is set")
        if removed_cm3:
            removal_minutes = removed_cm3 / profile.removal_rate_cm3_per_min
            machine_rate_minute = profile.machine_usd_per_hour / 60.0
            items.append(_item(
                "material removal time",
                "machine_time",
                removal_minutes,
                "minute",
                machine_rate_minute,
                removal_minutes * machine_rate_minute,
                "removed_volume_cm3 / removal_rate_cm3_per_min * machine_usd_per_min",
            ))
        _append_setup_items(items, metrics, profile)
        if metrics.tool_change_count:
            minutes = metrics.tool_change_count * profile.tool_change_time_minutes
            unit_cost = profile.machine_usd_per_hour / 60.0
            items.append(_item(
                "tool changes",
                "tooling",
                minutes,
                "machine_minute",
                unit_cost,
                minutes * unit_cost,
                "tool_change_count * tool_change_time_minutes * machine_usd_per_min",
            ))
        if metrics.hole_count and profile.hole_usd_each:
            items.append(_item(
                "hole operations",
                "features",
                metrics.hole_count,
                "hole",
                profile.hole_usd_each,
                metrics.hole_count * profile.hole_usd_each,
                "hole_count * hole_usd_each",
            ))
        return self._finish(profile, items, ("CNC removal time assumes uniform removal rate",))


class SheetLaserCostingAdapter(CostingAdapter):
    """Sheet/laser estimate from nested sheet area, cut length, bends, and holes."""

    process_id: ManufacturingProcess = "sheet_laser"

    def estimate(
        self,
        metrics: GeometryCostMetrics,
        profile: ProcessRateProfile,
    ) -> ManufacturingCostEstimate:
        self._check_profile(profile)
        if metrics.cut_length_mm and profile.cut_speed_mm_per_min is None:
            raise ValueError("cut_speed_mm_per_min is required when cut length is set")

        items: list[CostLineItem] = []
        nested_area_cm2 = (metrics.sheet_area_mm2 / 100.0) / metrics.sheet_nesting_yield
        items.append(_item(
            "nested sheet material",
            "material",
            nested_area_cm2,
            "cm2",
            profile.sheet_material_usd_per_cm2,
            nested_area_cm2 * profile.sheet_material_usd_per_cm2,
            "sheet_area_cm2 / sheet_nesting_yield * sheet_material_usd_per_cm2",
            (f"nesting yield {metrics.sheet_nesting_yield:.3f}",),
        ))
        if metrics.cut_length_mm:
            cut_minutes = metrics.cut_length_mm / profile.cut_speed_mm_per_min
            unit_cost = profile.machine_usd_per_hour / 60.0
            items.append(_item(
                "laser cutting time",
                "machine_time",
                cut_minutes,
                "minute",
                unit_cost,
                cut_minutes * unit_cost,
                "cut_length_mm / cut_speed_mm_per_min * machine_usd_per_min",
            ))
        _append_setup_items(items, metrics, profile)
        if metrics.bend_count and profile.bend_usd_each:
            items.append(_item(
                "bend operations",
                "features",
                metrics.bend_count,
                "bend",
                profile.bend_usd_each,
                metrics.bend_count * profile.bend_usd_each,
                "bend_count * bend_usd_each",
            ))
        if metrics.hole_count and profile.hole_usd_each:
            items.append(_item(
                "hole operations",
                "features",
                metrics.hole_count,
                "hole",
                profile.hole_usd_each,
                metrics.hole_count * profile.hole_usd_each,
                "hole_count * hole_usd_each",
            ))
        return self._finish(
            profile,
            items,
            ("sheet cost uses nested blank area, not full vendor stock",),
        )


class AdditivePrintCostingAdapter(CostingAdapter):
    """3D-print estimate from material volume, support volume, print time, and setup."""

    process_id: ManufacturingProcess = "additive_3d_print"

    def estimate(
        self,
        metrics: GeometryCostMetrics,
        profile: ProcessRateProfile,
    ) -> ManufacturingCostEstimate:
        self._check_profile(profile)
        items: list[CostLineItem] = []
        part_cm3 = metrics.material_volume_mm3 / 1000.0
        support_cm3 = metrics.support_material_volume_mm3 / 1000.0
        items.append(_item(
            "print material",
            "material",
            part_cm3,
            "cm3",
            profile.material_usd_per_cm3,
            part_cm3 * profile.material_usd_per_cm3,
            "material_volume_cm3 * material_usd_per_cm3",
        ))
        if support_cm3 and profile.consumable_usd_per_cm3:
            items.append(_item(
                "support material",
                "material",
                support_cm3,
                "cm3",
                profile.consumable_usd_per_cm3,
                support_cm3 * profile.consumable_usd_per_cm3,
                "support_material_volume_cm3 * consumable_usd_per_cm3",
            ))
        if metrics.print_time_minutes:
            unit_cost = profile.print_usd_per_hour / 60.0
            items.append(_item(
                "print time",
                "machine_time",
                metrics.print_time_minutes,
                "minute",
                unit_cost,
                metrics.print_time_minutes * unit_cost,
                "print_time_minutes * print_usd_per_min",
            ))
        _append_setup_items(items, metrics, profile)
        return self._finish(
            profile,
            items,
            ("print-time estimate should come from a slicer or profile",),
        )


def _append_setup_items(
    items: list[CostLineItem],
    metrics: GeometryCostMetrics,
    profile: ProcessRateProfile,
) -> None:
    if metrics.setup_count and profile.setup_fee_usd:
        items.append(_item(
            "setup fee",
            "setup",
            metrics.setup_count,
            "setup",
            profile.setup_fee_usd,
            metrics.setup_count * profile.setup_fee_usd,
            "setup_count * setup_fee_usd",
        ))
    if metrics.setup_count and profile.setup_time_minutes:
        minutes = metrics.setup_count * profile.setup_time_minutes
        unit_cost = profile.machine_usd_per_hour / 60.0
        items.append(_item(
            "setup time",
            "setup",
            minutes,
            "machine_minute",
            unit_cost,
            minutes * unit_cost,
            "setup_count * setup_time_minutes * machine_usd_per_min",
        ))


def _item(
    name: str,
    category: str,
    quantity: float,
    unit: str,
    unit_cost_usd: float,
    subtotal_usd: float,
    formula: str,
    assumptions: tuple[str, ...] = (),
) -> CostLineItem:
    return CostLineItem(
        name=name,
        category=category,
        quantity=_round_measure(quantity),
        unit=unit,
        unit_cost_usd=_round_money(unit_cost_usd),
        subtotal_usd=_round_money(subtotal_usd),
        formula=formula,
        assumptions=assumptions,
    )


def _round_money(value: float) -> float:
    return round(float(value), 6)


def _round_measure(value: float) -> float:
    return round(float(value), 6)


def _public_numbers(obj: object) -> dict[str, float]:
    return {
        name: float(value)
        for name, value in vars(obj).items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }


# --- Single-source interface for downstream pipelines (e.g. HWE-Pipeline #24) ---
#
# These are the canonical, vendor-neutral building blocks a sourcing/TPM cost
# layer can single-source instead of re-deriving rate tables:
#   * ADAPTERS / adapter_for() / estimate_cost(): one entrypoint that maps a
#     process_id to the right deterministic adapter.
#   * PROFILE_PRESETS / get_profile() / list_profiles(): a starter library of
#     illustrative generic rate profiles. They are deliberately NOT vendor
#     quotes — calibrate the rates locally. Vendor-specific profiles
#     (e.g. SendCutSend sheet rules) belong in the consuming repo, not here.

ADAPTERS: dict[ManufacturingProcess, type[CostingAdapter]] = {
    "cnc_milling": CncCostingAdapter,
    "sheet_laser": SheetLaserCostingAdapter,
    "additive_3d_print": AdditivePrintCostingAdapter,
}


def adapter_for(process_id: ManufacturingProcess) -> CostingAdapter:
    """Return a fresh CostingAdapter instance for ``process_id``."""
    try:
        return ADAPTERS[process_id]()
    except KeyError:
        raise ValueError(
            f"no costing adapter for process_id={process_id!r}; "
            f"known processes: {sorted(ADAPTERS)}"
        ) from None


def estimate_cost(
    metrics: GeometryCostMetrics,
    profile: ProcessRateProfile,
) -> ManufacturingCostEstimate:
    """Single entrypoint: dispatch to the adapter matching ``profile.process_id``.

    Downstream callers can rely on this instead of importing each adapter class.
    """
    return adapter_for(profile.process_id).estimate(metrics, profile)


_GENERIC = "illustrative generic baseline rate; calibrate locally, not a vendor quote"

PROFILE_PRESETS: dict[str, ProcessRateProfile] = {
    profile.profile_id: profile
    for profile in (
        ProcessRateProfile(
            process_id="cnc_milling",
            profile_id="cnc-aluminum-3axis-v1",
            material_id="6061-aluminum",
            material_usd_per_cm3=0.30,
            machine_usd_per_hour=75.0,
            removal_rate_cm3_per_min=8.0,
            setup_fee_usd=0.0,
            setup_time_minutes=15.0,
            tool_change_time_minutes=0.5,
            hole_usd_each=0.50,
            min_job_fee_usd=35.0,
            assumptions=(_GENERIC, "3-axis prismatic part, single fixturing"),
        ),
        ProcessRateProfile(
            process_id="cnc_milling",
            profile_id="cnc-mild-steel-3axis-v1",
            material_id="1018-mild-steel",
            material_usd_per_cm3=0.12,
            machine_usd_per_hour=85.0,
            removal_rate_cm3_per_min=3.0,
            setup_fee_usd=0.0,
            setup_time_minutes=20.0,
            tool_change_time_minutes=0.75,
            hole_usd_each=0.75,
            min_job_fee_usd=45.0,
            assumptions=(_GENERIC, "slower removal vs aluminum, harder tooling"),
        ),
        ProcessRateProfile(
            process_id="sheet_laser",
            profile_id="sheet-laser-mild-steel-v1",
            material_id="1.5mm-mild-steel-sheet",
            sheet_material_usd_per_cm2=0.015,
            machine_usd_per_hour=120.0,
            cut_speed_mm_per_min=4000.0,
            setup_fee_usd=0.0,
            bend_usd_each=1.25,
            hole_usd_each=0.10,
            min_job_fee_usd=25.0,
            assumptions=(_GENERIC, "fiber-laser cut, press-brake bends"),
        ),
        ProcessRateProfile(
            process_id="sheet_laser",
            profile_id="sheet-laser-aluminum-v1",
            material_id="2mm-aluminum-sheet",
            sheet_material_usd_per_cm2=0.022,
            machine_usd_per_hour=120.0,
            cut_speed_mm_per_min=3000.0,
            setup_fee_usd=0.0,
            bend_usd_each=1.50,
            hole_usd_each=0.10,
            min_job_fee_usd=25.0,
            assumptions=(_GENERIC, "aluminum cuts slower than equivalent steel gauge"),
        ),
        ProcessRateProfile(
            process_id="additive_3d_print",
            profile_id="fdm-pla-v1",
            material_id="pla-filament",
            material_usd_per_cm3=0.05,
            consumable_usd_per_cm3=0.05,
            print_usd_per_hour=3.0,
            setup_fee_usd=0.0,
            setup_time_minutes=5.0,
            min_job_fee_usd=5.0,
            assumptions=(_GENERIC, "desktop FDM; print time from a slicer profile"),
        ),
        ProcessRateProfile(
            process_id="additive_3d_print",
            profile_id="sla-resin-v1",
            material_id="standard-resin",
            material_usd_per_cm3=0.18,
            consumable_usd_per_cm3=0.18,
            print_usd_per_hour=4.0,
            setup_fee_usd=0.0,
            setup_time_minutes=8.0,
            min_job_fee_usd=8.0,
            assumptions=(_GENERIC, "MSLA resin; supports priced as consumable volume"),
        ),
    )
}


def get_profile(profile_id: str) -> ProcessRateProfile:
    """Return a copy-safe preset rate profile by id."""
    try:
        return PROFILE_PRESETS[profile_id]
    except KeyError:
        raise ValueError(
            f"unknown profile_id={profile_id!r}; "
            f"known presets: {sorted(PROFILE_PRESETS)}"
        ) from None


def list_profiles() -> list[str]:
    """List the available preset profile ids."""
    return sorted(PROFILE_PRESETS)
