"""Append-only JSONL store for SessionTelemetry records (Epic #569, Story #570)."""

from __future__ import annotations

import json
from pathlib import Path

from .schema import SessionTelemetry


def append(payload: SessionTelemetry, path: str = "data/sessions.jsonl") -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(payload.model_dump_json() + "\n")


def read_all(path: str = "data/sessions.jsonl") -> list[SessionTelemetry]:
    p = Path(path)
    if not p.exists():
        return []
    records = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(SessionTelemetry.model_validate_json(line))
    return records
