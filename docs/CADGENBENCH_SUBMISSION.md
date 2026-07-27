# CADGenBench Cross-Submission (prep)

Status: **prep phase** (issue #52). Actual STEP generation depends on the first
runnable `brep-build123d` task family landing (issue #47, docs/BREP_PROFILE.md).
The final upload and validation request are maintainer actions (Hugging Face
login required). See [Remaining manual steps](#remaining-manual-steps).

## What CADGenBench is

CADGenBench is a tool-agnostic benchmark for AI-driven CAD generation maintained
by Hugging Face's **HuggingAI4Engineering** team:

- Repo: <https://github.com/huggingface/cadgenbench> (Apache-2.0)
- Leaderboard Space: <https://huggingface.co/spaces/HuggingAI4Engineering/CADGenBench>
- Public inputs: <https://huggingface.co/datasets/HuggingAI4Engineering/cadgenbench-data>
  (ODC-BY) — 81 fixtures: 49 *generation* (engineering drawing → STEP solid) and
  32 *editing* (existing STEP + described change → modified STEP).

Submissions are STEP files regardless of how they were produced. Their reference
baseline agent uses **build123d** — the same stack as MakerBench's
`brep-build123d` profile.

## Verified submission contract

All facts below were fetched from primary sources on **2026-06-10**. Sources are
cited per item; nothing here is taken from secondary summaries.

### Folder layout

From `docs/benchmark/submission.md` (cadgenbench repo): one `output.step`
(or `output.stp`) per sample —

```
results/<run_name>/
├── <sample_name>/
│   └── output.step
└── ...
```

"No other files are required: no description, no metadata, no sub-volumes" —
that statement applies to the **local-grader layout**. The **leaderboard
upload** is different: per the repo README, "Zip them as `submission.zip` with
one folder per sample plus a small `meta.json` at the root. Upload via the
**Submit** tab on the leaderboard Space." I.e. the zip root contains the
per-sample folders directly (no `results/<run_name>/` prefix) plus `meta.json`.

The Space's `submit.py` rejects zips where the folder set differs from the
dataset's fixture set (missing and extra folders are both errors; a missing
`output.step` *inside* a present folder is allowed and scores zero; an empty
`output.step` is rejected).

### meta.json fields

From the Space's `submit.py` (HuggingAI4Engineering/CADGenBench):

| Field | Rule |
|---|---|
| `submitter_name` | non-empty string |
| `submission_name` | non-empty string |
| `agent_url` | string or `null` |
| `notes` | string or `null`; max 500 chars; non-empty when present |
| `agree_to_publish` | must be the literal boolean `true` |

There is no documented field for arbitrary provenance, so MakerBench provenance
(model, commit SHA, method) is folded into `notes` and kept in full in a
`provenance.json` sidecar **outside** the zip (extra root folders in the zip are
rejected; we keep the zip minimal and link the sidecar as evidence instead).

### Sanity check

A `sanity_check_submission.py` ships alongside the samples in the
`cadgenbench-data` dataset (per the repo README). Invocation, from
`docs/benchmark/submission.md`:

```bash
DATA=$(python -c 'from cadgenbench.common.paths import data_inputs_dir; print(data_inputs_dir())')
python "$DATA/sanity_check_submission.py" path/to/output.step
```

It applies the validity gate (well-formed B-rep, watertight, meshes to a closed
manifold) and exits non-zero on failure; `--quiet` suppresses output on pass.
**It is not stdlib-runnable** — it imports `cadgenbench.common.validity` and
`cadgenbench.common.mesh`, so it needs the `cadgenbench` package and its OCCT
stack installed. We therefore document the command rather than vendor the
script.

### Scoring

From the repo README and `docs/metrics/`:

1. **Validity gate** — well-formed B-rep, watertight, tessellates to a closed
   manifold. Failure zeroes everything: "Geometry must pass the CAD Validity
   gate; otherwise it scores `cad_score = 0`."
2. **Shape similarity** — surface-distance F1 and volume IoU.
3. **Interface match** — mating-feature correctness via keep-in/keep-out
   sub-volumes.
4. **Topology match** — Betti numbers (b0, b1, b2) of the tessellated boundary.

The CAD Score is "a weighted combination of the applicable component scores";
the exact weights are **not published** in the README (see `docs/metrics.md` in
their repo for the spec).

Recommended canonical pose (from `submission.md`): bounding-box center at the
origin, extents ordered Lx ≥ Ly ≥ Lz (longest → X), reference/mounting face on
the z = −Lz/2 plane when applicable.

### Review flow

From `docs/benchmark/validation.md`: "Submissions start `unvalidated`;
maintainers promote rows to `validated` after a manual review of the
methodology evidence." After upload, the Space stores the zip under
`submissions/<id>.zip`, appends a `status: pending` row to `results.jsonl`, and
the row appears on the **Unvalidated** leaderboard immediately, with scores
populated after evaluation.

To request validation: email **michael.rabinovich@huggingface.co** with subject
**"CadGenBench verification"** and a link to supporting evidence. No review SLA
is published.

### Fetch limitations

- The Space card README's prose body could not be fully retrieved (only YAML
  frontmatter rendered); the upload contract above comes from the Space's
  `submit.py` source instead, which is the enforcing code.
- CAD Score component weights are not stated in the README; we did not assert
  any.

## How a MakerBench run maps onto CADGenBench

- STEP outputs come from the **`brep-build123d` profile** (issue #47,
  docs/BREP_PROFILE.md): agents write build123d Python, MakerBench exports
  STEP. CADGenBench accepts STEP however produced, so the adapter-driven model
  run targets their 81 public fixtures (drawing→STEP generation; STEP+change
  editing) instead of MakerBench task briefs.
- `scripts/run_cadgenbench_adapter.py` reads a local checkout/snapshot of the
  public `cadgenbench-data` inputs, builds MakerBench-style `TaskSpec` prompts,
  calls an existing adapter, executes the returned build123d Python only when
  `--allow-code-execution` is explicitly passed, and stages
  `steps/<sample>/output.step` plus a `run_manifest.json`.
- `scripts/build_cadgenbench_packet.py` (stdlib-only) takes that directory of
  per-sample STEP files plus run metadata and emits both layouts: the
  `results/<run_name>/<sample>/output.step` staging tree and a contract-shaped
  `submission.zip` (sample folders + `meta.json` at the zip root), plus a
  `provenance.json` sidecar (MakerBench commit SHA via `git rev-parse HEAD`,
  model, method notes, Python version). Output lands in `dist/cadgenbench/`
  (git-ignored). `--dry-run` validates and prints the plan without writing.

```
python scripts/run_cadgenbench_adapter.py \
    --data-dir <local cadgenbench-data checkout> \
    --agent agents/openai_build123d_agent.py \
    --out dist/cadgenbench_adapter/<model> \
    --allow-code-execution

python scripts/build_cadgenbench_packet.py \
    --steps-dir dist/cadgenbench_adapter/<model>/steps \
    --run-name makerbench-brep-<model> \
    --submitter-name "MakerBench" \
    --submission-name "<model> via MakerBench brep-build123d" \
    --model <model> --agree-to-publish [--dry-run]
```

## What MakerBench can and cannot claim

A CADGenBench row is **their benchmark, their tasks, their metric**. It is not
a MakerBench score and must not be averaged into, or presented alongside,
MakerBench leaderboard numbers as if comparable. What a submission *does*
demonstrate: MakerBench's brep-build123d harness can drive a frontier model to
produce STEP geometry that survives an independent validity gate and scoring
pipeline. Any public statement should say exactly that and no more. Per the
2026-06-10 landscape sweep correction: attribute CADGenBench to Hugging Face /
HuggingAI4Engineering only.

## Validation-request outreach draft

> Subject: CadGenBench verification
>
> Hello — we run MakerBench (github.com/tonykoop/makerbench-hwe), an open
> Apache-2.0 benchmark for agentic design-for-manufacturing, and submitted run
> `<run_name>` (`<submission_name>`) to the CADGenBench leaderboard.
>
> Methodology evidence: `<link to provenance.json + run logs / repo tag>`. The
> STEP outputs were produced by `<model>` driven through MakerBench's
> brep-build123d adapter (build123d Python → OCCT STEP export) at MakerBench
> commit `<sha>`; generation was fully automated with no manual CAD edits, and
> the packet was built with our published tooling
> (`scripts/build_cadgenbench_packet.py`).
>
> We'd welcome a methodology review for validated status. Separately: we use
> the same build123d/STEP stack as your baseline, and our grading includes
> deterministic DFM checks (wall thickness, clearances, fastener fit) that may
> be of interest as a future cross-benchmark alignment point — happy to share
> details if useful.
>
> Thanks, Tony Koop (MakerBench)

## Remaining manual steps

1. **#47 lands**: first runnable `brep-build123d` task family on `main`
   (prerequisite for generating STEP with the adapter at a citable commit).
2. Run `scripts/run_cadgenbench_adapter.py` against the 81 `cadgenbench-data`
   fixtures to produce per-sample STEP files (their inputs, our stack).
3. `python scripts/build_cadgenbench_packet.py ... --dry-run`, then build.
4. Run their `sanity_check_submission.py` on each `output.step` (requires the
   `cadgenbench` package + OCCT environment — not stdlib).
5. **Maintainer action**: upload `submission.zip` via the Space's Submit tab
   (HF login, `agree_to_publish` consent).
6. **Maintainer action**: send the validation-request email above with the
   evidence link.
