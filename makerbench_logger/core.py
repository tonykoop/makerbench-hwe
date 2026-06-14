"""WorkflowManifest construction and lightweight run logging."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .schema import make_workflow_manifest, manifest_to_dict

_SECRET_KEY_HINTS = (
    "key", "token", "secret", "password", "passwd",
    "authorization", "auth", "credential", "cookie", "apikey",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _looks_secret(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_KEY_HINTS)


def _scrub_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): _scrub_value(v)
            for k, v in value.items()
            if not _looks_secret(str(k))
        }
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    if isinstance(value, tuple):
        return [_scrub_value(item) for item in value]
    if isinstance(value, str) and _private_oracle_path(value):
        return "[redacted-private-path]"
    return value


def _private_oracle_path(text: str) -> bool:
    segments = {segment.lower() for segment in text.replace("\\", "/").split("/")}
    return "private" in segments or "oracles" in segments


def _normalize_level(event: dict[str, Any]) -> str:
    raw = (
        event.get("human_intervention_level")
        or event.get("hii_level")
        or event.get("intervention_level")
    )
    if raw is None:
        return "L1" if event.get("human_steering") else "L0"
    text = str(raw).strip().upper()
    if text in {"0", "L0", "NONE", "AUTONOMOUS"}:
        return "L0"
    if text in {"1", "L1", "LIGHT", "PARAMETER"}:
        return "L1"
    if text in {"2", "L2", "DIRECT", "HUMAN"}:
        return "L2"
    raise ValueError(f"Unknown human intervention level: {raw!r}")


def _normalize_event(event: dict[str, Any], index: int) -> dict[str, Any]:
    tool = event.get("tool") or event.get("name")
    if not tool:
        raise ValueError(f"tool call {index} is missing 'tool'")
    params = event.get("params", event.get("args", {}))
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError(f"tool call {index} params/args must be a JSON object")
    normalized = {
        "index": index,
        "timestamp": event.get("timestamp") or event.get("started_at") or _utc_now(),
        "tool": str(tool),
        "params": _scrub_value(params),
        "human_steering": event.get("human_steering", False),
        "human_intervention_level": _normalize_level(event),
    }
    optional_keys = (
        "duration_seconds",
        "result_summary",
        "status",
        "notes",
    )
    for key in optional_keys:
        if key in event:
            normalized[key] = _scrub_value(event[key])
    return normalized


def load_tool_log(path: str | Path) -> list[dict[str, Any]]:
    """Load a tool-call log from JSON list/object or JSONL."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        events = [json.loads(line) for line in text.splitlines() if line.strip()]
        if not all(isinstance(event, dict) for event in events):
            raise ValueError("JSONL tool log must contain one object per line")
        return events
    if isinstance(payload, list):
        events = payload
    elif isinstance(payload, dict):
        for key in ("tool_calls", "events", "trace"):
            if key in payload:
                events = payload[key]
                break
        else:
            events = [payload]
    else:
        raise ValueError("Tool log must be a JSON object, JSON list, or JSONL")
    if not all(isinstance(event, dict) for event in events):
        raise ValueError("Tool log entries must be JSON objects")
    return events


def _hii(events: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"L0": 0, "L1": 0, "L2": 0}
    for event in events:
        counts[event["human_intervention_level"]] += 1
    total = sum(counts.values())
    weighted_score = 0.0 if total == 0 else (counts["L1"] + 2 * counts["L2"]) / (2 * total)
    return {
        "levels": counts,
        "total_events": total,
        "weighted_score": round(weighted_score, 6),
    }


def _autonomy_ratio(hii: dict[str, Any]) -> float:
    total = int(hii["total_events"])
    if total == 0:
        return 1.0
    return round(hii["levels"]["L0"] / total, 6)


