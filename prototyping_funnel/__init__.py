"""Deflationary prototyping funnel — cost & manufacturing bridge (epic #240).

Public surface:

    from prototyping_funnel.stages import (
        Stage, StageResult, IterationRecord, PrototypingFunnel,
    )
    from prototyping_funnel.cost_model import (
        QuantityBreak, PriceSchedule, IterationCostPoint, DeflationaryCurve,
    )
    from prototyping_funnel.mfg_bridge import (
        DesignSpec, ProcessCostResult, BridgeResult, ManufacturingBridge,
    )
    from prototyping_funnel.roi import ROIInput, BreakevenResult, breakeven_analysis

Pure Python stdlib — no external dependencies required.
"""

from .cost_model import DeflationaryCurve, IterationCostPoint, PriceSchedule, QuantityBreak
from .mfg_bridge import BridgeResult, DesignSpec, ManufacturingBridge, ProcessCostResult
from .report import DeflationaryReport, build_report, build_report_with_roi
from .roi import BreakevenResult, ROIInput, SensitivityTable, breakeven_analysis, roi_at_volume
from .runner import FunnelRunConfig, FunnelRunResult, build_funnel_from_price_schedule, run_funnel_stages
from .stages import IterationRecord, PrototypingFunnel, Stage, StageResult

__all__ = [
    # stages
    "Stage",
    "StageResult",
    "IterationRecord",
    "PrototypingFunnel",
    # cost_model
    "QuantityBreak",
    "PriceSchedule",
    "IterationCostPoint",
    "DeflationaryCurve",
    # mfg_bridge
    "DesignSpec",
    "ProcessCostResult",
    "BridgeResult",
    "ManufacturingBridge",
    # roi
    "ROIInput",
    "BreakevenResult",
    "SensitivityTable",
    "breakeven_analysis",
    "roi_at_volume",
    # runner
    "FunnelRunConfig",
    "FunnelRunResult",
    "run_funnel_stages",
    "build_funnel_from_price_schedule",
    # report
    "DeflationaryReport",
    "build_report",
    "build_report_with_roi",
]
