# Gamified HII badges (mb#106)

A contributor README can already show the single **Built with MakerBench** shields
badge (mb#99). The **HII badge classes** go further: a gamified badge keyed to
*how autonomously* a passing run was produced, so the human-AI stack behind a
verified row is visible at a glance.

Implemented in [`makerbench/hii_badge.py`](../makerbench/hii_badge.py); tested in
[`tests/test_hii_badge.py`](../tests/test_hii_badge.py). Additive and
stdlib-only — no grading path is touched.

## Badge classes

The badge **class equals the run's HII `highest_level`** (see
`schema.HumanInterventionIndex`). The tiers are a disclosure, not a score — an L0
row is more impressive than an L2 one, but all three are valid:

| Class | Title          | Color   | When earned                                                       | Flex                   |
| ----- | -------------- | ------- | ---------------------------------------------------------------- | ---------------------- |
| L0    | Pure Autonomy  | emerald | 100% of tool-calls via the API loop, zero human keystrokes/edits | AI-systems architect   |
| L1    | Elite Copilot  | blue    | Passing grade with light NL steering on an enterprise family     | cyborg engineer        |
| L2    | Master Triage  | purple  | Cleared a volatile multi-constraint challenge with heavy triage  | physical-domain mastery|

## Anti-fake

A badge's authority derives from the signed **`.mbc` certificate**
([`makerbench/certificate.py`](../makerbench/certificate.py)) plus **verified
leaderboard state** — never self-report. `badge_from_certificate()` enforces
three independent gates before a flex class is shown (`badge.earned`):

1. **`passed`** — the run met the geometry pass bar (read from the signed payload).
2. **`signature_verified`** — the HMAC re-verifies over the canonical payload, so a
   hand-edited score/ratio/tier breaks the signature.
3. **`leaderboard_verified`** — the row reached `public-regrade-verified` or
   `official-heldout-verified` (`schema.VerificationStatus`); `unverified` and
   `rejected` rows never earn a class.

If any gate fails the badge still renders — as a neutral grey **`unverified`**
badge — so a README image never breaks, but it cannot flex a class it did not earn.

## Dynamic endpoint — `/api/badge?user=<u>`

`serve_badge()` is the server-agnostic core of the endpoint described in the
issue. It is pure stdlib and takes a `lookup(user)` callback, so a Hugging Face
Space / WSGI / Flask backend can mount it without this module knowing how rows are
stored:

```python
from makerbench.hii_badge import serve_badge

def latest_verified_row(user):
    # return {"certificate": <.mbc source>, "key": KEY,
    #         "verification_status": "...", "expected_nonce": "..."} or None
    ...

def app(environ, start_response):
    status, content_type, body = serve_badge(
        environ.get("QUERY_STRING", ""),
        key=SHARED_KEY,
        lookup=latest_verified_row,
    )
    start_response(f"{status} OK", [("Content-Type", content_type)])
    return [body.encode("utf-8")]
```

It always returns an SVG: a flex badge on a verified hit, a grey `unverified`
badge on a bad signature/unverified row, and a grey badge with `404`/`400` status
on an unknown/missing user.

## Rendering options

- **Self-rendered SVG** — `render_badge_svg(badge)` emits a self-contained, flat
  two-segment badge (deterministic, no network, no font files). Used by the
  endpoint above.
- **shields.io endpoint JSON** — `badge_shields_payload(badge)` returns a
  `schemaVersion: 1` payload so the HII badge can plug into the same
  `img.shields.io/endpoint?url=…` machinery as the per-model badges
  (`site/build_data.py`).

See [`examples/hii_badge.example.json`](../examples/hii_badge.example.json) for a
worked output.

## Links

- HII source: mb#89 (+ Autonomy Ratio).
- `.mbc` certificate: mb#109 / mb#114.
- Extends distribution/badge infra: mb#99.
- Part of mb#100.
