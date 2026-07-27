"""Agent post-mortem benchmarking & telemetry engine (Epic #569).

Public API — the harvest -> store -> report flow:

    from telemetry import harvest, correlate

    # 1. Harvest a finished session directory into the JSONL store.
    payload = harvest("runs/session-42", store_path="data/sessions.jsonl")

    # 2. Once enough sessions have accumulated, rank the signals that
    #    correlate with pull_requests_opened.
    report = correlate("data/sessions.jsonl", min_n=5)

The same flow is reachable from the command line via ``python -m telemetry``
or the installed ``makerbench-telemetry`` console script.
"""

from __future__ import annotations

from .harvester import harvest
from .report import correlate
from .schema import SessionTelemetry
from .store import append, read_all

__all__ = [
    "harvest",
    "correlate",
    "SessionTelemetry",
    "append",
    "read_all",
]

__version__ = "0.1.0"
