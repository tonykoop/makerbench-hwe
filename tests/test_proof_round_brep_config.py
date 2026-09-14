"""Safety and matrix contract for the manager-only B-rep proof example (#756)."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "proof-round-brep.example.json"
DOC = ROOT / "docs" / "CODE_CAD_BREP_PROOF.md"
INSTRUMENTS = ["ocarina", "tongue-drum", "ukulele"]
ENTRANTS = [
    "claude-code-sonnet",
    "codex-gpt-5.6-sol",
    "antigravity-gemini-default",
]


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def test_example_pins_the_proof_matrix_and_cadquery_backend():
    config = _config()
    assert config["backend"] == "cadquery"
    assert config["matrix"] == {
        "instruments": INSTRUMENTS,
        "entrants": ENTRANTS,
        "seeds": [0, 1],
        "reps": 1,
    }
    args = config["arena_run_args"]
    assert args[args.index("--backend") + 1] == "cadquery"
    assert args[args.index("--instruments") + 1].split(",") == INSTRUMENTS
    assert args[args.index("--models") + 1].split(",") == ENTRANTS


def test_example_is_fail_closed_for_manager_only_real_cli_launch():
    config = _config()
    assert config["operator"] == "manager-only"
    assert config["launch_authorized"] is False
    assert config["safety"] == {
        "real_entrant_clis": True,
        "paid_api_calls": False,
        "manager_must_set_launch_authorized": True,
        "requires_approved_prs": [758, 759, 760],
    }
    assert not any("token" in key.lower() or "secret" in key.lower() for key in config)


def test_comparison_note_requires_matching_axes_and_separate_run_dirs():
    config = _config()
    text = DOC.read_text(encoding="utf-8")
    assert config["comparison"]["required_matching_axes"] == [
        "instruments",
        "entrants",
        "seeds",
        "reps",
    ]
    assert "separate directories" in text
    assert "objective_scoreline.json" in text
    assert "Do not compare Elo" in text
    assert "only the sprint manager" in text
