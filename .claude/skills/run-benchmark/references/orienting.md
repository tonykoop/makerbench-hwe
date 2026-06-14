# Orienting on an unfamiliar benchmark

The skill's portability comes from this: instead of knowing one benchmark's
commands, you learn *any* benchmark's contract from its own repo, fill in the
same map, and run the same five phases. This file is how you fill the map when
there's no worked reference (i.e. the target isn't MakerBench). Don't skip it and
guess — guessing is how contributors leak answer keys or get PRs auto-rejected.

This applies to **MakerBench-HWE and any other agentic / CAD / code benchmark** —
e.g. GrabCADBench, CADGenBench, SWE-bench and its family, or an internal eval a
team built. The names and commands differ; the *shape* (run → grade → submit, with
a public/private boundary and a verification step) is remarkably consistent. Treat
the worked MakerBench reference as one filled-in example of a map you can fill in
for any of them. When the target is one you don't recognize, the procedure below
is the same regardless of which platform it is.

## The map you're filling

Produce concrete answers to these before running anything. Show them to the user
so they can correct a wrong guess cheaply:

| Field | What you're looking for |
| --- | --- |
| Run command | how a single evaluation is invoked on a model/agent |
| Reproduce/grade | how to re-score an existing output locally (ideally without the answer key) |
| Results path/format | where result files go and the schema/example they follow |
| Aggregate step | how the leaderboard/summary is rebuilt from results |
| Canary / do-not-train | the contamination marker, if any |
| Private boundary | what must never be public (answers, oracles, held-out seeds, solved sources) and where it lives |
| Seeds/splits | which inputs are public/dev vs. held-out/official |
| Submission channel | how a row reaches the leaderboard (PR shape, private intake, attestation) |
| Verification model | states a row can be in and who assigns them |
| CI gates | the checks a PR must pass |

## Where to read it from (in priority order)

1. **Agent/contributor docs** — `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`,
   `README.md`. These usually state the run command, the integrity terms, and the
   submission flow outright. Treat them as authoritative; if they contradict this
   skill, they win.
2. **`docs/` policy files** — anything matching `*SUBMISSION*`, `*CONTRIBUT*`,
   `*ATTEST*`, `*INTEGRITY*`, `*CANARY*`, `*DATA*POLICY*`, `*VERSION*`. These
   carry the bundle contract and verification states.
3. **The harness entry point** — `pyproject.toml` `[project.scripts]`, `setup.cfg`
   console_scripts, a `Makefile`, `justfile`, `noxfile.py`, `tox.ini`, or a
   `scripts/`/`bin/` runner. This is the real run/grade command.
4. **A results example** — a committed sample result, a `schema.py`/JSON schema,
   or an `examples/` payload. This is the output format you must match.
5. **CI workflows** — `.github/workflows/*.yml`. The jobs that run on a PR *are*
   the gates; they also reveal the grade command and any artifact audit.
6. **`.gitignore` / submodules** — `.gitmodules` and ignore rules expose the
   public/private boundary: a private submodule or ignored `**/artifacts/*` tells
   you what's deliberately kept out of public history.

A fast first sweep:

```bash
ls -1; sed -n '1,60p' README.md AGENTS.md CONTRIBUTING.md 2>/dev/null
ls docs 2>/dev/null | grep -iE 'submission|contribut|attest|integrity|canary|policy|version'
grep -nE 'scripts|console_scripts|\[project.scripts\]' pyproject.toml setup.cfg Makefile 2>/dev/null
cat .gitmodules 2>/dev/null; grep -nE 'artifact|oracle|private|answer|gold|heldout|held-out' .gitignore 2>/dev/null
ls .github/workflows 2>/dev/null
```

## Turning the map into action

Once filled, the five phases in `SKILL.md` apply unchanged — you've just swapped
in this benchmark's specific commands and paths. A few adaptation notes:

- **No documented private boundary?** Be conservative: treat any gold/solved/oracle
  content as private, and ask the maintainer before publishing solved outputs.
  Absence of a rule is not permission.
- **No reproduce/regrade command?** Then a score can't be independently verified;
  say so plainly and don't present the row as verified. Offer to add a regrade
  path as a separate contribution.
- **No canary?** Fine for some benchmarks — but still don't train on the data, and
  note the lack of a contamination detector as a real (reportable) gap.
- **Different verification vocabulary?** Map it onto the same idea: a row starts
  unverified and is raised only by the maintainer's regrade/attestation, never by
  the submitter.

## Red flags that should stop you

- You're about to commit a solved source/answer artifact to a public PR.
- You're about to paste oracle/gold/held-out content into an issue, PR, log, or
  external service.
- You're tempted to edit a grader, threshold, or parameter so a score "passes."
- You're about to mark your own row verified/official.

Each of these breaks the benchmark for everyone, not just your row. Stop, re-read
the repo's integrity doc, and route through the private/maintainer path instead.
See [`integrity.md`](integrity.md) for why each one matters.
