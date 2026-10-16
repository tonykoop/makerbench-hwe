# Recorded repeat-run pictures for Studio

The Studio demo now shows all three recorded repeat runs for the
[model comparison](matchup-model.md) and the calibrated
[CAD-tool comparison](matchup-backend.md). These 15 PNGs are copies of the
existing scored runs, not new generations or regrades:

- Six previews from `s3-846-model`: Claude Opus 5.5 and Claude Sonnet 5.5,
  runs 0, 1 and 2 each.
- Nine previews from `s5-876-openscad`, `s5-876-cadquery` and
  `s5-876-build123d`: Claude Sonnet 5.5, runs 0, 1 and 2 per CAD tool.

The original model study's two seed-0 pictures belong to separately timed
generations. They stay unchanged. The new `matchup-model/scored/` pictures
belong to the six scored trials used for the published averages;
`matchup-backend/after/scored/` holds the nine calibrated re-run previews.

[`studio-repeat-runs.json`](studio-repeat-runs.json) records only the named
entrants, backend, run number, six binary check results, objective average,
public image path and hashes. Each source log must contain exactly the three
expected runs per entrant, with matching instrument, context and trial IDs.
The importer verifies every trial's six-check average and its agreement with
the committed public scoreline before copying anything. PNGs are verified,
copied byte-for-byte and have no text metadata. Generated CAD, meshes, raw
responses, host paths and source code are not copied. Source records remain
untouched; this extraction supplies no new benchmark attestation.
The source logs are the regraded copies written by `scripts/regrade_scoreline.py` (today's gate;
these 15 trials keep every check result), so `run_log_sha256` hashes the regraded log, whose
`regrade.source_run_log_sha256` names the recorded original.

Use `scripts/import_studio_repeat_renders.py --help` for the four local run
directory arguments. Then rebuild the demo with
`python scripts/build_studio_demo_data.py`; `--check` validates both the
snapshot and its wheel asset mappings. No model or CAD process runs.

The older ocarina matchup and gallery records did not save numerical measurements and
thresholds for each check. Studio says so in its chip explanations rather
than supplying guessed values. Where a public scoreline has the later
`failed_checks` data, Studio retains its measurement, threshold, tolerance,
unit, offending body and explanation. The kora wall-thickness failure uses
the historical values in the [original study](../kora/CASE_STUDY.md), not a
new measurement under the subsequently changed gate. All original study
scores and caveats remain applicable. These checks do not measure sound or
prove that the instrument is ready to build.
