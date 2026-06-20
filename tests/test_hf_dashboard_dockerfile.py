from __future__ import annotations

import shlex
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "spaces" / "hf_dashboard" / "Dockerfile"


def _copy_sources() -> list[str]:
    sources: list[str] = []
    for raw in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("COPY "):
            continue
        parts = [part for part in shlex.split(line) if not part.startswith("--")]
        assert parts[0] == "COPY"
        assert len(parts) >= 3
        sources.extend(parts[1:-1])
    return sources


def test_hf_dashboard_dockerfile_uses_explicit_public_copy_list() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    sources = _copy_sources()

    assert "ADD " not in dockerfile
    assert "." not in sources
    assert sources == [
        "spaces/hf_dashboard/requirements.txt",
        "spaces/hf_dashboard/",
        "scripts/generate_run_explorer.py",
        "scripts/generate_run_library.py",
        "site/",
        "results/",
    ]


def test_hf_dashboard_dockerfile_does_not_copy_private_benchmark_material() -> None:
    forbidden_tokens = ("private", "oracle", "oracles", "fixture", "fixtures", "answer")

    for source in _copy_sources():
        lowered = source.lower()
        assert not any(token in lowered for token in forbidden_tokens), source
