# Studio matchup results

Choose a matchup run on Studio's Runs screen to see each recorded trial's
entrant, render, mesh-gate results and wall time side by side. The header names
the varied axes and held values; multiple varying axes are explicitly labeled
factorial. The view shows objective gates without a preference ranking.

The summary API validates the persisted `config.matchup` through the shared
`MatchupMetadata` schema. PNGs are fetched through a trial-specific render
route that resolves the recorded image inside the selected run and refuses
external paths, escaping symlinks and non-PNG files. Missing renders and
missing gate observations are shown explicitly. Declared topology and interface
checks appear when recorded; undeclared checks are not invented.

New orchestrated trials record observed `wall_time_s` for the latest execution
attempt, on both success and error. Rate-limit waiting and previous attempts
are excluded. Resuming a completed trial retains its recorded time. Legacy
trials without timing show “Not recorded”; no duration is inferred from file
timestamps. Non-finite, negative or out-of-range observations stay unknown.
Legacy runs without explicit matchup metadata retain their existing summary.

Real-browser coverage runs through the actual Studio server and render route
in both themes, desktop and phone widths, with WebGL disabled. The desktop
control checks that the entrant cards share a row; phone coverage checks for
horizontal overflow. Fixture renders and gate values are synthetic test data,
not published benchmark results.
