"""Channel-comparison analysis helper (#103) tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "makerbench_channel_comparison",
    ROOT / "scripts" / "channel_comparison_report.py",
)
ccr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ccr)


def _bundle(channel, model, rows):
    return {
        "benchmark_version": "0.1.0",
        "model_identifier": model,
        "agent_identifier": channel,
        "results": rows,
    }


def _row(task, seed, score, *, track="blind", notes=None, wall=None, usage=None, cost=None):
    grade = {"task_id": task, "track": track, "score": score, "levels": []}
    if notes is not None:
        grade["notes"] = notes
    row = {"task_id": task, "seed": seed, "track": track, "grade": grade}
    if wall is not None:
        row["runtime"] = {"schema_version": "0.1", "wall_time_s": wall}
    if usage is not None:
        row["usage"] = usage
    if cost is not None:
        row["cost"] = cost
    return row


def test_channels_stay_separate_and_means_exclude_infra():
    cli = _bundle("claude_cli", "claude-code-sonnet-4-6", [
        _row("vented_plate", 0, 4),
        _row("vented_plate", 1, 2),
        _row("vented_plate", 2, 0, notes="agent_error"),  # infra → excluded
    ])
    api = _bundle("anthropic_api", "claude-sonnet-4-6", [
        _row("vented_plate", 0, 3),
        _row("vented_plate", 1, 3),
    ])

    cells = ccr.summarize_cells([cli, api])
    cli_stats = ccr.cell_stats(cells[("claude_cli", "claude-code-sonnet-4-6", "vented_plate", "blind")])
    api_stats = ccr.cell_stats(cells[("anthropic_api", "claude-sonnet-4-6", "vented_plate", "blind")])

    # Same family, different channels → two distinct cells, never conflated.
    assert cli_stats["n_seeds"] == 2 and cli_stats["n_infra"] == 1
    assert cli_stats["mean_score"] == 3.0  # (4+2)/2, infra excluded
    assert api_stats["n_seeds"] == 2 and api_stats["mean_score"] == 3.0


def test_telemetry_split_is_honest_measured_vs_local_log_vs_opaque():
    api = _bundle("anthropic_api", "claude-sonnet-4-6", [
        _row("vented_plate", 0, 4,
             usage={"source": "measured", "measurement_authority": "api_billing",
                    "total_tokens": 1000},
             cost={"source": "estimated", "total_cost_usd": 0.02}),
    ])
    cli = _bundle("claude_cli", "claude-code-sonnet-4-6", [
        _row("vented_plate", 0, 4,
             usage={"source": "local_log", "estimated": True, "total_tokens": 800},
             cost={"source": "not_available", "api_equivalent_usd": 0.011}),
        _row("vented_plate", 1, 3, usage={"source": "subscription_opaque"}),
    ])

    cells = ccr.summarize_cells([api, cli])
    api_s = ccr.cell_stats(cells[("anthropic_api", "claude-sonnet-4-6", "vented_plate", "blind")])
    cli_s = ccr.cell_stats(cells[("claude_cli", "claude-code-sonnet-4-6", "vented_plate", "blind")])

    # API: authoritative measured tokens + real cost.
    assert api_s["mean_measured_tokens"] == 1000.0
    assert api_s["mean_actual_cost_usd"] == 0.02
    assert api_s["mean_local_log_tokens"] is None

    # Subscription: local-log tokens (not measured), no actual cost, only api-equiv.
    assert cli_s["mean_measured_tokens"] is None
    assert cli_s["mean_local_log_tokens"] == 800.0
    assert cli_s["mean_actual_cost_usd"] is None
    assert cli_s["mean_api_equivalent_usd"] == 0.011
    assert cli_s["usage_sources"] == {"local_log": 1, "subscription_opaque": 1}


def test_format_markdown_pairs_channels_and_never_shows_estimate_as_cost():
    cli = _bundle("claude_cli", "claude-code-sonnet-4-6", [
        _row("vented_plate", 0, 4,
             usage={"source": "local_log", "total_tokens": 800},
             cost={"source": "not_available", "api_equivalent_usd": 0.011}),
    ])
    api = _bundle("anthropic_api", "claude-sonnet-4-6", [
        _row("vented_plate", 0, 3,
             usage={"source": "measured", "total_tokens": 1000},
             cost={"source": "estimated", "total_cost_usd": 0.02}),
    ])

    md = ccr.format_markdown(ccr.summarize_cells([cli, api]))

    assert "claude_cli mean (n)" in md and "anthropic_api mean (n)" in md
    assert "Δ (api − cli)" in md
    # Subscription cost is opaque, never $0; api-equivalent is labelled an estimate.
    assert "opaque" in md
    assert "est" in md and "~$0.0110" in md


def test_iter_result_bundles_reads_only_valid_result_jsons(tmp_path):
    good = tmp_path / "claude-cli" / "sonnet-4-6"
    good.mkdir(parents=True)
    (good / "vented_plate-blind.json").write_text(
        json.dumps(_bundle("claude_cli", "m", [_row("vented_plate", 0, 4)])),
        encoding="utf-8",
    )
    (good / "not-a-result.json").write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
    (good / "broken.json").write_text("{not json", encoding="utf-8")

    bundles = [data for _p, data in ccr.iter_result_bundles(tmp_path)]
    assert len(bundles) == 1
    assert bundles[0]["agent_identifier"] == "claude_cli"
