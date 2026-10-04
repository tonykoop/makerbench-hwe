"""Rank-agreement headline with a deterministic 95% bootstrap CI (#857, epic #853).

The arena publishes, per round, the Spearman rank agreement between the
off-site blind-preference ordering and the objective mesh-gate ordering
(``makerbench.code_cad_agreement``). The public headline is the mean of that
statistic over rounds 6-10 (the R5-R10 window, keeping only rounds with >= 3
comparable entrants). This module reproduces that mean from committed data
only (``site/data/arena.json``) and attaches a percentile bootstrap CI.

Resampling unit
---------------
* **Round-level bootstrap (used for the published CI).** The committed data
  carries one rho per round; the per-entrant preference rankings stay off the
  public repo by policy (they are derived from Elo). So the only resampling
  that committed data supports is drawing the five round-level rho values with
  replacement and taking the mean. With five rounds there are only 5**5 = 3125
  equally likely ordered resamples, so the Monte-Carlo CI is cross-checked
  against the exact enumeration (``exact_round_bootstrap_ci``).
* **Entrant-level bootstrap (available, not used for the headline).**
  ``entrant_bootstrap_ci`` resamples entrants within each round when the
  per-entrant paired scores are on hand (a local ``runs/`` tree). With three
  entrants per round it is badly degenerate: only 6/27 resamples keep three
  distinct entrants and 3/27 collapse to a single entrant (rho undefined), so
  it is reported for completeness, never as the headline interval.

Only aggregate agreement numbers leave this module: no Elo, ratings or
per-person numbers, so the output is safe to cite from site/ and docs/.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import random
from pathlib import Path
from typing import Iterable, Optional, Sequence

SCHEMA = "makerbench-rank-agreement-ci-v1"
DEFAULT_SEED = 857
DEFAULT_N_BOOT = 10_000
DEFAULT_LEVEL = 0.95
DEFAULT_ROUNDS = (6, 7, 8, 9, 10)
MIN_ENTRANTS = 3
DEFAULT_ARENA_JSON = Path("site/data/arena.json")
DEFAULT_OUT = Path("docs/showcase/data/agreement-rounds-6-10.json")


# --- statistics ---------------------------------------------------------------


def average_ranks(values: Sequence[float]) -> list[float]:
    """1-based average ranks (ties share the mean of their positions)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        avg = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[order[k]] = avg
        i = j
    return ranks


