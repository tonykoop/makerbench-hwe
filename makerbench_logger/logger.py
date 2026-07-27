"""WorkflowLogger — the drop-in recorder agents use to emit a WorkflowManifest.

Wrap an agent run, log each tool-call (timestamp, params, human-steering level),
and on exit compute the Human Intervention Index + autonomy ratio and emit a
schema-valid ``workflow_manifest.json`` (mb#89).

Stdlib-only. Drop into any Python agent::

    from makerbench_logger import WorkflowLogger

    with WorkflowLogger(task_id="bracket_v1", seed=0, framework="blender-mcp") as log:
        log.tool_call("blender.add_cube", {"size": 20})            # autonomous (L0)
        log.tool_call("blender.bevel", {"width": 1}, steering="L1")  # nudged by a human
    log.emit("workflow_manifest.json")
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

from .manifest import (
    HII_L0,
    HII_L1,
    HII_L2,
    HumanInterventionIndex,
    Metrics,
    ProvenanceTrace,
    Stack,
    ToolCall,
    WorkflowManifest,
    validate_fail_closed,
    validate_with_schema,
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowLogger:
    """Records an agent run and emits a :class:`WorkflowManifest`.

    Usable as a context manager (wall-clock is captured automatically between
    ``__enter__`` and ``__exit__``) or standalone (call :meth:`start`/:meth:`stop`).
    """

    def __init__(
        self,
        *,
        run_id: Optional[str] = None,
        task_id: Optional[str] = None,
        seed: Optional[int] = None,
        orchestrator: Optional[str] = None,
        framework: Optional[str] = None,
        host_application: Optional[str] = None,
        execution_bridge: Optional[str] = None,
        stack: Optional[Stack] = None,
    ) -> None:
        self.run_id = run_id
        self.task_id = task_id
        self.seed = seed
        self.stack = stack or Stack(
            orchestrator=orchestrator,
            framework=framework,
            host_application=host_application,
            execution_bridge=execution_bridge,
        )
        self._calls: list[ToolCall] = []
        self._artifacts: list[str] = []
        self._notes: list[str] = []
        self._inference_tokens: Optional[int] = None
        self._estimated_cost_usd: Optional[float] = None
        self._provenance = ProvenanceTrace()
        self._created_at = _utc_now_iso()
        self._start_perf: Optional[float] = None
        self._wall_clock_seconds: Optional[float] = None
        self._iteration = 0
        # Result of the most recent emit() validation: (ok, reason).
        self.last_validation: tuple[bool, Optional[str]] = (True, None)

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> "WorkflowLogger":
        self._start_perf = time.perf_counter()
        return self

    def stop(self) -> "WorkflowLogger":
        if self._start_perf is not None:
            self._wall_clock_seconds = round(time.perf_counter() - self._start_perf, 4)
        return self

    def __enter__(self) -> "WorkflowLogger":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # -- recording -----------------------------------------------------------
    def next_iteration(self) -> int:
        """Advance the iteration counter (one design/feedback loop)."""
        self._iteration += 1
        return self._iteration

    def tool_call(
        self,
        name: str,
        params: Optional[dict[str, Any]] = None,
        *,
        steering: Any = HII_L0,
        iteration: Optional[int] = None,
        note: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> ToolCall:
        """Record one tool-call. ``steering`` is the HII level (L0/L1/L2 or 0/1/2)."""
        call = ToolCall(
            name=name,
            iteration=self._iteration if iteration is None else iteration,
            timestamp=timestamp or _utc_now_iso(),
            params=dict(params or {}),
            human_steering=steering,
            note=note,
        )
        self._calls.append(call)
        return call

    def add_artifact(self, path: str) -> None:
        self._artifacts.append(path)

    def add_note(self, note: str) -> None:
        self._notes.append(note)

    def set_metrics(
        self,
        *,
        inference_tokens: Optional[int] = None,
        estimated_cost_usd: Optional[float] = None,
    ) -> None:
        if inference_tokens is not None:
            self._inference_tokens = inference_tokens
        if estimated_cost_usd is not None:
            self._estimated_cost_usd = estimated_cost_usd

    def set_provenance(
        self,
        *,
        tool_call_log_url: Optional[str] = None,
        session_recording_hash: Optional[str] = None,
    ) -> None:
        if tool_call_log_url is not None:
            self._provenance.tool_call_log_url = tool_call_log_url
        if session_recording_hash is not None:
            self._provenance.session_recording_hash = session_recording_hash

    # -- output --------------------------------------------------------------
    def build(self) -> WorkflowManifest:
        """Assemble the in-memory :class:`WorkflowManifest`."""
        if self._wall_clock_seconds is None and self._start_perf is not None:
            # context manager not yet exited; snapshot elapsed so far
            self._wall_clock_seconds = round(time.perf_counter() - self._start_perf, 4)
        metrics = Metrics(
            wall_clock_seconds=self._wall_clock_seconds,
            inference_tokens=self._inference_tokens,
            tool_calls_count=len(self._calls),
            estimated_cost_usd=self._estimated_cost_usd,
        )
        return WorkflowManifest(
            run_id=self.run_id,
            task_id=self.task_id,
            seed=self.seed,
            created_at=self._created_at,
            stack=self.stack,
            metrics=metrics,
            hii=HumanInterventionIndex.from_calls(self._calls),
            provenance_trace=self._provenance,
            tool_call_log=list(self._calls),
            artifacts=list(self._artifacts),
            notes=list(self._notes),
        )

    def to_dict(self) -> dict[str, Any]:
        return self.build().to_dict()

    def emit(
        self,
        path: Union[str, Path],
        *,
        indent: int = 2,
        validate: Union[bool, str] = True,
        write_mbc: bool = False,
    ) -> dict[str, Any]:
        """Write ``workflow_manifest.json`` and return the manifest dict.

        ``validate`` controls schema enforcement:

        * ``True`` (default) — round-trip through the authoritative
          ``makerbench.schema`` (when importable) and **raise ``ValueError``** on
          mismatch. Backward-compatible strict behavior.
        * ``False`` — skip validation entirely.
        * ``"soft"`` — **fail closed without raising**: validate via
          :func:`validate_fail_closed`, still write the manifest to disk so the
          (possibly malformed) evidence is preserved, and record the outcome on
          ``self.last_validation`` (a ``(ok, reason)`` tuple). A grader can then
          treat ``ok is False`` as an automatic zero instead of crashing on a
          garbled agent submission.

        ``write_mbc`` optionally calls bob's ``write_mbc()`` (mb#109) if that
        helper is importable — a no-op otherwise.
        """
        manifest = self.build().to_dict()
        if validate == "soft":
            self.last_validation = validate_fail_closed(manifest)
        elif validate:
            self.last_validation = validate_with_schema(manifest)
            ok, reason = self.last_validation
            if not ok:
                raise ValueError(f"WorkflowManifest failed makerbench.schema validation: {reason}")
        out = Path(path)
        out.write_text(json.dumps(manifest, indent=indent, sort_keys=False), encoding="utf-8")
        if write_mbc:
            _maybe_write_mbc(manifest, out)
        return manifest


def _maybe_write_mbc(manifest: dict[str, Any], manifest_path: Path) -> Optional[Path]:
    """Best-effort call into bob's certificate writer (mb#109) if present."""
    try:
        from makerbench.schema import write_mbc  # type: ignore
    except Exception:
        return None
    try:  # pragma: no cover - exercised only once bob's helper lands
        return write_mbc(manifest, manifest_path)
    except Exception:
        return None
