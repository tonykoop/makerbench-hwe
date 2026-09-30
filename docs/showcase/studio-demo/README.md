# Studio demo GIF (about 60 s)

`studio-walkthrough.gif` (800 px wide, 8 fps, about 62 s, 4.5 MB) is a scripted,
captioned walkthrough of Arena Studio, produced by `scripts/record_studio_demo.py`
(story #850, epic #845). Regenerate it rather than screen-recording by hand.

## What it shows

1. The Runs screen listing two local runs (the real #846 and #847 run directories).
2. Opening each run: entrants, instruments, trial count.
3. The DoE matrix in **Vary one axis** mode: axis "models", instrument ocarina, entrants
   `claude-code-opus-5.5, claude-code-sonnet-5.5`, then the preview with the held values
   and the `$0 subscription` cost badges.

## What it does not show

- No model is launched; Studio runs without `--allow-live` and nothing is queued.
- The voting, agreement-analytics and morning-review screens are never opened, so no
  vote count or preference number appears. The run panels show 0 blind votes.
- The ocarina reference image is not approved in the throwaway checkout, so the preview
  says "No image" and lists the instrument under the queue's skip list. That is visible
  in the recording and is accurate for that setup.
- Cosmetic: Studio's "Held values" JSON line runs past the preview panel's right edge
  (an existing #830 UI issue), and the header shows Studio's default voter name.

## Regenerate

```bash
pip install playwright        # plus a Chromium Playwright can launch, and ffmpeg
python3 scripts/record_studio_demo.py \
  runs/code_cad_arena/s3-846-model runs/code_cad_arena/s3-847-cadquery \
  --out docs/showcase/studio-demo/studio-walkthrough.gif
```

The script copies only `run_log.json` and the preview renders of the runs you name
into a throwaway checkout (no `.scad`, `.stl`, `.step` or lock files), starts Studio on
127.0.0.1 against it, drives it with Playwright, records the page and converts the video
with ffmpeg. Run directories stay in the gitignored `runs/`.

## Provenance

- Code: `origin/main` at `9ada91d`. Playwright with Chromium 153, ffmpeg from the distro.
- The two run directories are from `../post3/matchup-model.md` and `../post3/matchup-backend.md`
  (2026-09-30); see those reports for what the runs do and do not show.
- Licence: our own recording of our own UI and data, Apache-2.0. It contains no
  third-party imagery.
