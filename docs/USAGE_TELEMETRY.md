# Usage & Cost Telemetry

MakerBench rows may carry token-usage and cost telemetry. The hard rule is
**honesty about provenance**: a subscription run must never look like it had an
authoritative API bill, and an estimate must never look like money actually spent.
This document defines the vocabulary that keeps those cases separate.

Schema lives in [`makerbench/schema.py`](../makerbench/schema.py) (`UsageReport`,
`CostReport`); pricing helpers in [`makerbench/pricing.py`](../makerbench/pricing.py);
the opt-in local-log reader in [`makerbench/usage_logs.py`](../makerbench/usage_logs.py).
This extends the optional telemetry section of
[`SUBMISSION_CONTRACT.md`](SUBMISSION_CONTRACT.md); it does not change scoring.

## Four cases we never collapse

| Case | What it is | `usage.source` | `usage.measurement_authority` | Actual cost (`cost.total_cost_usd`) | API-equivalent (`cost.api_equivalent_usd`) |
| --- | --- | --- | --- | --- | --- |
| **Authoritative API billing** | Provider-reported token usage from a direct API run. | `measured` | `api_billing` | estimated from measured usage × versioned pricing | n/a |
| **Local-log token telemetry** | Token counts read from a local coding-CLI usage log (e.g. via `ccusage`, across Claude Code, Codex, Gemini CLI, Qwen, …). Real counts, opaque billing. | `local_log` | `local_log` | **`null`** (billing opaque) | optional — public-rate what-if |
| **API-equivalent estimate** | What a run's tokens *would* cost at public API rates. | (carried on the row that has tokens) | — | **`null`** | populated, clearly labelled as not-a-bill |
| **Actual subscription cost** | What the subscription run actually cost. | `subscription_opaque` (no tokens) or `local_log` (tokens, no cost) | — | **`null`** — never `0` | n/a |

`usage.source` also keeps the pre-existing values `estimated` (tokenizer/heuristic
counts) and `not_reported` (runtime reported nothing).

## How `subscription_opaque` differs from `local_log`

- **`subscription_opaque`** — the run went through a subscription surface and we
  have *no* trustworthy token numbers. Token fields stay `null`.
- **`local_log`** — same opaque *billing*, but we *do* have token counts from a
  local usage log. Tokens are populated; `estimated=True`;
  `measurement_authority="local_log"`; `measurement_tool` records the tool (e.g.
  `ccusage`), `measurement_tool_version` its version, and `measurement_source` the
  data-source namespace the counts were filtered to. The counts are usable for
  scale/efficiency comparisons but are **not** authoritative billing.

A `local_log` row is strictly more informative than a `subscription_opaque` one,
but it makes **no** stronger claim about cost — see below.

## Local-log telemetry is source-agnostic (not Claude-only)

`ccusage` is a local coding-agent usage tool across many data sources — Claude
Code, Codex, Gemini CLI, Qwen, Kimi, Copilot CLI, and others — with an all-sources
default plus source-specific commands (`ccusage claude daily`, `ccusage codex
daily`, `ccusage gemini daily`, …). So `local_log` telemetry is **not** Claude-only;
MakerBench uses it for any subscription coding-CLI row.

To keep attribution honest, a `local_log` row records **`usage.measurement_source`**
— the ccusage data-source namespace its counts came from (`claude`, `codex`,
`gemini`, `qwen`, …). This is distinct from `agent_identifier`, which is the
MakerBench harness identity: a row can read `measurement_tool="ccusage"`,
`measurement_source="codex"`, `agent_identifier="codex_cli"`.

**Per-row counts must come from a source-specific export**, filtered to the
benchmarked harness/channel (e.g. `ccusage codex daily` for a Codex row). Never
attach an all-sources ccusage aggregate to a single MakerBench row — that would mix
in tokens from unrelated agents. `makerbench.usage_logs` takes the `source`
explicitly and never auto-aggregates.

## Why `cost_usd` stays null for subscription runs

`cost.total_cost_usd` (and the legacy top-level `cost_usd`) is an *actual* cost. It
is only populated when tokens were authoritatively measured and priced
(`estimate_cost`, which refuses any non-`measured` usage). For a subscription run —
`subscription_opaque` or `local_log` — the real charge is included in a plan and is
not attributable per-run, so the actual cost is `null`. **It is never reported as
`$0`:** zero would falsely imply a free run, when the truth is "unknown / not
separable." The site renders this as *not available*, never `$0`.

## Where API-equivalent estimates may be displayed

`cost.api_equivalent_usd` answers a different question: *if these tokens had gone
through the public API, what would they have cost?* It is computed by
`estimate_api_equivalent_cost`, which prices the row's tokens but deliberately
leaves `total_cost_usd` and the cost `source` at `not_available` — so the figure
can never be mistaken for an actual bill.

On the leaderboard, when a row has no actual cost but does have an API-equivalent
estimate, the Cost column shows it **distinctly** — prefixed `~`, styled as an
estimate, and tooltip-labelled "API-equivalent estimate at public API rates — NOT
an actual billed cost." Likewise the Tokens column shows local-log tokens with an
"est · local logs" sub-label rather than as authoritative measured tokens.
Authoritative measured tokens and actual cost always take precedence when present.

## Privacy rules for local usage logs

Local usage logs can contain sensitive material. The contract for any code that
reads them (and for the `usage` object that ends up in a result bundle) is that
**only integer token counts may be extracted.** Specifically, MakerBench telemetry
must never read, store, or emit:

- raw log files or any verbatim log content,
- prompts, completions, or any message text,
- local usernames, home directories, or filesystem paths,
- account, organization, or session identifiers,
- API keys or other secrets,
- timestamps or unrelated session metadata.

`makerbench/usage_logs.py` enforces this mechanically. It reads token fields only
through an explicit key allowlist (`_TOKEN_KEY_ALIASES`), coerces each to a
non-negative integer, and ignores everything else in the input. The result object
carries token counts plus the tool name/version — nothing path- or content-shaped.

### The opt-in local-log reader

The reader is deliberately small and safe:

- **Opt-in** — nothing runs automatically; a caller passes an explicit file path.
- **Reads only that path** — `load_ccusage_export(path, model=...)` reads the one
  file given, parses it, keeps only token integers, and never copies or persists
  the raw bytes. `usage_from_ccusage_export(dict, ...)` does the same for an
  already-parsed dict; `sanitize_local_log_usage(...)` builds a report from
  explicit token kwargs.
- **Fails closed** — on a missing file, malformed JSON, an unexpected shape, or no
  usable token counts, every entry point returns a token-free
  `subscription_opaque` report instead of raising. Telemetry can therefore never
  block or crash a benchmark run.
- **Never commits raw logs** — no usage log is written into the repository or a
  result bundle; only the sanitized `UsageReport` (token integers + tool
  name/version) travels onward.

## Relation to other contracts

- [`SUBMISSION_CONTRACT.md`](SUBMISSION_CONTRACT.md) — the result payload and the
  optional `usage` / `cost` / `runtime` objects this refines.
- [`TOOL_CONTRACT.md`](TOOL_CONTRACT.md) — tool-call disclosure has the same
  redaction discipline (no secrets, no private paths) applied to traces.
- Usage/cost telemetry never affects scores, levels, or regrade reconstruction.
