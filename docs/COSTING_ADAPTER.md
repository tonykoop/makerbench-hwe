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

## Single-sourcing for downstream pipelines (HWE-Pipeline, epic #24)

The CostingAdapter layer is the canonical home for the *geometry→dollars*
formulas. A downstream sourcing/TPM cost layer (e.g. HWE-Pipeline) should
single-source against it rather than re-deriving rate tables or adapter wiring:

- **One entrypoint.** `estimate_cost(metrics, profile)` dispatches to the adapter
  matching `profile.process_id`, so callers never import each adapter class.
  `adapter_for(process_id)` and the `ADAPTERS` map are available when a caller
  needs the adapter directly.
- **Serializable contract.** `GeometryCostMetrics` and `ProcessRateProfile`
  expose `to_dict()` / `from_dict()`. They round-trip through JSON (tuple
  `assumptions` restore from lists) and `from_dict` ignores unknown keys, so a
  downstream registry can carry extra annotation fields (`label`, `vendor`,
  notes) without breaking.
- **Starter profiles.** `PROFILE_PRESETS` / `get_profile(id)` / `list_profiles()`
  provide vendor-neutral baseline rate profiles
  (`cnc-aluminum-3axis-v1`, `sheet-laser-mild-steel-v1`, `fdm-pla-v1`, …). Every
  preset is labelled *"not a vendor quote"* and is meant to be calibrated
  locally.

```python
from makerbench.costing import estimate_cost, get_profile, GeometryCostMetrics

profile = get_profile("cnc-aluminum-3axis-v1")          # or ProcessRateProfile.from_dict(...)
metrics = GeometryCostMetrics(material_volume_mm3=12_000, removed_volume_mm3=3_000)
estimate = estimate_cost(metrics, profile)
print(estimate.total_usd, estimate.as_dict()["line_items"])
```

**Boundary for the downstream repo:** vendor-specific rate tables and process
rules (e.g. SendCutSend sheet limits, a shop's actual shop rate) belong in the
consuming pipeline, not here. This module stays vendor-neutral and deterministic;
HWE-Pipeline plugs its own `ProcessRateProfile` values (or a `CostingAdapter`
subclass) on top.

## Boundary

- Costing estimates are local, transparent, and reproducible.
- `estimate_not_quote` is always true for these adapters.
- Vendor-specific profiles such as SendCutSend-like sheet rules can subclass
  `CostingAdapter` or provide a `ProcessRateProfile`, but live quote bridges
  remain a separate layer.
- These estimates do not affect `GradeResult.score`, leaderboard L1-L4
  semantics, or token-cost telemetry in `makerbench.pricing`.
