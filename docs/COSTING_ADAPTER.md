# CostingAdapter

`makerbench.costing` provides the local manufacturing-cost estimate layer for
MakerBench. It prices the produced part, not the LLM run that made it.

The adapter is intentionally deterministic and dependency-light: callers pass
public geometry or feature metrics plus a process rate profile, and receive an
itemized estimate with the formulas and assumptions surfaced. It does not call a
manufacturing API and it is not a quote.

## Data Flow

1. Derive `GeometryCostMetrics` from an artifact, vector path, B-rep summary, or
   disclosed process metadata.
2. Choose a `ProcessRateProfile` for the process and material.
3. Call a `CostingAdapter` implementation.
4. Store or display the returned `ManufacturingCostEstimate` as local estimate
   metadata.

The first shipped adapters are:

| Adapter | Process | Core formulas |
| --- | --- | --- |
| `CncCostingAdapter` | `cnc_milling` | material volume, removal time, setup, tool changes, holes |
| `SheetLaserCostingAdapter` | `sheet_laser` | nested sheet area, cut time, setup, bends, holes |
| `AdditivePrintCostingAdapter` | `additive_3d_print` | part/support material, print time, setup |

## Example

```python
from makerbench.costing import CncCostingAdapter, GeometryCostMetrics, ProcessRateProfile

metrics = GeometryCostMetrics(
    material_volume_mm3=12_000,
    removed_volume_mm3=3_000,
    hole_count=4,
)
profile = ProcessRateProfile(
    process_id="cnc_milling",
    profile_id="local-cnc-aluminum-v1",
    material_id="6061-aluminum",
    material_usd_per_cm3=0.2,
    machine_usd_per_hour=60.0,
    removal_rate_cm3_per_min=1.5,
    setup_fee_usd=10.0,
)

estimate = CncCostingAdapter().estimate(metrics, profile)
print(estimate.total_usd)
print(estimate.as_dict()["line_items"])
```

## Boundary

- Costing estimates are local, transparent, and reproducible.
- `estimate_not_quote` is always true for these adapters.
- Vendor-specific profiles such as SendCutSend-like sheet rules can subclass
  `CostingAdapter` or provide a `ProcessRateProfile`, but live quote bridges
  remain a separate layer.
- These estimates do not affect `GradeResult.score`, leaderboard L1-L4
  semantics, or token-cost telemetry in `makerbench.pricing`.
