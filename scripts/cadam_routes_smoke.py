#!/usr/bin/env python3
"""CADAM routes side-by-side smoke (#1103): ONE command, safe by default.

    python scripts/cadam_routes_smoke.py                      # plan + redacted preflight; runs nothing, spends nothing
    python scripts/cadam_routes_smoke.py --run --tony-go --yes   # the real run: spends OpenRouter money and subscription quota

The OpenRouter route is NOT hard-capped: nothing limits a request provider-side. The $3 in the plan is only reserved
from the nightly budget and compared after the call (an earlier one-generation Fable 5.1 smoke cost $0.28; a long
generation can cost more). So the real run needs ALL of --run, --tony-go (Tony approved the spend) and --yes (you accept
the uncapped OpenRouter cost). It first runs the same
redacted preflight as `arena preflight` and refuses on NO-GO, then runs one job through the three routes
(OpenRouter Fable 5.1, claude via the CLI bridge, codex via the CLI bridge) with the sambuca photo, and writes a
3-column comparison page. Secrets are read from the nightly secrets file into this process only; never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from makerbench import cadam_routes_smoke as smoke  # noqa: E402
from makerbench import nightly_preflight  # noqa: E402

DEFAULT_SECRETS = "/mnt/c/Users/Tony/Documents/StudioPipeline-Pilot/arena-nightly/makerbench-nightly.env"
DEFAULT_RUNS = "/mnt/c/Users/Tony/Documents/StudioPipeline-Pilot/arena-nightly/runs"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", action="store_true", help="actually run the smoke (needs --tony-go and --yes)")
    p.add_argument("--tony-go", action="store_true", help="Tony's go for the quota/spend; required with --run")
    p.add_argument("--yes", action="store_true", help="accept that the OpenRouter route is not hard-capped; required with --run")
    p.add_argument("--secrets", default=DEFAULT_SECRETS)
    p.add_argument("--runs-root", default=DEFAULT_RUNS)
    p.add_argument("--out", default=smoke.DEFAULT_OUT)
    p.add_argument("--reference", default=smoke.DEFAULT_REFERENCE)
    p.add_argument("--registry", default=str(ROOT / "tasks" / "code_cad_arena" / "registry.json"))
    p.add_argument("--fable", default=smoke.FABLE)
    p.add_argument("--claude-model", default=smoke.CLAUDE_BEST, help="bridge-mapped model id for the claude route")
    p.add_argument("--codex-model", default=smoke.CODEX_BEST, help="bridge-mapped model id for the codex route")
    return p


def main(argv=None, *, executor_factory=None, environ=None) -> int:
    args = build_parser().parse_args(argv)
    env = os.environ if environ is None else environ
    if args.run and not (args.tony_go and args.yes):
        print("refusing: --run spends OpenRouter money (NOT hard-capped; the $3 is only a post-call check) and subscription quota; "
              "it needs both --tony-go (Tony has said go) and --yes (you accept the uncapped OpenRouter cost).", file=sys.stderr)
        return 2
    entrants = smoke.smoke_entrants(fable=args.fable, claude_model=args.claude_model, codex_model=args.codex_model)
    print("\n".join(smoke.plan_lines(entrants, args.reference, args.out)))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    job = smoke.smoke_job(f"cadam-routes-smoke-{stamp}", args.reference, entrants)
    runs_root = Path(args.runs_root)
    workdir = Path(tempfile.mkdtemp(prefix="cadam-routes-smoke-"))
    queue = workdir / "queue.json"
    queue.write_text(json.dumps(smoke.smoke_queue(job), indent=1), encoding="utf-8")

    secrets = Path(args.secrets)
    if args.run and secrets.is_file():  # values go into THIS process only, for the run; never printed
        for key, value in nightly_preflight.parse_secrets_file(secrets).items():
            env.setdefault(key, value)
    report = nightly_preflight.build_report(
        secrets_path=secrets, lock_path=runs_root / ".nightly-cad.lock", queue_path=queue,
        named_paths={"reference_photo": Path(args.reference), "runs_root": runs_root}, environ=env)
    print("\n".join(nightly_preflight.render_report_lines(report)))
    if not args.run:
        print("\nplan only: re-run with --run --tony-go --yes to spend the quota and the (uncapped) OpenRouter cost.")
        return 0 if report.ok else 1
    if not report.ok:
        print("\nrefusing to run: preflight is NO-GO.", file=sys.stderr)
        return 1

    from makerbench.nightly_cad import NightlyExecutor

    factory = executor_factory or NightlyExecutor
    result = factory(queue_path=queue, registry_path=Path(args.registry), output_root=runs_root).run()
    run_dir = Path(result["run_dir"])
    from makerbench.nightly_cad import NightlyEntrant

    labels = {e["entrant_id"]: NightlyEntrant.from_dict(e).route_label for e in entrants}
    routes = {e["entrant_id"]: e["route"] for e in entrants}
    page = smoke.publish(run_dir, Path(args.out), ROOT, order=[e["entrant_id"] for e in entrants], labels=labels, routes=routes)
    print(f"\nrun {result.get('run_id')}: {result.get('status')}; comparison page: {page}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
