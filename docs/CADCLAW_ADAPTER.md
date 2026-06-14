# CADCLAWAdapter — micro-DFM as a sub-grade gate for MARB assemblies

`makerbench.adapters.cadclaw` exposes MakerBench-HWE's deterministic
component-level DFM checks to [CADCLAW/MARB](https://github.com/) as an
assembly-level **sub-grade gate**. It is the first concrete adapter built on the
`BaseAdapter` boundary (mb#76); this adapter is mb#78.

## Why the two benchmarks are complementary

CADCLAW grades **macro-assembly readiness** with black-box STEP gates —
inventory, interference, adjacency, floating parts — as an assembly climbs its
L1–L7 ladder. It does *not* grade the **micro-fabrication layer**: whether each
individual part is actually manufacturable.

A model can place a bracket correctly (passes CADCLAW) while the bracket is
physically un-manufacturable (bad wall, malformed exchange geometry, private-path
leak). MakerBench's portable scorer catches exactly that gap. The adapter lets a
CADCLAW assembly fail *because one of its placed parts is un-makeable*, without
CADCLAW adopting MakerBench task generation, oracles, or an LLM judge.

## Data contract

```
CADCLAW assembly                    MakerBench micro-DFM              CADCLAW gate
(per-part STEP/exchange)   ──────►  makerbench_core.score_file ─────► pytest-style
                            adapter   (per part, deterministic)  rollup  pass/fail + score
```

1. **In** — a list of parts. Each part is an `AssemblyPart(part_id,
   artifact_path, quantity=1, metadata={})`, or a plain dict with the same keys,
   or a bare artifact path (`str`/`PathLike`, `part_id` derived from the file
   stem). `artifact_path` is a public exchange artifact: STEP/STL/OBJ/OFF/SCAD/
   SVG/DXF — the surface CADCLAW already exports per part.
2. **Per part** — `score_file` runs the portable `portable-dfm-v1` profile:
   public-safe path, readable/non-empty, reproducible SHA-256, and format/
   geometry structure rules. No CAD kernel, no oracle, no judge.
3. **Rollup** — per-part scores combine into one `AssemblySubGrade`.
4. **Out** — `AssemblySubGrade` → `as_cadclaw_gate()` yields the minimal dict a
   CADCLAW pytest gate asserts on.

## Rollup semantics

| Field | Meaning |
| --- | --- |
| `assembly_score` | quantity-weighted mean of per-part `makerbench_dfm_score` |
| `min_part_score` | weakest part — the manufacturability bottleneck |
| `gate_passed` | the boolean a CADCLAW gate asserts on |
| `parts` | per-part `PartComponentReport` with failing-rule evidence |

Two rollup policies, chosen at construction:

- **Strict (default, `require_all_parts=True`)** — the gate passes only if
  **every** part passes its own micro-DFM gate. One un-manufacturable part fails
  the assembly. This matches CADCLAW's all-parts-must-clear gate philosophy.
- **Soft (`require_all_parts=False`)** — the gate passes when the weighted
  `assembly_score` clears `assembly_pass_threshold`, tolerating a weak part if
  the assembly as a whole is strong. Useful as a non-blocking ladder signal.

A part passes when the portable scorer's own `passed` gate is true **and** the
score clears this adapter's `part_pass_threshold` (default 80, matching
`makerbench_core`). Raising `part_pass_threshold` makes the gate stricter than
the standalone scorer.

## Usage

```python
from makerbench.adapters import AssemblyPart, CADCLAWAdapter

adapter = CADCLAWAdapter()  # strict, 80% thresholds
grade = adapter.evaluate(
    [
        AssemblyPart("bracket-L", "parts/bracket_left.step", quantity=2),
        AssemblyPart("bracket-R", "parts/bracket_right.step", quantity=2),
        AssemblyPart("base-plate", "parts/base.step"),
    ],
    assembly_id="gantry-v3",
)

print(grade.gate_passed, grade.assembly_score)
print(grade.failed_part_ids)        # which parts to fix
gate = grade.as_cadclaw_gate()      # {"gate_id", "passed", "score", ...}
```

In a CADCLAW pytest-style gate:

```python
def test_microdfm_subgrade(assembly_step_parts):
    grade = CADCLAWAdapter().evaluate(assembly_step_parts)
    gate = grade.as_cadclaw_gate(gate_id="makerbench.microdfm")
    assert gate["passed"], gate["reason"]
```

## Core vs plugin

The adapter ships in **core** (`makerbench/adapters/`), not an optional plugin:
its only dependency is the portable `makerbench_core` component scorer — there is
no CAD kernel, oracle, or heavy task-harness coupling to quarantine. The
`BaseAdapter` contract guarantees `oracle_access=False`, `llm_judge=False`,
`generates_tasks=False`, which is what lets a neighboring benchmark adopt it as a
sub-grade without inheriting the MakerBench leaderboard harness.

## Mirror work

Agent-side integration (driving a VLM/agent to *produce* assembly parts that
clear this gate) is mirrored in `3DMaker-VLM`; this adapter is the deterministic
grader half. Related: mb#76 (adapter API), mb#74 (CADCLAW outreach).
