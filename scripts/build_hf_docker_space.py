#!/usr/bin/env python3
"""Stage the MakerBench dual-league dashboard as a Hugging Face Docker Space (#98).

Companion to ``build_hf_space.py`` (which stages the static leaderboard mirror).
This one stages the *interactive* Docker Space under ``spaces/hf_dashboard/``:

1. build (or reuse) the run-nav ``runs-manifest.json`` for a directory of run
   dirs (``scripts/generate_run_library.py``, schema ``makerbench-runs-manifest-v1``);
2. bake the dashboard payloads — ``dual_league.json`` / ``inspect_index.json`` /
   ``run_details.json`` — from that manifest (``spaces/hf_dashboard/dashboard_data.py``);
3. copy the Space app (``app.py``, ``geometry_convert.py``, ``dashboard_data.py``,
   ``static/``, ``Dockerfile``, ``requirements.txt``, the Space-card ``README.md``)
   plus the submitted run dirs (``runs/``) into a staging dir
   (default ``dist/hf_docker_space/``, git-ignored).

It does NOT create or upload a Space, and it does NOT run the hidden-seed regrade
worker. Baking the public payloads (submitted artifacts + their already-public
grades) is all this script does; pulling hidden seeds via repo secrets to regrade
is a separate, secret-gated job — see docs/HF_SPACE_DASHBOARD.md. Uploading is a
manual maintainer action (``hf upload``).

Example:
    python scripts/build_hf_docker_space.py --runs-root tests/fixtures/run_nav
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPACE_SRC = REPO_ROOT / "spaces" / "hf_dashboard"
DEFAULT_OUT_DIR = REPO_ROOT / "dist" / "hf_docker_space"

# Space app files copied verbatim into the staging root.
APP_FILES = ("app.py", "dashboard_data.py", "geometry_convert.py",
             "requirements.txt", "Dockerfile", "README.md")
EXCLUDE_DIR_NAMES = {"__pycache__", ".pytest_cache"}


def _load(rel_path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_manifest(runs_root: Path) -> dict:
    """Generate the runs-manifest dict for a runs root via the run-library generator."""
    library = _load("scripts/generate_run_library.py", "generate_run_library")
    views = library.scan_runs(runs_root)
    return library.build_manifest(views, runs_root)


def _ignore(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n in EXCLUDE_DIR_NAMES}


def stage(out_dir: Path, manifest: dict, runs_root: Path) -> int:
    """Bake payloads + copy app, static, and run dirs into out_dir. Return file count."""
    import json

    dashboard = _load("spaces/hf_dashboard/dashboard_data.py", "dashboard_data")

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    # 1. app files + static UI
    for name in APP_FILES:
        shutil.copy2(SPACE_SRC / name, out_dir / name)
    shutil.copytree(SPACE_SRC / "static", out_dir / "static", ignore=_ignore)
    (out_dir / "runs-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    # 2. baked payloads (built from the manifest dict + the public run dirs)
    data_dir = out_dir / "data"
    data_dir.mkdir()
    details = {}
    for entry in manifest.get("runs") or []:
        run_id = entry.get("run_id")
        run_dir = runs_root / run_id
        if (run_dir / "run.json").exists():
            details[run_id] = dashboard.build_run_detail(run_dir)
    payload = {
        "dual_league.json": dashboard.build_dual_league(manifest),
        "inspect_index.json": dashboard.build_inspect_index(manifest),
        "run_details.json": details,
    }
    for fname, data in payload.items():
        (data_dir / fname).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # 3. submitted run dirs (public artifacts the viewer serves)
    shutil.copytree(runs_root, out_dir / "runs", ignore=_ignore)

    if not (out_dir / "data" / "dual_league.json").is_file():
        raise SystemExit("staging produced no data/dual_league.json")
    return sum(1 for p in out_dir.rglob("*") if p.is_file())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs-root", type=Path, required=True,
                        help="directory of run dirs (each with run.json)")
    parser.add_argument("--manifest", type=Path, default=None,
                        help="reuse an existing runs-manifest.json instead of regenerating")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR,
                        help=f"staging directory (default: {DEFAULT_OUT_DIR})")
    args = parser.parse_args()

    import json

    runs_root = args.runs_root.resolve()
    if args.manifest is not None:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    else:
        manifest = build_manifest(runs_root)

    n_files = stage(args.out, manifest, runs_root)
    print(f"Staged {n_files} files in {args.out}")
    print("Smoke-test locally:")
    print(f"  cd {args.out} && docker build -t mb-dashboard . && "
          "docker run --rm -p 7860:7860 mb-dashboard")
    print("Manual upload (maintainer action — see docs/HF_SPACE_DASHBOARD.md):")
    print(f"  hf upload <user-or-org>/<space-name> {args.out} . --repo-type space")
    print("NOTE: this stages PUBLIC submitted artifacts only. The hidden-seed "
          "regrade worker (repo secrets) is a separate secret-gated job; it is "
          "not run here and its seeds are never baked into the Space.")


if __name__ == "__main__":
    main()
