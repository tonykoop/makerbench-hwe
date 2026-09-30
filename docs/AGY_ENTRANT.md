# The agy (Gemini) entrant: headless notes and the #926 fix

`antigravity-*` entrants run the local `agy` CLI (`agy --print <prompt> --print-timeout 15m`,
subscription auth, no API key). Adapter: `make_agy_generator` in
`makerbench/code_cad_providers.py`.

## What went wrong in the frontier re-run (#926)

66 Gemini cells (R1 to R10) came back as adapter errors and the entrant was reported
`PARTIAL_ADAPTER_BLOCK`. The diagnosis:

- **It was tool use, not auth, quota, prompt size, image handling or parsing.** agy's headless
  `--print` mode cannot prompt for a permission. When the model decides to run a shell command,
  the CLI **auto-denies** it, the turn ends, and agy exits **0 with empty stdout**; the only trace
  is a stderr line (`jetski: no output produced — a tool required the "command" permission that
  headless mode cannot prompt for, so it was auto-denied ...`) and `soft-denying tool confirmation
  "RunCommand"` in agy's own log.
- It is **non-deterministic and brief-dependent**. With the real arena prompt for four briefs,
  on `gemini-3.8-flash-high` from an empty cwd: `ocarina` and `kora` answered (14.7k and 21k
  chars); `tongue-drum` was denied at step 4 after 34 s and `kena` at step 36 after 200 s. The
  one-word probes I ran passed (two models); that is an observation, not a guarantee.
- The frontier run used a run-local wrapper that treated the first empty answer as a permanent
  block and refused every later dispatch, so 65 of the 66 cells never called the model.
- Separately, the adapter had **no way to pick a model**: an `antigravity-gemini-3.8-flash-high`
  entrant ran whatever agy's default model was unless a wrapper added `--model`.

## The fix

- **Model selection.** A `--model-map` entry `{"provider": "agy", "model": "gemini-3.8-flash-high"}`
  now passes `--model` (after `--print <prompt>`). Without an explicit `model` nothing is passed:
  the name derived from an entrant id is not guaranteed to be a model `agy models` lists.
- **Prompt note (blind tier).** The prompt ends with an instruction not to run commands or use
  tools and to answer with the single fenced code block. With it, `tongue-drum` (twice) and `kena`
  completed (3 of 3) where the plain prompt was denied on both. It is an instruction, not a
  permission: **no `--dangerously-skip-permissions`, no allow-rule, no `~/.gemini` change**. The
  non-blind tiers keep their read-only allow list and are not given the note.
- **Retry.** A silent denial (exit 0, empty stdout) is retried up to twice more, since the model's
  tool use varies run to run; after that it is reported with agy's stderr reason and the attempt
  count. A non-zero exit keeps its single retry.
- `agy --mode plan` was tried and does not prevent the denial.

Tests use a stubbed `agy` executable (no live call, login or quota): `tests/test_agy_adapter.py`.

## Known limits

The probe is small (four briefs, a handful of calls), so the note lowers the denial rate; it does
not prove it is zero. Vision (image context tier) through agy was not exercised here.
