# Code-CAD A/B Arena — vertical-slice prototype

> Epic #421 · Stories #422 (generator harness) · #425 (Elo engine)  
> Status: **prototype** — for Tony's review before full build-out

A blind, lmarena-style A/B arena where multiple LLM models (and, later, human
and human+AI entrants) generate parametric OpenSCAD code for instruments in the
`instrument-maker` design catalog.  Tony votes blind on rendered pairs, producing
a per-entrant Elo leaderboard.

---

## Quick start

```bash
# Offline smoke test (mock generator, no API keys needed)
python -m pytest arena/tests/ -v

# Generate OpenSCAD for one spec+seed (mock model)
python -m arena.arena --spec dulcimer-3string-v1 --seed 42

# With a real model (requires ANTHROPIC_API_KEY)
ANTHROPIC_API_KEY=sk-ant-... python -m arena.arena \
    --spec dulcimer-3string-v1 --seed 42 --entrant claude-sonnet

# Attempt render (requires openscad on PATH; auto-fails cleanly if absent)
python -m arena.arena --spec dulcimer-3string-v1 --seed 42 --render
```

---

## DoE design

| Variable          | Value                                                    |
|-------------------|----------------------------------------------------------|
| **Independent**   | Entrant (model / human / human+AI)                       |
| **Controlled**    | Instrument spec + seed (held constant across entrants)   |
| **Dependent (subjective)** | Blind A/B vote → Elo rating per entrant       |
| **Dependent (objective)**  | Render pass-rate + DFM/acoustic gate (#423)  |

**Research questions**

1. Do subjective Elo ratings agree with objective pass-rates?  
   (Subjective weighs aesthetics/legibility; objective weighs compilability +
   manufacturability — disagreement is the expected null, not a finding.)

2. How does entrant quality vary over time budget?  
   (Human-track stories #428–430: human-solo vs human+AI under equal time budgets.)

**Single-voter caveat:** Tony is the only voter in this prototype.
Elo ratings are directional signals, not population-level claims.
The `Leaderboard.caveat` field propagates this note to every serialised output.

---

## Architecture

```
arena/
├── models.py          # Entrant, InstrumentSpec, TrialResult, MatchResult
├── generator.py       # Generator ABC + MockGenerator + LLM adapter stubs
├── elo.py             # Pure-Python pairwise Elo engine + leaderboard
├── render.py          # openscad shell adapter (auto-fail if absent)
├── arena.py           # Orchestrator: generation batch → render → Elo
├── specs/
│   └── example_instruments.py  # Inline specs (dulcimer, ukulele, clarinet)
└── tests/             # pytest — all pass offline, no API keys, no openscad
```

### Entrant-generic design

The core abstraction is `Entrant`, not "model".  Every trial result and Elo
update is keyed on `entrant_id`, which can be:

| `entrant_type` | Example `entrant_id`       | Story   |
|----------------|---------------------------|---------|
| `ai`           | `"claude-sonnet"`         | #422    |
| `ai`           | `"gpt-4o"`                | #422    |
| `human`        | `"human:tony"`            | #428 🔜 |
| `human_ai`     | `"human+ai:tony"`         | #429 🔜 |

Stories #428–430 (timed human authoring sessions) plug in by:
1. Creating an `Entrant(entrant_type=HUMAN, time_budget_seconds=300)`
2. Submitting a `TrialResult` with `time_to_submit_seconds` filled in
3. Calling `process_votes(run, matches)` as normal — the Elo engine is agnostic

### Time-budget fields

`TrialResult` carries two optional time fields:

- `time_budget_seconds` — hard limit (from `Entrant`; `None` for untimed AI)
- `time_to_submit_seconds` — actual wall-clock authoring time (human sessions only)

These are `None` for all AI runs and populated by the human-session handler in #428.

---

## Generator harness (#422)

`Generator` is an abstract interface:

```python
class Generator(abc.ABC):
    def generate(self, spec: InstrumentSpec, entrant: Entrant, seed: int) -> GeneratorResult:
        ...
    def is_available(self) -> bool:
        ...
```

**`MockGenerator`** — deterministic, no API keys, used in CI.  
**`AnthropicGenerator`** — skipped unless `ANTHROPIC_API_KEY` is set.  
**`OpenAIGenerator`** — skipped unless `OPENAI_API_KEY` is set.  
**`GoogleGenerator`** — skipped unless `GOOGLE_API_KEY` is set.

To add a new model:
1. Subclass `_LLMStubGenerator`
2. Override `_env_key`, `_model_id`, `_entrant_ids`, `_call_api`
3. Register in `DEFAULT_GENERATORS`

---

## Elo engine (#425)

Pure Python, no dependencies beyond the standard library.

```python
engine = EloEngine(k_factor=32)
engine.process_match(MatchResult(
    spec_id="dulcimer-3string-v1",
    seed=42,
    entrant_a_id="claude-sonnet",
    entrant_b_id="gpt-4o",
    outcome=VoteOutcome.A_WINS,
))
print(engine.leaderboard())
```

### Sampling strategy (avoids all-pairs blowup)

With M entrants, all-pairs = M(M−1)/2 comparisons per spec round (O(M²)).
The default strategy is **Swiss pairing**: rank entrants by current Elo, pair
adjacent-ranked entrants.  This gives O(M) matches per round and concentrates
comparisons where the ranking is most uncertain.

```python
pairs = engine.suggest_next_pairs(SamplingStrategy.SWISS)
# → [("claude-sonnet", "gpt-4o"), ("gemini", "mock"), ...]
```

---

## Render adapter (#423-lite)

`render_scad(scad_source)` shells to `openscad` if present; otherwise returns
`RenderResult(status=RenderStatus.OPENSCAD_NOT_FOUND)`.  **Never raises.**

Non-rendering outputs are recorded as objective losses per the epic's red-team
note: "non-rendering outputs must be auto-losses or the arena starves for pairs."

---

## Selecta provenance boundary

The provenance / one-shot-comparison layer (`Selecta`) is a **PRIVATE** interface.
This prototype references it only as stub comments in `arena.py`:

```python
# Selecta EXTERNAL INTERFACE (private — do not embed internals):
# selecta.record_trial(trial_result)   — log provenance to Selecta
# selecta.get_comparison_pair()        — fetch next pair for blind voting
# selecta.record_vote(match_result)    — log a human vote
```

Wire these in when Selecta access is available in the private repo.

---

## Wiring real models

1. **Anthropic / Claude:**
   ```bash
   pip install anthropic
   export ANTHROPIC_API_KEY=sk-ant-...
   ```
   The `AnthropicGenerator` auto-activates.  Update `_model_id` in
   `arena/generator.py` to pin a specific model slug.

2. **OpenAI / GPT:**
   ```bash
   pip install openai
   export OPENAI_API_KEY=sk-...
   ```

3. **Google / Gemini:**
   ```bash
   pip install google-generativeai
   export GOOGLE_API_KEY=...
   ```

4. **Real instrument registry:**
   Replace `arena/specs/example_instruments.py` with a YAML loader:
   ```python
   import yaml
   specs = [InstrumentSpec(**e) for e in yaml.safe_load(open("registry.yaml"))["instruments"]]
   ```

---

## Future stories (plug-in points)

| Story | Title | Plug-in surface |
|-------|-------|-----------------|
| #423  | Render + objective-score adapter | `arena/render.py` → full DFM gate |
| #424  | Blind A/B vote surface | `arena.py::process_votes()` consumer |
| #426  | DoE orchestration runner | `ArenaConfig` with N specs × M entrants |
| #427  | Results dashboard + agreement analysis | `ArenaRun.leaderboard` + render results |
| #428  | Time-boxed human authoring session | `Entrant(type=HUMAN, time_budget_seconds=…)` + `TrialResult.time_to_submit_seconds` |
| #429  | Human+AI co-pilot mode | `Entrant(type=HUMAN_AI, …)` |
| #430  | Time-budget sweep + quality-vs-time | `ArenaConfig.seeds` sweep + `time_to_submit_seconds` analysis |
