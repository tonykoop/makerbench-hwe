# OpenRouter queue — estimate only, 2026-09-30

No metered model execution occurred. Tony must approve and start any paid run.

This queue uses configured OpenRouter routes in the harness; it does not assert that these models are unavailable through every other provider. Resolve current catalog identifiers and confirm returned model identifiers before execution.

## Historical proxy and matrix

The offline preparation script reads public `results/` cost/runtime metadata only. Its 414 records are historical task-stack measurements, not new arena sessions or verified invoices. Costs use recorded harness accounting. Durations are historical task runtimes, not guaranteed future arena latency. Missing data remains unknown.

Projection: 66 cells per model, matching available subscription replay rounds R1–R10. R11–R14 have no original run logs and are excluded. Reuse the same instrument/seed/repetition matrix, OpenSCAD backend and blind context if Tony starts the queue.

| Configured entrant | Cost samples | Mean $/cell | Mean seconds/cell | Projected $/66 cells | Projected serial hours |
|---|---:|---:|---:|---:|---:|
| `openrouter-deepseek-v4-flash` | 66 | 0.002248 | 143.1 | 0.148368 | 2.623 |
| `openrouter-deepseek-v4-pro` | 66 | 0.042857 | 175.1 | 2.828562 | 3.210 |
| `openrouter-grok-4.3` | 66 | 0.007650 | 25.9 | 0.504900 | 0.475 |
| `openrouter-grok-4.5` | 25 | 0.049880 | 73.4 | 3.292080 | 1.346 |
| `openrouter-kimi-k2.6` | 59 | 0.106093 | 559.6 | 7.002138 | 10.259 |
| `openrouter-qwen3-max` | 66 | 0.007434 | 31.3 | 0.490644 | 0.574 |
| `openrouter-qwen3.6-max-preview` | 66 | 0.081673 | 288.5 | 5.390418 | 5.289 |

Projected total: **$19.65711**, **23.776 serial hours** (85595.4 seconds). This is a projection, not a spending limit or completion guarantee. The CLI's `--budget-usd 20` compares the projection with a reference budget; it does not execute or enforce a run budget.

`openrouter-glm-5.2` has zero historical samples: both cost and duration are `null`. It is excluded from the priced seven-model queue. Including it makes the queue total unknown, rather than treating it as free.

## Reproduce offline

Run from the repository root. These commands prepare local metadata and estimate; they cannot dispatch a model.

```bash
python3 scripts/prepare_openrouter_telemetry.py --out runs/frontier-openrouter-estimates-2026-09-30/sessions.jsonl
python -m makerbench.cli arena estimate \
  --models openrouter-deepseek-v4-flash,openrouter-deepseek-v4-pro,openrouter-grok-4.3,openrouter-grok-4.5,openrouter-kimi-k2.6,openrouter-qwen3-max,openrouter-qwen3.6-max-preview \
  --trials 66 --backend openscad \
  --telemetry-store runs/frontier-openrouter-estimates-2026-09-30/sessions.jsonl \
  --budget-usd 20
python -m makerbench.cli arena estimate \
  --models openrouter-glm-5.2 --trials 66 \
  --telemetry-store runs/frontier-openrouter-estimates-2026-09-30/sessions.jsonl
```

Actual command output reports `execution: estimate_only`, sample counts, sources, unknown models and projected totals. No executable paid-run command is included. A future approved invocation must have an enforced cost ceiling and record actual spend separately; a forecast alone is insufficient.
