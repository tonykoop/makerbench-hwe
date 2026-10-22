"""Production ``revision`` handler for the nightly runner (#1116): continue one entrant's design.

A Morning Review revision request names a source night, one design (trial) of it, a turn and
Tony's note. This handler reads that design's stored candidate source from the source run folder
(confined to it), asks the same entrant, through the arena's own generator factory, for the
complete revised program given the note, and ingests the answer into the new run as an ordinary
scored trial (``ingest_candidate``), so votes, scorelines and the Studio need no special case.

Honest limits, recorded in the trial's provenance rather than hidden:

- the arena stores one candidate per trial, so unless a turn record carries its own
  ``source_path`` the "best turn" resolves to that stored candidate (``turn_source`` says which);
- ``extra_turns`` is the turn allowance asked for in the prompt and recorded; the generator's own
  turn cap still applies;
- only OpenSCAD ``arena`` entrants can be continued; others fail the request with a clear reason.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from . import code_cad_arena_runner as arena_runner
from . import code_cad_providers as providers
from .code_cad_generator import GenerationRequest
from .run_log_io import find_trial


class RevisionUnsupported(RuntimeError):
    """The request cannot be continued by this handler (reason is user-facing)."""


def continue_prompt(source: str, feedback: str, *, from_turn: Any, extra_turns: int,
                    backend: str = "openscad", render_path: Optional[str] = None) -> str:
    return (
        "You are continuing an earlier design after the maker reviewed it.\n\n"
        f"Current source (`{backend}`, your best result at turn {from_turn}):\n\n```{backend}\n{source}\n```\n\n"
        f"Requested change:\n{feedback.strip()}\n\n"
        + (f"Previous render: {render_path}\n\n" if render_path else "")
        + f"You may use up to {extra_turns} further turns. Keep everything that was not asked to change. "
        "Return the complete revised program as the single fenced code block the format requires."
    )


def _contained_file(root: Path, value: object) -> Optional[Path]:
    if not value:
        return None
    path = Path(str(value))
    path = path if path.is_absolute() else root / path
    if path.is_symlink() or not path.is_file():
        return None
    return path if path.resolve().is_relative_to(root.resolve()) else None


def best_turn_source(source_run: Path, row: Mapping[str, Any], from_turn: Any) -> tuple[Path, str]:
    """The source file to continue from and how it was chosen (``turn_source``)."""
    result = row.get("result") if isinstance(row.get("result"), Mapping) else {}
    if isinstance(from_turn, int) and not isinstance(from_turn, bool):
        for turn in result.get("turns") or []:
            if isinstance(turn, Mapping) and turn.get("turn") == from_turn:
                path = _contained_file(source_run, turn.get("source_path"))
                if path is not None:
                    return path, f"turn-{from_turn}-record"
    gen = result.get("gen") if isinstance(result.get("gen"), Mapping) else {}
    path = _contained_file(source_run, gen.get("scad_path"))
    if path is None:
        path = _contained_file(source_run, Path("gen") / str(row.get("trial_id")) / "candidate.scad")
    if path is None:
        raise RevisionUnsupported("the source design has no stored candidate source to continue from")
    return path, "stored-candidate"


def make_revision_handler(
    *,
    resolve_generator: Optional[Callable[..., Callable[[GenerationRequest], str]]] = None,
    ingest: Optional[Callable[..., dict]] = None,
    compiler_for_backend: Optional[Callable[[str], Any]] = None,
) -> Callable[[Any, Any, Path, Mapping[str, object]], float]:
    """The handler ``NightlyExecutor`` calls for a revision job: ``(job, entrant, run_dir, registry) -> cost``.

    The three collaborators resolve at call time (module attributes), so tests swap providers
    and the compiler with fakes; production passes nothing."""

    def handler(job: Any, entrant: Any, run_dir: Path, registry: Mapping[str, object]) -> float:
        resolve = resolve_generator or providers.resolve_generator
        ingest_row = ingest or arena_runner.ingest_candidate
        compile_for = compiler_for_backend or arena_runner.compiler_for_backend
        revision = job.revision or {}
        if entrant.kind != "arena" or entrant.backend != "openscad":
            raise RevisionUnsupported(
                f"revising {entrant.kind}/{entrant.backend} entrants is not supported (OpenSCAD arena entrants only)"
            )
        source_run = Path(str(revision["source_run_dir"]))
        log_path = source_run / "run_log.json"
        if log_path.is_symlink() or not log_path.is_file():
            raise RevisionUnsupported("the source run has no run_log.json")
        row = find_trial(json.loads(log_path.read_text(encoding="utf-8")), str(revision["trial_id"]))
        if row is None:
            raise RevisionUnsupported(f"design {revision['trial_id']} is not in the source run")
        source_path, turn_source = best_turn_source(source_run, row, revision.get("from_turn"))
        source = source_path.read_text(encoding="utf-8", errors="replace")
        prompt = continue_prompt(
            source, str(revision["feedback"]), from_turn=revision.get("from_turn"), extra_turns=int(revision["extra_turns"])
        )
        spec = arena_runner.instrument_spec_from_registry(registry, job.instrument_id)
        request = GenerationRequest(
            model_id=entrant.model_id,
            instrument_id=job.instrument_id,
            seed=job.seed,
            spec=dict(spec),
            prompt=prompt,
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        )
        generator = resolve(entrant.model_id, timeout_s=entrant.timeout_s, backend=entrant.backend)
        revised = generator(request)
        if not str(revised).strip():
            raise RuntimeError("the entrant returned an empty program")
        candidate = Path(run_dir) / "revision" / "candidate.scad"
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(str(revised).rstrip() + "\n", encoding="utf-8")
        ingest_row(
            run_log_path=Path(run_dir) / "run_log.json",
            registry=registry,
            instrument_id=job.instrument_id,
            model_id=entrant.entrant_id,
            scad_path=candidate,
            run_dir=Path(run_dir),
            seed=job.seed,
            compiler=compile_for(entrant.backend),
            provenance_extra={
                "adapter_schema": "makerbench-nightly-revision-v1",
                "revision_of": {
                    "run_id": revision["source_run_id"],
                    "trial_id": revision["trial_id"],
                    "from_turn": revision.get("from_turn"),
                    "extra_turns": revision["extra_turns"],
                    "turn_source": turn_source,
                    "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                },
                "feedback_sha256": hashlib.sha256(str(revision["feedback"]).encode("utf-8")).hexdigest(),
                "prompt_sha256": request.prompt_sha256,
                "model": entrant.model_id,
                "cost_usd": 0.0,
            },
        )
        return 0.0

    return handler