def build_manifest(
    tool_calls: Iterable[dict[str, Any]],
    *,
    run_id: str,
    stack: dict[str, Any] | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    wall_clock_seconds: float | None = None,
    tokens: dict[str, int] | None = None,
    metadata: dict[str, Any] | None = None,
):
    """Build a WorkflowManifest object from raw tool-call events."""
    normalized_events = [
        _normalize_event(event, index)
        for index, event in enumerate(tool_calls, start=1)
    ]
    hii = _hii(normalized_events)
    metrics = {
        "wall_clock": wall_clock_seconds,
        "wall_clock_seconds": wall_clock_seconds,
        "tokens": tokens or {},
        "tool_calls": len(normalized_events),
    }
    payload = {
        "schema_version": "0.1",
        "manifest_type": "makerbench.workflow_manifest",
        "run_id": run_id,
        "created_at": _utc_now(),
        "started_at": started_at,
        "completed_at": completed_at,
        "stack": stack or {},
        "metrics": metrics,
        "human_intervention_index": hii,
        "autonomy_ratio": _autonomy_ratio(hii),
        "tool_calls": normalized_events,
        "metadata": metadata or {},
    }
    return make_workflow_manifest(payload)


def emit_manifest(manifest: object, out: str | Path) -> None:
    """Write a WorkflowManifest object as stable UTF-8 JSON."""
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest_to_dict(manifest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def find_write_mbc() -> Callable[..., Any] | None:
    """Return Bob's certificate writer when that lane is present."""
    candidates = (
        "makerbench.mbc",
        "makerbench.workflow_certificate",
        "makerbench.certificate",
        "makerbench.schema",
    )
    for module_name in candidates:
        try:
            module = __import__(module_name, fromlist=["write_mbc"])
        except ImportError:
            continue
        writer = getattr(module, "write_mbc", None)
        if callable(writer):
            return writer
    return None


def write_mbc_if_available(manifest: object, out: str | Path) -> bool:
    """Call the optional certificate writer. Returns False when unavailable."""
    writer = find_write_mbc()
    if writer is None:
        return False
    try:
        writer(manifest, out)
    except TypeError:
        writer(manifest=manifest, out=out)
    return True


@dataclass
class RunLogger:
    """Small in-process logger that can wrap tool calls and emit a manifest."""

    run_id: str
    stack: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=_utc_now)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def log_tool_call(
        self,
        tool: str,
        params: dict[str, Any] | None = None,
        *,
        human_steering: bool | str = False,
        human_intervention_level: str | int | None = None,
        result_summary: Any | None = None,
        status: str = "ok",
        duration_seconds: float | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "timestamp": _utc_now(),
            "tool": tool,
            "params": params or {},
            "human_steering": human_steering,
            "status": status,
        }
        if human_intervention_level is not None:
            event["human_intervention_level"] = human_intervention_level
        if result_summary is not None:
            event["result_summary"] = result_summary
        if duration_seconds is not None:
            event["duration_seconds"] = duration_seconds
        self.tool_calls.append(event)

    def wrap_tool(self, name: str, func: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap a callable so each invocation is logged with duration and status."""
        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            try:
                result = func(*args, **kwargs)
            except Exception:
                self.log_tool_call(
                    name,
                    kwargs,
                    status="error",
                    duration_seconds=time.monotonic() - started,
                )
                raise
            self.log_tool_call(
                name,
                kwargs,
                status="ok",
                duration_seconds=time.monotonic() - started,
            )
            return result

        return _wrapped

    def manifest(
        self,
        *,
        completed_at: str | None = None,
        wall_clock_seconds: float | None = None,
        tokens: dict[str, int] | None = None,
    ):
        completed = completed_at or _utc_now()
        return build_manifest(
            self.tool_calls,
            run_id=self.run_id,
            stack=self.stack,
            started_at=self.started_at,
            completed_at=completed,
            wall_clock_seconds=wall_clock_seconds,
            tokens=tokens,
            metadata=self.metadata,
        )

    def emit(self, out: str | Path, **kwargs: Any) -> None:
        emit_manifest(self.manifest(**kwargs), out)
