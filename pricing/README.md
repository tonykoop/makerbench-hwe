# MakerBench Pricing Tables

Pricing files are versioned snapshots used only for reproducible cost
estimates. A result row should include `cost.pricing_ref` when a cost estimate
was computed, for example `pricing/openai-2026-06-02.json#gpt-5.5`.

Rules:

- Estimate cost only from measured token usage and a matching pricing entry.
- Leave cost as `not_available` when usage is missing, subscription-routed, or
  the model is not in a pricing table.
- Do not infer marginal cost for ChatGPT, Codex, Claude Code, Antigravity, or
  other subscription-routed CLI runs unless that runtime exposes measured token
  usage and a provider-billed API price applies.
- Treat pricing files as immutable snapshots. Add a new dated file when provider
  pricing changes.

Sources checked on 2026-06-02:

- OpenAI API pricing: https://openai.com/api/pricing/
- Anthropic Claude pricing: https://platform.claude.com/docs/en/about-claude/pricing
- Google Gemini Developer API pricing: https://ai.google.dev/gemini-api/docs/pricing
