# Code-CAD Arena Blind Vote Surface

This document defines the #424 blind A/B vote surface for Epic #421. It uses the
TwinGrid blind/reveal discipline in a lightweight HTML helper: candidates are
shown side by side, Tony votes without model labels, and model identity is
revealed only after the vote through the Partner-Peek step.

## Blind Payload

`makerbench.code_cad_vote_surface.blind_pair_payload()` exposes only:

- `pair_id`
- side (`left` / `right`)
- `candidate_id`
- `trial_id`
- `render_path`

It does not expose `model_id` or provider provenance. The rendered HTML follows
the same rule.

## Vote Record

`record_vote()` captures:

- `pair_id`
- `winner`: `left`, `right`, or `draw`
- `voter_id`
- blind left/right candidate provenance

`append_vote_record()` persists the blind record as JSONL. These records are
safe to aggregate into the subjective Elo engine because they preserve pair and
trial provenance without unblinding the voter.

## Partner-Peek Reveal

`reveal_vote()` adds model identity and public provenance only after the vote is
recorded. That keeps the subjective vote blind while still making the resulting
arena row auditable.

No Selecta internals are embedded in the public payload, HTML, or vote log. Any
private provenance interface remains outside this repository boundary.
