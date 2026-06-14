# MakerBench-HWE arXiv staging

This directory is a staging area for issue #53's short technical report.

Regenerate the LaTeX source from the markdown draft before submission:

```bash
pandoc docs/ARXIV_TECH_REPORT.md -o paper/main.tex --standalone
```

Do not submit from this directory until the maintainer has refreshed the
leaderboard snapshot, re-checked landscape claims against primary sources, and
chosen the final arXiv category/license. No arXiv ID exists yet; keep
`CITATION.cff` unchanged until the announcement email provides the real ID.
