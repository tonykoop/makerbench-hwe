# Landscape Sweep Runbook

This is the quarterly maintenance loop for
[LANDSCAPE.md](LANDSCAPE.md), [landscape.yaml](landscape.yaml), and
[STRATEGY_MEMO.md](STRATEGY_MEMO.md).

Current full sweep: 2026-06-10.
Next full sweep due: 2026-09-10.
Latest addendum: 2026-06-14.

## Rules

- Use primary sources only: arXiv abstract pages, project repositories,
  project pages, Hugging Face Spaces, datasets, or official vendor pages.
- Fetch and quote before changing public copy. Do not promote a claim from
  memory, secondary summaries, or model recall.
- Keep benchmark, method, dataset, leaderboard, and product labels precise.
- Treat promised releases, leaderboard counts, top scores, validation policies,
  VLM-judge replacement claims, and vendor availability as volatile facts.
- Add a new `docs/landscape-evidence/<YYYY-MM-DD>.yaml` file for each sweep or
  dated addendum. Do not rewrite older evidence sidecars.

## Quarterly Checklist

1. Re-verify every `docs/landscape.yaml` entry against its `source` and
   `source_alt` URLs where present.
2. Record every fetched URL, fetch date, supporting short excerpts or claim
   notes, and volatility notes in the new evidence sidecar before editing
   `LANDSCAPE.md` or `landscape.yaml`.
3. Hunt for entries newer than the last full sweep date. Mark entries as
   `recent: true` when their v1 or major revision is within 90 days of the
   sweep date.
4. Re-check the promised-but-unshipped releases in the volatile watchlist:
   UniCAD, Physics-in-the-Loop, Hephaestus-CCX, and GenCAD-3D.
5. Re-check CADGenBench leaderboard size, top score, validation policy, and
   whether CAD Score gained a manufacturability component.
6. Re-check whether MUSE replaced its VLM judge with deterministic checks.
7. Diff `docs/landscape.yaml`, append to the "What changed since the last
   sweep" section in `LANDSCAPE.md`, and refresh `STRATEGY_MEMO.md` rankings
   and guardrails where the evidence changes the threat model.
8. Run the landscape tests before opening or updating a PR:
   `python -m pytest tests/test_landscape_yaml.py`.

## Evidence Sidecar Shape

Use this shape for each sweep. Keep excerpts short and source-bound.

```yaml
sweep_date: 2026-09-10

entries:
  - name: Example Project
    sources:
      - https://example.com/primary-source
    fetched_on: 2026-09-10
    supports: [name, type, source, date, recent, framing_own]
    claims:
      - 'Short claim note or brief excerpt that supports the changed field.'
    volatility:
      - 'Release status, leaderboard counts, policy terms, or other facts that
         should be re-checked next sweep.'
```

## Closeout

Update `sweep.date` only for a full re-sweep. Use a dated addendum sidecar for
small intervening additions, and name the addendum in `LANDSCAPE.md` without
moving the full-sweep date.
