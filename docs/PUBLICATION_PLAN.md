# Publication checklist — HF Space mirror + arXiv tech report (issue #53)

Maintainer checklist for the two remaining public-discovery steps from the
2026-06 landscape sweep ([STRATEGY_MEMO.md](STRATEGY_MEMO.md), "Distribution
Plan"): mirroring the leaderboard into a Hugging Face Space and submitting a
short arXiv technical report. Everything here is a manual maintainer action;
nothing in this repo publishes anything automatically.

**State as of 2026-06-10:**

- Live: GitHub Pages leaderboard (<https://tonykoop.github.io/makerbench-hwe/>),
  regenerated from `results/` by `.github/workflows/pages.yml`.
- Citable substance on `main`: the DFM rule catalog
  ([DFM_RULES.md](DFM_RULES.md), #48), the verified landscape survey
  ([LANDSCAPE.md](LANDSCAPE.md) / [landscape.yaml](landscape.yaml)), the
  runnable `brep-build123d` family (`tasks/brep_plate_hole_pattern/`,
  [BREP_PROFILE.md](BREP_PROFILE.md), #47), and the CADGenBench
  cross-submission prep ([CADGENBENCH_SUBMISSION.md](CADGENBENCH_SUBMISSION.md),
  #62 — note **#52 is still open**: no CADGenBench submission has been made).
- Not yet existing: any Hugging Face presence, any arXiv ID.
- Paper source: [ARXIV_TECH_REPORT.md](ARXIV_TECH_REPORT.md) (draft in this
  repo).

Claim rules while working through this list: follow the "Public Claim
Guardrails" section of [STRATEGY_MEMO.md](STRATEGY_MEMO.md). In particular, do
not link to the Space before it is uploaded, do not cite an arXiv ID before
the announcement email arrives, and do not describe #52 as done until a
submission actually exists on the CADGenBench board.

---

## 1. Hugging Face Space mirror (static)

The Pages site is a self-contained static build (stdlib-only generator, no
framework, Chart.js from CDN), so the Space is a byte-level mirror of the same
output — no second site to maintain.

- [ ] Stage the Space contents:

  ```bash
  python scripts/build_hf_space.py
  ```

  This regenerates `site/data/` via `site/build_data.py`, copies the static
  site into `dist/hf_space/` (git-ignored), and writes a Space card
  `README.md` with `sdk: static` frontmatter. `--skip-build` reuses the
  committed site data; `--out` overrides the staging directory.

- [ ] Smoke-test the staged copy locally (the page fetches
  `data/leaderboard.json`, which `file://` may block):

  ```bash
  cd dist/hf_space && python -m http.server 8000
  ```

  Check: leaderboard table renders, charts draw, track toggle works, blog
  pages open.

- [ ] Decide the owner: personal account (`tonykoop`) is fine for v1; an org
  can adopt the Space later without breaking the slug badly (HF redirects
  renames, but pick deliberately).

- [ ] Create the Space (SDK **Static**, license Apache-2.0), e.g.
  `tonykoop/makerbench-hwe-leaderboard`, then upload the staged tree:

  ```bash
  hf upload tonykoop/makerbench-hwe-leaderboard dist/hf_space . --repo-type space
  ```

  (or `git clone` the Space repo and copy/commit/push.)

- [ ] Verify the live Space: index renders, `data/leaderboard.json` fetch
  succeeds, Chart.js CDN loads inside the HF iframe.

- [ ] Refresh policy: the Space is a mirror, not a second source of truth.
  After new results land on `main` and Pages redeploys, rerun
  `build_hf_space.py` and re-upload. Manual refresh is acceptable for v1; a
  scheduled Action with an `HF_TOKEN` secret is a later nicety, not a
  prerequisite.

- [ ] Only after the Space is live: add the cross-links in section 4.

## 2. arXiv tech report

- [ ] Source of truth: [ARXIV_TECH_REPORT.md](ARXIV_TECH_REPORT.md). Before
  converting, refresh anything dated: regenerate the leaderboard
  (`python site/build_data.py`), update the results-snapshot numbers in the
  draft, and re-verify any landscape claim being quoted (LANDSCAPE.md entries
  were verified 2026-06-10; re-check leaderboard counts/top scores per the
  STRATEGY_MEMO guardrails before quoting).
- [ ] Convert to LaTeX (arXiv strongly prefers LaTeX source):
  `pandoc docs/ARXIV_TECH_REPORT.md -o paper/main.tex --standalone` as a
  starting point, then hand-fix into a standard `article`/`acmart`-free
  layout. Figures: the committed charts under `docs/images/`
  (`efficiency-*.png`, `profiles-*.png`) plus, optionally, a failure-gallery
  render.
- [ ] Category: **cs.AI** primary; consider cross-listing **cs.CE**
  (computational engineering) and/or **cs.SE**. First-time submission to a
  category may require endorsement — start that early if needed.
- [ ] arXiv license selection: CC BY 4.0 recommended (matches the repo's
  open posture; the repo code itself stays Apache-2.0).
- [ ] Tag a repo release (e.g. `v0.1.x`) at the commit whose numbers the paper
  quotes, so the paper can cite an immutable ref.
- [ ] Submit; after the announcement email, record the arXiv ID and update the
  draft's header, the README citation pointer, and CITATION.cff (section 3).
- [ ] Optional, after the ID exists: claim the paper on Hugging Face Papers
  and link the Space to it, which is how HF surfaces paper↔Space pairs.

## 3. CITATION.cff update — blocked until an arXiv ID exists

> **TODO (do not do this early):** `CITATION.cff` currently cites the
> repository as software, which is correct today. Only after the arXiv
> announcement provides a real ID, add a `preferred-citation` block — and
> never commit a placeholder ID.

When the ID exists (replace `XXXX.XXXXX` with the real one):

```yaml
preferred-citation:
  type: report
  title: "MakerBench-HWE: deterministic, multi-process manufacturability evaluation for hardware-design agents"
  authors:
    - family-names: Koop
      given-names: Tony
  year: 2026
  url: "https://arxiv.org/abs/XXXX.XXXXX"
identifiers:
  - type: other
    value: "arXiv:XXXX.XXXXX"
    description: "arXiv preprint"
```

- [ ] Validate the edited file (cffconvert is the reference validator):
  `pipx run cffconvert --validate` (or `pip install cffconvert` into the
  venv). There is no in-repo test that parses CITATION.cff today, so this
  external validation step is the gate.
- [ ] Bump `version:`/`date-released:` if the paper coincides with a tagged
  release.

## 4. Cross-linking (apply each edge only once both ends exist)

| Edge | Where | When |
| --- | --- | --- |
| Repo → Pages | README "Leaderboard" section | already live |
| Repo → HF Space | README "Leaderboard" section, one line next to the Pages link | after Space upload |
| HF Space → repo + Pages | Space card README (template already emits these links) | at Space upload |
| Repo → arXiv | README citation pointer + CITATION.cff (section 3) | after arXiv ID |
| HF Space → arXiv | Space card README + HF Papers claim | after arXiv ID |
| Paper → repo/Pages/Space | already drafted in ARXIV_TECH_REPORT.md (footnote URLs; fill Space/arXiv slots at conversion time) | at submission |
| Repo ↔ CADGenBench bridge | [LANDSCAPE.md](LANDSCAPE.md) ↔ [CADGENBENCH_SUBMISSION.md](CADGENBENCH_SUBMISSION.md) already cross-linked; add a link to the actual submission row only when #52 completes | after #52 |

Keep the Pages site as the canonical leaderboard URL in all copy; the Space is
described as a mirror.

## 5. Out of scope for this checklist

- CADGenBench submission itself — tracked in #52 with its own
  [remaining manual steps](CADGENBENCH_SUBMISSION.md#remaining-manual-steps).
- Any automation that publishes on push (Space sync Action, arXiv v2 updates).
  Revisit after the manual v1 of each exists.
