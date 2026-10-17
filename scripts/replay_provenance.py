"""Shared, metadata-only provenance validation for recorded replay builders."""
from __future__ import annotations

import copy
import json
from pathlib import Path


def load_regrade_provenance(path: Path) -> dict:
    """An explicit provenance input must be an object, including for JSON null."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("regrade provenance must be an object")
    return value


def validate_regrade_provenance(regrade: dict, hashes: dict[str, str]) -> dict:
    """Require exactly the disclosed fields and hashes of every consumed run log."""
    if not isinstance(regrade, dict):
        raise ValueError("regrade provenance must be an object")
    allowed = {"regraded_by", "regrade_commit", "gate_policy", "source_run_log_sha256"}
    unexpected = set(regrade) - allowed
    if unexpected:
        raise ValueError(f"regrade has unsupported keys: {', '.join(sorted(unexpected))}")
    for key in ("regraded_by", "regrade_commit", "gate_policy"):
        if not isinstance(regrade.get(key), str) or not regrade[key].strip():
            raise ValueError(f"regrade requires {key}")
    if regrade.get("source_run_log_sha256") != hashes:
        raise ValueError("regrade source_run_log_sha256 does not match consumed logs")
    return copy.deepcopy(regrade)
