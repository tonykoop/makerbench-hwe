# Code-CAD A/B Arena

Issue [#425](https://github.com/tonykoop/makerbench-hwe/issues/425) adds the
public scoring layer for the Code-CAD arena under Epic
[#421](https://github.com/tonykoop/makerbench-hwe/issues/421): blind pairwise
votes become a per-model Elo leaderboard.

This is deliberately only the aggregation layer. It does not generate
OpenSCAD, render images, call private provenance systems, or regrade geometry.
Upstream stories produce vote records; this module consumes those records.

## Vote record

Each blind vote is a pair:

```python
PairwiseVote(
    trial_id="lyre-seed0-pair3",
    left_model="gpt-5.5",
    right_model="sonnet",
    winner="left",  # "left", "right", or "tie"
    voter_id="tony",
    spec_id="lyre",
    seed=0,
)
```

The model ids must differ. A tie is a first-class outcome and records a draw.

## Elo leaderboard

`makerbench.code_cad_arena.build_elo_leaderboard` starts every model at 1000 Elo
and applies standard pairwise Elo updates with K=32 by default. The output is a
deterministically sorted list of per-model rows:

- `rank`
- `model`
- `rating`
- `votes`
- `wins`
- `losses`
- `ties`
- `last_delta`

`render_markdown_leaderboard` gives a small table suitable for an issue, PR, or
static-site ingestion while the fuller dashboard story (#427) is still pending.

## Sampling strategy

The arena should not ask Tony to vote every all-pairs combination for every
instrument. For M models, all-pairs grows as `M * (M - 1) / 2` per instrument
and quickly becomes the bottleneck.

`bounded_pair_sample` uses a deterministic public seed to shuffle model order,
ranks candidate pairs by the fewest prior votes, and greedily emits pairs while
each model stays under `max_pairs_per_model` in the batch. With the default cap
of 3, an eight-model batch asks for at most 12 pair votes instead of 28.

This keeps the queue balanced and resumable:

- under-seen pairs are preferred before repeated pairs,
- each model appears a bounded number of times per batch,
- the same `(models, seed, prior counts)` input emits the same pair list.

## Single-voter caveat

Early arena results are Tony's blind preferences, not a population estimate. A
single voter can produce a useful directional ranking for the instrument catalog,
but the leaderboard should be described as a subjective Tony-vote Elo until
additional voters exist. The objective MakerBench DFM/acoustic scoreline remains
separate so #427 can analyze whether subjective preference agrees with
manufacturability and acoustic validity.
