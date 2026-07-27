# Worked example: SOLIDWORKS LEO vs MakerBench DFM

A small, public-safe comparison for [`docs/LEO_DFM_COMPARISON.md`](../../docs/LEO_DFM_COMPARISON.md).
It demonstrates the intended workflow: an embedded copilot (LEO) gives an
in-session manufacturability verdict while modeling; MakerBench gives an
independent, deterministic verdict on the **exported artifact**.

This example contains **only public-safe data** — a generic L-bracket STEP with
no proprietary SOLIDWORKS internals, no private oracle geometry, and no
held-out seeds.

## The part

[`leo_passed_bracket.step`](leo_passed_bracket.step) — a generic L-bracket
exported to STEP, standing in for a part that LEO **passed** during modeling.

## Run the independent MakerBench check

```bash
makerbench-dfm-score examples/leo_comparison/leo_passed_bracket.step --json
```

```python
from makerbench_core import score_file

result = score_file("examples/leo_comparison/leo_passed_bracket.step")
print(result.makerbench_dfm_score)   # 100.0
print(result.passed)                 # True
```

## Agreement matrix (this example)

| Part | LEO verdict | MakerBench verdict | Interpretation |
| --- | --- | --- | --- |
| `leo_passed_bracket.step` | pass (recorded metadata) | **pass** (`makerbench_dfm_score: 100.0%`) | The embedded and independent checks agree for the exported artifact. |

The LEO verdict column is **recorded metadata only** — a maintainer with a
legitimate LEO seat fills it in per the
[minimal comparison protocol](../../docs/LEO_DFM_COMPARISON.md#minimal-comparison-protocol).
The MakerBench column is reproducible from this repo with no SOLIDWORKS license:
the `portable-dfm-v1` profile validates the STEP envelope, sections, product
metadata, and B-rep/cartesian shape tokens without a CAD kernel.

The four cells of the full agreement matrix (pass/pass, flag/fail, flag/pass,
pass/fail) and the machine-readable capability map
([`docs/leo_dfm_mapping.json`](../../docs/leo_dfm_mapping.json)) let an outreach
or differentiation write-up cite a specific deterministic rule for every
disagreement instead of adjudicating by LEO's private verdict.
