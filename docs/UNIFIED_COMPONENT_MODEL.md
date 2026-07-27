# The Unified Component Model

**The single ECAD↔MCAD bridge the whole PCBA benchmark domain rests on: every
electronic component is exactly three CAD files plus a manifest, and the same
three files that make a part *gradable* are also the manufacturing Release
Package inputs.**

Software benchmarks can grade "it compiled." Hardware cannot — a cleanly
formatted netlist can still refuse to fit the box or cook the board. The PCBA
domain (epic [#214](https://github.com/tonykoop/makerbench-hwe/issues/214))
grades **physical electrical reality** with deterministic math, and it does so
over a component catalog where each part carries three orthogonal views:

| File | View | Tells an agent… |
| --- | --- | --- |
| `symbol.json` | **SCH** — pin map, electrical types, logical groups | *how the city functions* (logic / nets) |
| `footprint.kicad_mod` | **PCB** — pads, pitch, courtyard, keep-outs | *where buildings anchor* (2D land pattern) |
| `model.step` | **MCAD** — ISO-10303 geometry + height | *the Z-axis volume* (does the lid close?) |
| `metadata.yaml` | **manifest** — material/thermal/electrical physics + provenance | *what the brick is made of* + where it came from |

The schematic gives the electrical half of the dual gate (ERC/DRC, see
[`#210`](https://github.com/tonykoop/makerbench-hwe/issues/210)); the STEP gives
the mechanical half (Z-collision against the enclosure lid,
[`#209`](https://github.com/tonykoop/makerbench-hwe/issues/209)); the manifest
physics feeds the deterministic primitives (`trace_width_calc`, `thermal_calc`,
[`#211`](https://github.com/tonykoop/makerbench-hwe/issues/211)).

## Source of truth: `offtheshelf`, not a MakerBench copy

The catalog itself lives **once**, in the shared
[`tonykoop/offtheshelf`](https://github.com/tonykoop/offtheshelf) repository, and
is consumed by MakerBench, the StudioPipeline `hwe_pcba_pack` Part-Builder, and
the HWE sourcing engine alike. MakerBench **does not re-declare or fork** that
catalog; it reads it. This document specifies the on-disk shape MakerBench
*consumes* and the validator that asserts an entry is well-formed, so the same
contract holds for a bundled fixture and for a clone of `offtheshelf`.

> **Public/private boundary.** Catalog parts are **public fixtures** —
> first-party or redistributable data only, license declared per part in
> `metadata.yaml → provenance`. They carry **no oracle thresholds**: a part
> describes *what it is*, never *what score a design should get*. Task answer
> keys stay in `private/oracles/`. The validator only checks identity and
> geometry consistency; it never reads or emits a grading threshold.

## On-disk layout

```
components/
└── electronic/
    └── <MPN>/
        ├── symbol.json          # SCH view
        ├── footprint.kicad_mod  # PCB view
        ├── model.step           # MCAD view (ISO-10303-21)
        └── metadata.yaml        # manifest → points at the three files above
```

Mechanical parts (fasteners, inserts, bearings) live under
`components/mechanical/<part>/` and may carry only a `metadata.yaml` (STEP
optional); the electrical views do not apply to them.

### The manifest (`metadata.yaml`)

```yaml
mpn: GENERIC-LQFP-64           # required — MPN or stable id for a generic part
category: electronic           # required — "electronic" | "mechanical"
package: "LQFP-64"             # standardized package / standard
description: Generic LQFP-64 microcontroller, 0.5 mm pitch, 10x10 mm body.
files:                         # relative paths; null when a view is absent
  symbol: symbol.json
  footprint: footprint.kicad_mod
  model_step: model.step
physical:                      # bounding envelope; height_mm drives Z interference
  length_mm: 12.0
  width_mm: 12.0
  height_mm: 1.4
physics:                       # "what the brick is made of" (feeds #211 primitives)
  package_material: "epoxy mold compound"
  max_junction_temp_c: 150
  theta_ja_c_per_w: 45
electrical: {type: mcu, pin_count: 64}
provenance:                    # license + redistribution (public-readiness gate)
  license: CC-BY-4.0
  redistributable: true
  source: "first-party (parametric LQFP-64 package, authored for offtheshelf)"
  source_url: null
tags: [active, mcu, smd, "lqfp-64"]
```

The manifest is validated by `CatalogEntryManifest`
(`makerbench/unified_component.py`), which **tolerates** the extra `offtheshelf`
keys (`physics`, `electrical`, `vendors`, `provenance`, `tags`) so the upstream
catalog loads without modification. The canonical machine schema for the
*in-memory exchange* shape (the answer-free object handed to graders/agents) is
[`schemas/unified_component.schema.json`](../schemas/unified_component.schema.json),
exported from the same module.

### `symbol.json`

A lightweight pin map. `pins[].number` is the identifier matched against the
footprint; `~` is treated as an unnamed placeholder and ignored for the
pin↔pad check.

```json
{
  "name": "R_0603",
  "reference_prefix": "R",
  "pins": [
    { "number": "1", "name": "~", "type": "passive" },
    { "number": "2", "name": "~", "type": "passive" }
  ]
}
```

### `footprint.kicad_mod`

A standard KiCad S-expression footprint module. Each electrical pad is a
`(pad "<name>" <type> …)` form; pads named `""` and `np_thru_hole` features are
mechanical and excluded from the pin↔pad check.

### `model.step`

An ISO-10303-21 STEP solid. The validator does not need a full B-rep — it reads
every `CARTESIAN_POINT` and computes the axis-aligned bounding box, asserting a
**non-degenerate** extent (real volume, positive height). The Z extent must
agree with the manifest `height_mm` to ±0.5 mm or a warning is raised.

## The validator

`makerbench/unified_component.py` exposes `validate_catalog_entry(entry_dir)`,
returning a `CatalogEntryReport`. For an `electronic` entry it asserts:

1. **All three files are declared and present** on disk.
2. **Pin↔pad agreement** — the set of `symbol.json` pin identifiers equals the
   set of electrical footprint pad names (mechanical/NPTH pads excluded).
3. **Non-degenerate STEP** — the `model.step` bounding box has positive extent
   on all three axes.

`mechanical` entries default to a relaxed policy (STEP optional); pass
`require_all_files=True` to demand all three regardless of category. The report
carries `ok`, `errors`, `warnings`, the pin/pad counts, and the STEP bbox.

Run it from the CLI over the bundled examples or any catalog tree:

```bash
# bundled worked examples (0603 + LQFP-64)
python scripts/validate_component_catalog.py

# a clone of the shared catalog
python scripts/validate_component_catalog.py path/to/offtheshelf/components
```

## Worked examples

Two complete, first-party (CC-BY-4.0) entries ship under
[`examples/component_catalog/`](../examples/component_catalog/) and are exercised
by `tests/test_component_catalog.py`:

| Entry | Package | Pins / pads | STEP bbox (mm) |
| --- | --- | --- | --- |
| `GENERIC-RES-0603` | 0603 passive | 2 / 2 | 1.6 × 0.8 × 0.45 |
| `GENERIC-LQFP-64` | LQFP-64, 0.5 mm pitch | 64 / 64 | 12 × 12 × 1.4 |

Both pass all three checks: the resistor is the minimal two-terminal case; the
LQFP-64 exercises a high-pin-count package where pin↔pad agreement and a tall
enough STEP body (1.4 mm — enough to fail a tight lid) actually matter.

## Why one schema serves both the benchmark and "click order"

The three files are exactly the inputs of a manufacturing Release Package:
`footprint.kicad_mod` → Gerbers + drill, `metadata.yaml` → BOM + centroid,
`model.step` → the mechanical model. A design that passes the benchmark's dual
gate is therefore one an agent could literally send to JLCPCB/Tempo — the
catalog that grades the design *is* the catalog that builds it.