def spearman_rho(left: Sequence[float], right: Sequence[float]) -> Optional[float]:
    """Tie-aware Spearman rho (Pearson on average ranks).

    Returns ``None`` when fewer than two pairs are given or either side has no
    variance (e.g. every value tied), matching ``code_cad_agreement``.
    """
    if len(left) != len(right):
        raise ValueError("left and right must have the same length")
    if len(left) < 2:
        return None
    a, b = average_ranks(left), average_ranks(right)
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va == 0.0 or vb == 0.0:
        return None
    return num / math.sqrt(va * vb)


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile (numpy's default) of pre-sorted data."""
    if not sorted_values:
        raise ValueError("percentile of empty data")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be in [0, 1]")
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def round_bootstrap_ci(
    rhos: Sequence[float],
    *,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
    level: float = DEFAULT_LEVEL,
) -> tuple[float, float]:
    """Percentile CI of the mean rho, resampling rounds with replacement."""
    rhos = [float(r) for r in rhos]
    if not rhos:
        raise ValueError("need at least one round-level rho")
    if n_boot < 1:
        raise ValueError("n_boot must be positive")
    rng = random.Random(seed)
    n = len(rhos)
    means = sorted(_mean([rhos[rng.randrange(n)] for _ in range(n)]) for _ in range(n_boot))
    alpha = (1.0 - level) / 2.0
    return percentile(means, alpha), percentile(means, 1.0 - alpha)


def exact_round_bootstrap_ci(
    rhos: Sequence[float], *, level: float = DEFAULT_LEVEL
) -> tuple[float, float]:
    """Exact bootstrap percentile CI: enumerate all n**n ordered resamples."""
    rhos = [float(r) for r in rhos]
    n = len(rhos)
    if not 1 <= n <= 7:
        raise ValueError("exact enumeration supports 1..7 rounds")
    means = sorted(_mean(combo) for combo in itertools.product(rhos, repeat=n))
    alpha = (1.0 - level) / 2.0
    return percentile(means, alpha), percentile(means, 1.0 - alpha)


def entrant_bootstrap_ci(
    rounds: Sequence[Sequence[tuple[float, float]]],
    *,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
    level: float = DEFAULT_LEVEL,
) -> dict:
    """Percentile CI of the mean rho, resampling entrants within each round.

    ``rounds`` is one list per round of ``(preference_score, objective_score)``
    pairs, one pair per entrant. A resample whose round rho is undefined (all
    draws the same entrant, or no variance) drops that round from that
    replicate's mean; a replicate with no defined round is discarded. The
    discard share is returned so callers can see how degenerate this is.
    """
    if not rounds:
        raise ValueError("need at least one round")
    rng = random.Random(seed)
    means: list[float] = []
    undefined_rounds = 0
    for _ in range(n_boot):
        per_round = []
        for pairs in rounds:
            n = len(pairs)
            draw = [pairs[rng.randrange(n)] for _ in range(n)]
            rho = spearman_rho([p[0] for p in draw], [p[1] for p in draw])
            if rho is None:
                undefined_rounds += 1
            else:
                per_round.append(rho)
        if per_round:
            means.append(_mean(per_round))
    means.sort()
    alpha = (1.0 - level) / 2.0
    return {
        "ci95_low": percentile(means, alpha) if means else None,
        "ci95_high": percentile(means, 1.0 - alpha) if means else None,
        "n_replicates_used": len(means),
        "undefined_round_share": undefined_rounds / (n_boot * len(rounds)),
    }


# --- committed data -------------------------------------------------------------


def load_round_rhos(
    arena: dict, rounds: Iterable[int] = DEFAULT_ROUNDS, min_entrants: int = MIN_ENTRANTS
) -> list[dict]:
    """Per-round rho from the committed arena page payload.

    Applies the published headline filter: the round is in ``rounds``, has a
    defined rho, and has at least ``min_entrants`` comparable entrants.
    """
    wanted = set(rounds)
    used = []
    for entry in arena.get("rounds") or []:
        number = entry.get("round")
        stat = entry.get("agreement") or {}
        if number not in wanted or stat.get("rho") is None:
            continue
        if (stat.get("n") or 0) < min_entrants:
            continue
        used.append(
            {
                "round": int(number),
                "rank_agreement_rho": float(stat["rho"]),
                "n_entrants": int(stat["n"]),
            }
        )
    used.sort(key=lambda e: e["round"])
    return used


def build_summary(
    arena: dict,
    *,
    rounds: Iterable[int] = DEFAULT_ROUNDS,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = DEFAULT_SEED,
    level: float = DEFAULT_LEVEL,
    source: str = DEFAULT_ARENA_JSON.as_posix(),
) -> dict:
    per_round = load_round_rhos(arena, rounds)
    if not per_round:
        raise ValueError("no comparable rounds in the requested window")
    rhos = [e["rank_agreement_rho"] for e in per_round]
    low, high = round_bootstrap_ci(rhos, n_boot=n_boot, seed=seed, level=level)
    exact_low, exact_high = exact_round_bootstrap_ci(rhos, level=level) if len(rhos) <= 7 else (None, None)
    return {
        "schema": SCHEMA,
        "source": source,
        "statistic": "mean Spearman rank agreement between blind-preference order and objective mesh-gate order",
        "rounds_used": [e["round"] for e in per_round],
        "per_round": per_round,
        "mean_rank_agreement_rho": round(_mean(rhos), 4),
        "ci95_low": round(low, 4),
        "ci95_high": round(high, 4),
        "ci95_exact_low": None if exact_low is None else round(exact_low, 4),
        "ci95_exact_high": None if exact_high is None else round(exact_high, 4),
        "method": {
            "ci": "percentile bootstrap",
            "level": level,
            "resampling_unit": "round",
            "n_boot": n_boot,
            "seed": seed,
            "rng": "python random.Random",
            "filter": f"rounds {min(rounds)}-{max(rounds)} with >= {MIN_ENTRANTS} comparable entrants",
            "note": (
                "Per-entrant preference rankings are not committed (they derive from "
                "off-site Elo), so entrants cannot be resampled from committed data; "
                "with three entrants per round an entrant-level bootstrap would also "
                "be degenerate. The exact CI enumerates all n**n round resamples."
            ),
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arena", type=Path, default=DEFAULT_ARENA_JSON)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--n-boot", type=int, default=DEFAULT_N_BOOT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--check", action="store_true", help="fail if --out is stale")
    args = parser.parse_args(argv)

    arena = json.loads(args.arena.read_text(encoding="utf-8"))
    summary = build_summary(arena, n_boot=args.n_boot, seed=args.seed, source=args.arena.as_posix())
    text = json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print(f"{args.out} is stale; regenerate with python -m makerbench.rank_agreement_ci")
            return 1
        print(f"{args.out} is up to date")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(
        f"mean rho {summary['mean_rank_agreement_rho']} "
        f"95% CI [{summary['ci95_low']}, {summary['ci95_high']}] "
        f"(exact [{summary['ci95_exact_low']}, {summary['ci95_exact_high']}]) -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
