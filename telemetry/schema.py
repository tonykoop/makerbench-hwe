"""SessionTelemetry — canonical per-session payload model (Epic #569, Story #570)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SessionTelemetry(BaseModel):
    session_id: str
    agent_id: str
    duration_seconds: float

    # Context-dynamics signals (populated by context_dynamics.extract)
    telemetry: dict = Field(
        default_factory=lambda: {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "compaction_events": 0,
            "tool_clearing_events": 0,
            "context_velocity_avg_tokens_per_turn": 0.0,
        }
    )

    # Git throughput signals (populated by git_throughput.compute)
    git_metrics: dict = Field(
        default_factory=lambda: {
            "issues_closed": 0,
            "pull_requests_opened": 0,
            "failed_ci_runs_resolved": 0,
        }
    )

    # Tool utility signals (populated by tool_utility.compute)
    tool_metrics: dict = Field(
        default_factory=lambda: {
            "bash_invocations": 0,
            "programmatic_tool_batches": 0,
            "failed_tool_calls": 0,
        }
    )
