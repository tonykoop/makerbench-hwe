#!/usr/bin/env python3
"""Copy recorded public-study PNGs and objective metadata; no CAD or model calls."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
GATES = {"renders", "watertight", "nonzero_volume", "fits_envelope", "body_count", "min_wall"}
MODELS = ("claude-code-opus-5.5", "claude-code-sonnet-5.5")
BACKENDS = ("openscad", "cadquery", "build123d")


def prepare(root: Path, model_run: Path, backend_runs: dict[str, Path]):
    records = []
    copies = []
    sources = [("post3-models", "openscad", model_run, MODELS)]
    sources += [("post3-backends", backend, backend_runs[backend], (MODELS[1],)) for backend in BACKENDS]
    for case, backend, run, entrants in sources:
        run = run.resolve()
        log = run / "run_log.json"
        content = json.loads(log.read_text())
        selected = [trial for trial in content["trials"] if trial["model_id"] in entrants]
        keys = [(trial["model_id"], trial["seed"]) for trial in selected]
        if sorted(keys) != sorted((entrant, seed) for entrant in entrants for seed in range(3)):
            raise ValueError("Each recorded entrant must have exactly seeds 0, 1 and 2")
        published = ("docs/showcase/post3/matchup-model/objective_scoreline.json" if case == "post3-models"
                     else f"docs/showcase/post3/matchup-backend/after/objective_scoreline-{backend}.json")
        rows = json.loads((root / published).read_text())["rows"]
        trials = []
        for trial in sorted(selected, key=lambda item: (item["model_id"], item["seed"])):
            result = trial["result"]
            if (trial["status"] != "scored" or result["status"] != "scored"
                    or result["backend"] != backend or not result["render_ok"]
                    or trial["instrument_id"] != "ocarina" or trial["rep"] != 0
                    or result["context_tier"] != "blind"
                    or result["instrument_id"] != trial["instrument_id"]
                    or result["model_id"] != trial["model_id"] or result["seed"] != trial["seed"]
                    or trial["trial_id"] != f'ocarina__seed{trial["seed"]}__rep0__{trial["model_id"]}'):
                raise ValueError("Unexpected study trial or incomplete render")
            gates = result["objective"]["sub_scores"]
            if set(gates) != GATES or any(value not in (0, 1) for value in gates.values()):
                raise ValueError("Expected exactly the six recorded binary build checks")
            rate = result["objective"]["objective_pass_rate"]
            if abs(rate - sum(gates.values()) / 6) > 1e-6:
                raise ValueError("Recorded check scores disagree with the objective average")
            entrant, seed = trial["model_id"], trial["seed"]
            png = run / "render" / trial["trial_id"] / "preview.png"
            if (png.resolve() != png or not png.resolve().is_relative_to(run)
                    or not png.is_file() or not png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")):
                raise ValueError("Preview must be a contained recorded PNG")
            with Image.open(png) as preview:
                if preview.format != "PNG" or set(preview.info) - {"gamma", "srgb", "dpi", "transparency"}:
                    raise ValueError("Recorded previews must be PNGs without textual metadata")
                preview.verify()
            name = entrant.removeprefix("claude-code-") if case == "post3-models" else backend
            directory = "matchup-model" if case == "post3-models" else "matchup-backend/after"
            relative = f"docs/showcase/post3/{directory}/scored/{name}-seed{seed}.png"
            copies.append((png, root / relative))
            trials.append({"entrant": entrant, "backend": backend, "seed": seed,
                           "objective_pass_rate": rate, "gates": gates, "image": relative,
                           "png_sha256": hashlib.sha256(png.read_bytes()).hexdigest()})
        for entrant in entrants:
            actual = [trial for trial in trials if trial["entrant"] == entrant]
            row = next(row for row in rows if row["entrant"] == entrant and row["backend"] == backend)
            if (row["n_objective_trials"] != len(actual)
                    or abs(row["objective_pass_rate"] - sum(t["objective_pass_rate"] for t in actual) / len(actual)) > 1e-6):
                raise ValueError("Recorded trials disagree with the committed public scoreline")
        records.append({"case": case, "backend": backend, "published_scoreline": published,
                        "run_log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(), "trials": trials})
    return {"schema": "makerbench-studio-repeat-renders-v1", "records": records}, copies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-run", type=Path, required=True)
    for backend in BACKENDS:
        parser.add_argument(f"--{backend}-run", type=Path, required=True)
    args = parser.parse_args()
    content, copies = prepare(ROOT, args.model_run,
                              {backend: getattr(args, backend + "_run") for backend in BACKENDS})
    # Everything is validated before any output; original records and PNGs stay untouched.
    for source, destination in copies:
        if destination.exists() and destination.read_bytes() != source.read_bytes():
            raise ValueError("Existing public preview differs; refusing to replace it")
    output = ROOT / "docs/showcase/post3/studio-repeat-runs.json"
    for source, destination in copies:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    output.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n")
    print(f"Copied {len(copies)} recorded PNGs and objective metadata; no source geometry or model calls")


if __name__ == "__main__":
    main()
