"""Correlation report for session telemetry (Epic #569, Story #575).

Loads all sessions from the JSONL store and ranks which signals correlate with
high pull_requests_opened, under a minimum-sample guard.

The minimum-N guard is load-bearing: without it, the report would present
statistical noise from a single session as a "God Mode formula."
"""

from __future__ import annotations

from typing import Any

from .store import read_all

# Signals to correlate against pull_requests_opened.
# Each entry: (friendly_name, extractor_fn)
_SIGNALS = [
    "context_velocity_avg_tokens_per_turn",  # from telemetry dict
    "batch_ratio",                            # from telemetry dict (also in tool_metrics)
    "compaction_events",                      # from telemetry dict
    "code_retention_ratio",                   # from git_metrics dict
    "tool_failure_rate",                      # from tool_metrics dict
]


def correlate(
    path: str = "data/sessions.jsonl",
    min_n: int = 5,
) -> dict[str, Any]:
    """Rank telemetry signals by correlation with pull_requests_opened.

    Returns ``{"status": "insufficient_sample", "n": <int>}`` when fewer than
    ``min_n`` sessions are available — never sells noise as a formula.

    Returns a ranked signal list when ``n >= min_n``.
    """
    sessions = read_all(path=path)
    n = len(sessions)

    if n < min_n:
        return {"status": "insufficient_sample", "n": n, "min_n": min_n}

    # Extract (signal_value, pr_count) pairs per session
    outcomes: list[int] = []
    signal_values: dict[str, list[float]] = {s: [] for s in _SIGNALS}

    for sess in sessions:
        pr_count = int(sess.git_metrics.get("pull_requests_opened", 0))
        outcomes.append(pr_count)
        for sig in _SIGNALS:
            val = _get_signal(sess, sig)
            signal_values[sig].append(float(val) if val is not None else 0.0)

    # Pearson-r correlation (simplified, no scipy dep)
    rankings = []
    for sig in _SIGNALS:
        r = _pearson(signal_values[sig], outcomes)
        rankings.append({"signal": sig, "pearson_r": round(r, 4)})

    rankings.sort(key=lambda x: abs(x["pearson_r"]), reverse=True)

    return {
        "status": "ok",
        "n": n,
        "outcome": "pull_requests_opened",
        "rankings": rankings,
    }


def _get_signal(sess, sig: str) -> float | None:
    # Check telemetry dict first, then tool_metrics, then git_metrics
    for bucket in (sess.telemetry, sess.tool_metrics, sess.git_metrics):
        if sig in bucket:
            return bucket[sig]
    return None


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys)) ** 0.5
    if sx == 0 or sy == 0:
        return 0.0
    return num / (sx * sy)
