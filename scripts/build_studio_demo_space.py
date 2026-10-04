#!/usr/bin/env python3
"""Build a fresh, curated Studio demo Space directory locally; never upload."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ("makerbench/__init__.py", "makerbench/arena_studio/__init__.py",
           "makerbench/arena_studio/demo.py", "makerbench/arena_studio/data/showcase.json")
RECIPE = ("app.py", "Dockerfile", "pyproject.toml", "README.md")
PUBLIC_PNG = re.compile(
    r"docs/showcase/(?:post3/matchup-model/scored/(?:opus-5\.5|sonnet-5\.5)-seed[012]|"
    r"post3/matchup-backend/after/scored/(?:openscad|cadquery|build123d)-seed[012]|"
    r"post3/matchup-model/(?:opus-5\.5|sonnet-5\.5)-seed0|"
    r"post3/matchup-backend/after/(?:openscad|cadquery|build123d)-seed0|"
    r"strings/gallery/(?:img/design-[0-9]{3}|grid)|kora/assets/(?:blind|image)-seed[012])\.png"
    r"|docs/showcase/kora/assets/reference-photo-900px\.jpg"
)
PREFERENCE = re.compile(r"^(?:elo.*|votes?.*|voters?.*|ballot.*|ratings?.*|rankings?.*|preference.*)$")
HOST_PATH = re.compile(r"(?:/(?:home|tmp|mnt|Users)/|(?<![A-Za-z])[A-Za-z]:[\\/])")
SOURCE_BASE = "https://github.com/tonykoop/makerbench-hwe/blob/main/"


def audit_source(path, url):
    if (not path.startswith("docs/showcase/") or ".." in Path(path).parts
            or Path(path).suffix != ".md" or url != SOURCE_BASE + path):
        raise ValueError("Source links must point to public showcase write-ups on GitHub")


def audit_image(content, image):
    if image is not None and (not image.startswith("/api/demo/assets/")
                             or image.removeprefix("/api/demo/assets/") not in content["assets"]):
        raise ValueError("Image must reference a declared local demo asset")


def audit_data(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if PREFERENCE.fullmatch(re.sub(r"[^a-z0-9]", "", key.lower())):
                raise ValueError("Preference data is forbidden in the demo")
            audit_data(item)
    elif isinstance(value, list):
        for item in value:
            audit_data(item)
    elif isinstance(value, str) and HOST_PATH.search(value):
        raise ValueError("Host paths are forbidden in the demo")


def audit_snapshot(content):
    audit_data(content)
    if set(content) != {"assets", "cases", "demo", "schema", "verification_status", "hero"}:
        raise ValueError("Unexpected showcase fields")
    if content["schema"] != "makerbench-studio-showcase-v1" or content["demo"] is not True:
        raise ValueError("Unexpected showcase schema")
    if {c["id"] for c in content["cases"]} != {"post3-models", "post3-backends", "strings-gallery", "kora"}:
        raise ValueError("Unexpected showcase groups")
    if set(content["hero"]) != {"image", "alt", "caption"}:
        raise ValueError("Unexpected hero fields")
    audit_image(content, content["hero"]["image"])
    for case in content["cases"]:
        if set(case) - {"id", "title", "instrument", "note", "source", "source_url", "varied_axis", "held", "rows", "story", "photo"}:
            raise ValueError("Unexpected showcase fields")
        audit_source(case["source"], case["source_url"])
        if "photo" in case:
            photo = case["photo"]
            if (case["id"] != "kora" or set(photo) != {"image", "alt", "caption", "credit_url", "license_url"}
                    or photo["credit_url"] != "https://github.com/tonykoop/kora"
                    or photo["license_url"] != "https://creativecommons.org/licenses/by/4.0/"):
                raise ValueError("Unexpected reference photo fields or attribution")
            audit_image(content, photo["image"])
        if "story" in case:
            story = case["story"]
            if case["id"] != "post3-backends" or set(story) != {"title", "summary", "caveat", "rows", "sources"}:
                raise ValueError("Unexpected showcase story fields")
            for source in story["sources"]:
                if set(source) != {"path", "url", "label"}:
                    raise ValueError("Unexpected story source fields")
                audit_source(source["path"], source["url"])
            for row in story["rows"]:
                if set(row) != {"backend", "label", "published", "same_meshes", "fresh", "n_trials"}:
                    raise ValueError("Unexpected historical comparison fields")
                if row["n_trials"] != 3 or any(not 0 <= row[field] <= 1 for field in ("published", "same_meshes", "fresh")):
                    raise ValueError("Historical comparisons must retain three runs and valid averages")
        if case["id"] in {"post3-models", "post3-backends"}:
            seeds = case["held"].get("seeds")
            if seeds != [0, 1, 2] or any(row["n_objective_trials"] != len(seeds) for row in case["rows"]):
                raise ValueError("Ocarina matchup aggregate trial counts must match seeds 0, 1, 2")
            for row in case["rows"]:
                trials = row.get("trials", [])
                if (sorted(trial["seed"] for trial in trials) != seeds
                        or len({trial["image"] for trial in trials}) != len(seeds)):
                    raise ValueError("Ocarina matchup render strips must contain distinct runs for seeds 0, 1, 2")
                if abs(sum(trial["objective_pass_rate"] for trial in trials) / len(seeds) - row["objective_pass_rate"]) > 1e-6:
                    raise ValueError("Ocarina matchup trial averages must match the recorded result")
        for row in case["rows"]:
            if set(row) - {"label", "entrant", "backend", "objective_pass_rate", "n_objective_trials",
                           "status", "image", "recorded_pipeline_rate", "gates", "trials", "context_tier"}:
                raise ValueError("Unexpected showcase fields")
            if "context_tier" in row and (case["id"] != "kora" or row["context_tier"] not in {"blind", "image"}):
                raise ValueError("Unexpected reference context")
            audit_image(content, row["image"])
            for trial in row.get("trials", []):
                if set(trial) != {"seed", "image", "gates", "objective_pass_rate", "failed_checks"}:
                    raise ValueError("Unexpected showcase trial fields")
                audit_image(content, trial["image"])
                if set(trial["gates"]) - {"renders", "watertight", "nonzero_volume", "fits_envelope", "body_count", "min_wall"}:
                    raise ValueError("Unexpected showcase build check")
                for failure in trial["failed_checks"]:
                    if set(failure) - {"check", "measured", "threshold", "tolerance", "unit", "requires", "body_id", "detail"}:
                        raise ValueError("Unexpected showcase failure fields")


def stage(out: Path, root: Path = ROOT) -> dict:
    root = root.resolve()
    out = out.resolve()
    if out.exists():
        raise ValueError("Use a fresh output directory; existing content is never reused")
    tracked = set(subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"]).decode().split("\0"))
    content = json.loads((root / RUNTIME[-1]).read_text())
    audit_snapshot(content)
    sources = {relative: relative for relative in RUNTIME}
    sources.update({relative: relative for relative in tracked
                    if relative.startswith("makerbench/arena_studio/static/")})
    for asset, relative in content["assets"].items():
        if not re.fullmatch(r"[a-f0-9]{20}\.(?:png|jpg)", asset) or not PUBLIC_PNG.fullmatch(relative):
            raise ValueError("Unexpected demo asset reference")
        if Path(asset).suffix != Path(relative).suffix:
            raise ValueError("Demo image extension must match the public asset")
        sources["makerbench/arena_studio/demo_assets/" + asset] = relative
    # Validate every source before creating any output.
    for destination, relative in sources.items():
        source = root / relative
        if (relative not in tracked or source.resolve() != source or not source.is_file()
                or not source.resolve().is_relative_to(root)):
            raise ValueError("Demo source must be a contained, tracked regular file")
        if Path(destination).suffix in {".scad", ".stl", ".step", ".dxf", ".env"}:
            raise ValueError("Source geometry and secrets are forbidden")
    for name in RECIPE:
        source = root / "spaces/studio_demo" / name
        if source.resolve() != source or not source.is_file():
            raise ValueError("Invalid Space recipe file")
    project = (root / "pyproject.toml").read_text().split("[project]", 1)[1].split("\n[", 1)[0]
    version = re.search(r'^version\s*=\s*"([^"]+)"', project, re.M).group(1)
    out.mkdir(parents=True)
    for destination, relative in sources.items():
        target = out / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    for name in RECIPE:
        shutil.copyfile(root / "spaces/studio_demo" / name, out / name)
    recipe = (out / "pyproject.toml").read_text()
    (out / "pyproject.toml").write_text(re.sub(r'^version = ".*"$', f'version = "{version}"', recipe, flags=re.M))
    manifest = {
        "schema": "makerbench-studio-demo-space-build-v1", "profile": "read-only-showcase",
        "project_version": version,
        "source_head": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"]).decode().strip(),
        "source_tree_dirty": bool(subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"])),
        "files": {p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in sorted(out.rglob("*")) if p.is_file()},
    }
    (out / "build-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "runs/studio-demo-space")
    args = parser.parse_args()
    manifest = stage(args.out)
    print(f"Built {len(manifest['files'])} files locally; no deployment performed")


if __name__ == "__main__":
    main()
