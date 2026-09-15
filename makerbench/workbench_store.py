"""Append-only revision store for the design workbench (#788 W2).

Everything the workbench keeps lives under one root, ``runs/workbench/`` in
the repository (gitignored), never in the instrument repos and never in an
arena run directory::

    runs/workbench/<design_id>/
      design.json             origin, instrument, backend, title  (written once)
      index.jsonl             append-only revision index, under file_lock
      curation.jsonl          append-only picks/titles/notes; last line wins
      revisions/<rev_id>/
        source.scad|source.py  the exact bytes that were compiled
        revision.json          provenance; written once, never rewritten
        objective.json         copied from the draft, if it compiled
        artifacts/             copied from the draft, if any
      drafts/<draft_id>/
        source.*, draft.json, job.log, objective.json, artifacts/

**Revisions are immutable.** A revision is staged completely in a
contained scratch directory and published with one ``rename``; an existing
``rev_id`` is a :class:`Conflict` and no byte changes, and a failure before
publication leaves nothing behind (a retry is not a spurious conflict). The
source hash in the provenance is computed from the bytes being saved, under
the index lock, never copied from the draft's mutable metadata. Revision ids are content-addressed
(``r-`` + 16 hex of sha256 over the design, parent, source hash and editor),
so saving the same draft twice is a conflict, not a duplicate, and a sibling
from the same parent gets its own id. ``seq`` is assigned under the index
lock. **Drafts are mutable**: their job status changes as the compile runs,
and they expire.

Ids follow the Studio task-id rule (``[a-z0-9][a-z0-9_-]{0,63}``) and every
path the store touches is resolved and required to stay under the root, so
neither ``..`` nor a symlink can reach outside. The store returns paths
relative to its root; callers never see a host-absolute path in its dicts.

The store does not compile or run anything. The job runner (W3) writes
``job.log``, ``objective.json`` and ``artifacts/`` into the draft and updates
its status through :meth:`WorkbenchStore.update_draft_job`.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import secrets
import shutil
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .run_log_io import atomic_write_json, file_lock

DESIGN_SCHEMA = "makerbench-workbench-design-v1"
DRAFT_SCHEMA = "makerbench-workbench-draft-v1"
REVISION_SCHEMA = "makerbench-workbench-revision-v1"
CURATION_SCHEMA = "makerbench-workbench-curation-v1"

ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
BACKENDS = ("openscad", "cadquery")
SOURCE_NAMES = {"openscad": "source.scad", "cadquery": "source.py"}
EDITOR_KINDS = ("human", "model", "parameters")
#: Model-revision confinement, recorded from launch evidence only (W6, G5):
#: ``verified`` (codex/agy ran inside the entrant sandbox), ``restricted-tools``
#: (claude ran with read-only tools confined to the workspace),
#: ``not_applicable`` (the stub: no process ran) and ``unconfined`` (no
#: evidence, never saveable).
CONFINEMENT_VALUES = ("verified", "unconfined", "restricted-tools", "not_applicable")
ORIGIN_KINDS = ("trial", "master", "blank")
DRAFT_KINDS = ("edit", "parameters", "revise")
JOB_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled", "interrupted")
FINISHED_STATUSES = ("succeeded", "failed", "cancelled", "interrupted")

MAX_SOURCE_BYTES = 256 * 1024
MAX_TEXT_BYTES = 2 * 1024
MAX_FEEDBACK_BYTES = 8 * 1024
DRAFT_TTL_S = 24 * 60 * 60


class WorkbenchError(ValueError):
    """Base for store errors; the API maps subclasses to HTTP statuses."""


class NotFound(WorkbenchError):
    """Unknown design, draft or revision, or a path outside the store."""


class Conflict(WorkbenchError):
    """The revision already exists, or the draft is not in a saveable state."""


class TooLarge(WorkbenchError):
    """A source or text exceeds its size cap."""


def is_valid_id(value: object) -> bool:
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _check_text(value: object, what: str, limit: int = MAX_TEXT_BYTES) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise WorkbenchError(f"{what} must be a string")
    if len(value.encode("utf-8")) > limit:
        raise TooLarge(f"{what} is longer than {limit} bytes")
    return value


def _check_source(source: object) -> str:
    if not isinstance(source, str):
        raise WorkbenchError("source must be a string")
    if "\x00" in source:
        raise WorkbenchError("source must not contain NUL bytes")
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise TooLarge(f"source is longer than {MAX_SOURCE_BYTES} bytes")
    return source


def validate_editor(editor: Mapping[str, Any]) -> dict:
    """Normalise an ``editor`` provenance dict; unknown kinds are refused."""

    if not isinstance(editor, Mapping):
        raise WorkbenchError("editor must be an object")
    kind = editor.get("kind")
    if kind not in EDITOR_KINDS:
        raise WorkbenchError(f"editor.kind must be one of {EDITOR_KINDS}")
    out: dict[str, Any] = {"kind": kind}
    if kind == "human":
        out["voter"] = _check_text(editor.get("voter") or "unknown", "editor.voter")
    elif kind == "parameters":
        changed = editor.get("changed") or {}
        if not isinstance(changed, Mapping):
            raise WorkbenchError("editor.changed must be an object")
        out["changed"] = {str(k): list(v) if isinstance(v, (list, tuple)) else v for k, v in changed.items()}
        out["voter"] = _check_text(editor.get("voter") or "unknown", "editor.voter")
    else:
        out["model_id"] = _check_text(editor.get("model_id"), "editor.model_id")
        out["provider"] = _check_text(editor.get("provider"), "editor.provider")
        confinement = editor.get("confinement")
        if confinement not in CONFINEMENT_VALUES:
            raise WorkbenchError(f"editor.confinement must be one of {CONFINEMENT_VALUES}")
        out["confinement"] = confinement
        prompt = _check_text(editor.get("prompt"), "editor.prompt", MAX_FEEDBACK_BYTES)
        out["prompt_sha256"] = _sha256_text(prompt) if prompt else editor.get("prompt_sha256")
        out["max_turns"] = editor.get("max_turns")
        images = editor.get("reference_images") or []
        out["reference_images"] = [str(i) for i in images][:64]
    return out


def validate_origin(origin: Mapping[str, Any]) -> dict:
    if not isinstance(origin, Mapping) or origin.get("kind") not in ORIGIN_KINDS:
        raise WorkbenchError(f"origin.kind must be one of {ORIGIN_KINDS}")
    out = {"kind": origin["kind"]}
    for key in ("run_id", "trial_id", "model_id", "instrument_id", "repo_rel_path", "file"):
        if key in origin and origin[key] is not None:
            out[key] = _check_text(origin[key], f"origin.{key}")
    return out


def source_diff(a: str, b: str, *, context: int = 3) -> list[dict]:
    """Unified diff as rows ``{"op": " "|"+"|"-", "text": ..}`` plus hunk
    headers ``{"op": "@", "text": ..}``; rendered by the UI as text only."""

    rows = []
    for line in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=context):
        if line.startswith(("---", "+++")):
            continue
        if line.startswith("@@"):
            rows.append({"op": "@", "text": line})
        else:
            rows.append({"op": line[:1], "text": line[1:]})
    return rows


def _copy_artifact_tree(src: Path, dst: Path) -> None:
    """Copy a draft's ``artifacts/`` into staging: regular files and real
    directories only. Any symlink anywhere in the tree (file or directory,
    at any depth) refuses the whole save before a byte is copied, so a link
    to a host file can never become an immutable revision artifact."""

    entries: list[tuple[Path, Path]] = []

    def walk(directory: Path, target: Path) -> None:
        for entry in sorted(os.scandir(directory), key=lambda e: e.name):
            if entry.is_symlink():
                raise WorkbenchError(
                    f"draft artifacts contain a symlink ({entry.name}); refusing to publish"
                )
            if entry.is_dir(follow_symlinks=False):
                walk(Path(entry.path), target / entry.name)
            elif entry.is_file(follow_symlinks=False):
                entries.append((Path(entry.path), target / entry.name))
            else:
                raise WorkbenchError(
                    f"draft artifacts contain a special file ({entry.name}); refusing to publish"
                )

    walk(src, dst)
    dst.mkdir(parents=True, exist_ok=False)
    for source, target in entries:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


class WorkbenchStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    # --- containment ------------------------------------------------------

    def contained(self, design_id: str, *parts: str) -> Path:
        """Resolve ``<root>/<design_id>/<parts...>`` and require it to stay under
        the root. Every id segment must pass the id rule. Never creates."""

        if not is_valid_id(design_id):
            raise NotFound("unknown design")
        for part in parts:
            if not isinstance(part, str) or not part or "/" in part or "\\" in part or part in (".", ".."):
                raise NotFound("unknown path")
        candidate = self.root.joinpath(design_id, *parts)
        resolved = candidate.resolve()
        # ``root`` is already resolved and no part can climb, so the only way
        # the resolved path differs from the candidate is a symlink somewhere
        # in the chain. Symlinks are refused outright, even ones that would
        # land elsewhere *inside* the root (another design's files).
        if resolved != candidate or not resolved.is_relative_to(self.root):
            raise NotFound("unknown path")
        return resolved

    def _contained_children(self, parent: Path, marker: str) -> list[tuple[str, Path]]:
        """Immediate subdirectories of ``parent`` that carry ``marker`` and pass
        containment: a valid id, not a symlink, resolving to exactly
        ``parent/<name>``. Anything else is skipped, never followed."""

        if not parent.is_dir() or parent.is_symlink():
            return []
        out = []
        for entry in sorted(parent.iterdir(), key=lambda e: e.name):
            if not is_valid_id(entry.name) or entry.is_symlink() or not entry.is_dir():
                continue
            if entry.resolve() != parent / entry.name:
                continue
            if (entry / marker).is_symlink() or not (entry / marker).is_file():
                continue
            out.append((entry.name, entry))
        return out

    def _rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def _design_dir(self, design_id: str, *, must_exist: bool = True) -> Path:
        path = self.contained(design_id)
        if must_exist and not (path / "design.json").is_file():
            raise NotFound("unknown design")
        return path

    def _draft_dir(self, design_id: str, draft_id: str) -> Path:
        if not is_valid_id(draft_id):
            raise NotFound("unknown draft")
        path = self.contained(design_id, "drafts", draft_id)
        if not (path / "draft.json").is_file():
            raise NotFound("unknown draft")
        return path

    def _revision_dir(self, design_id: str, rev_id: str) -> Path:
        if not is_valid_id(rev_id):
            raise NotFound("unknown revision")
        path = self.contained(design_id, "revisions", rev_id)
        if not (path / "revision.json").is_file():
            raise NotFound("unknown revision")
        return path

    # --- designs ----------------------------------------------------------

    def create_design(
        self,
        *,
        instrument_id: str,
        backend: str,
        title: str,
        origin: Mapping[str, Any],
    ) -> dict:
        """Create ``design.json``. The origin source becomes the first draft
        through :meth:`create_draft` (parent ``None``) and the first revision
        once it is saved."""

        if not is_valid_id(instrument_id):
            raise WorkbenchError("instrument_id must match [a-z0-9][a-z0-9_-]{0,63}")
        if backend not in BACKENDS:
            raise WorkbenchError(f"backend must be one of {BACKENDS}")
        title = _check_text(title, "title") or instrument_id
        origin = validate_origin(origin)
        # Everything fallible is validated above; nothing on disk changes
        # before this line.
        self.root.mkdir(parents=True, exist_ok=True)
        for _attempt in range(8):
            design_id = self.new_design_id(instrument_id)
            design_dir = self.contained(design_id)
            try:
                design_dir.mkdir(parents=False, exist_ok=False)
            except FileExistsError:
                continue
            break
        else:  # pragma: no cover - 8 collisions of 24 random bits
            raise WorkbenchError("could not allocate a design id")
        (design_dir / "revisions").mkdir()
        (design_dir / "drafts").mkdir()
        payload = {
            "schema": DESIGN_SCHEMA,
            "design_id": design_id,
            "instrument_id": instrument_id,
            "backend": backend,
            "title": title,
            "origin": origin,
            "created_at": _now(),
        }
        atomic_write_json(design_dir / "design.json", payload)
        return payload

    @staticmethod
    def new_design_id(instrument_id: str) -> str:
        """``d-<instrument>-<6 hex>`` within the 64-character id rule. A long
        instrument id is shortened, never the random suffix, so two designs
        of the longest valid instrument still get distinct ids."""

        suffix = secrets.token_hex(3)
        budget = 64 - len("d-") - len("-") - len(suffix)
        return f"d-{instrument_id[:budget]}-{suffix}"

    def read_design(self, design_id: str) -> dict:
        design_dir = self._design_dir(design_id)
        return json.loads((design_dir / "design.json").read_text(encoding="utf-8"))

    def list_designs(self) -> list[dict]:
        rows = []
        if not self.root.is_dir():
            return rows
        for design_id, design_dir in self._contained_children(self.root, "design.json"):
            try:
                design = json.loads((design_dir / "design.json").read_text(encoding="utf-8"))
                revisions = self.list_revisions(design_id)
                curation = self.curation_state(design_id)
            except (OSError, json.JSONDecodeError, NotFound):
                continue
            rows.append(
                {
                    **design,
                    "title": curation.get("title") or design.get("title"),
                    "revision_count": len(revisions),
                    "latest_rev_id": revisions[-1]["rev_id"] if revisions else None,
                    "latest_seq": revisions[-1]["seq"] if revisions else 0,
                    "pick": curation.get("pick"),
                }
            )
        rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return rows

    def get_design(self, design_id: str) -> dict:
        design = self.read_design(design_id)
        return {
            **design,
            "revisions": self.list_revisions(design_id),
            "curation": self.curation_state(design_id),
            "drafts": self.list_drafts(design_id),
        }

    # --- drafts -----------------------------------------------------------

    def create_draft(
        self,
        design_id: str,
        *,
        parent_rev_id: Optional[str],
        source: str,
        editor: Mapping[str, Any],
        kind: str = "edit",
    ) -> dict:
        design = self.read_design(design_id)
        source = _check_source(source)
        if kind not in DRAFT_KINDS:
            raise WorkbenchError(f"draft kind must be one of {DRAFT_KINDS}")
        editor = validate_editor(editor)
        if parent_rev_id is not None:
            self._revision_dir(design_id, parent_rev_id)
        elif self.list_revisions(design_id):
            raise Conflict("this design already has revisions; name a parent_rev_id")
        # Everything fallible is validated above; nothing on disk changes
        # before this line.
        draft_id = f"j-{secrets.token_hex(6)}"
        draft_dir = self.contained(design_id, "drafts", draft_id)
        draft_dir.mkdir(parents=True, exist_ok=False)
        source_name = SOURCE_NAMES[design["backend"]]
        (draft_dir / source_name).write_text(source, encoding="utf-8")
        payload = {
            "schema": DRAFT_SCHEMA,
            "draft_id": draft_id,
            "design_id": design_id,
            "backend": design["backend"],
            "kind": kind,
            "parent_rev_id": parent_rev_id,
            "source_name": source_name,
            "source_sha256": _sha256_text(source),
            "editor": editor,
            "created_at": _now(),
            "job": {
                "status": "queued",
                "pid": None,
                "started_at": None,
                "finished_at": None,
                "exit_code": None,
                "error": None,
            },
        }
        atomic_write_json(draft_dir / "draft.json", payload)
        return payload

    def read_draft(self, design_id: str, draft_id: str) -> dict:
        draft_dir = self._draft_dir(design_id, draft_id)
        payload = json.loads((draft_dir / "draft.json").read_text(encoding="utf-8"))
        objective = draft_dir / "objective.json"
        if objective.is_file():
            try:
                payload["objective"] = json.loads(objective.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload["objective"] = None
        payload["artifacts"] = self._artifact_names(draft_dir)
        return payload

    def list_drafts(self, design_id: str) -> list[dict]:
        design_dir = self._design_dir(design_id)
        rows = []
        for _draft_id, draft_dir in self._contained_children(design_dir / "drafts", "draft.json"):
            try:
                rows.append(json.loads((draft_dir / "draft.json").read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        rows.sort(key=lambda r: r.get("created_at") or "")
        return rows

    def draft_source(self, design_id: str, draft_id: str) -> str:
        draft_dir = self._draft_dir(design_id, draft_id)
        draft = json.loads((draft_dir / "draft.json").read_text(encoding="utf-8"))
        return (draft_dir / draft["source_name"]).read_text(encoding="utf-8")

    def draft_dir(self, design_id: str, draft_id: str) -> Path:
        """Host path of a draft, for the job runner only."""

        return self._draft_dir(design_id, draft_id)

    def update_draft_source(self, design_id: str, draft_id: str, source: str, *, editor: Optional[Mapping[str, Any]] = None) -> dict:
        """Replace a draft's source (a model revision returned it) and rehash
        it, merging ``editor`` provenance fields (re-validated). Only a
        ``revise`` draft that has not finished may be rewritten (W6)."""

        source = _check_source(source)
        draft_dir = self._draft_dir(design_id, draft_id)
        path = draft_dir / "draft.json"
        with file_lock(path):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("kind") != "revise":
                raise Conflict("only a revise draft's source can be rewritten")
            if payload["job"].get("status") in FINISHED_STATUSES:
                raise Conflict("this draft has finished; its source is fixed")
            merged = dict(payload["editor"])
            merged.update(dict(editor or {}))
            payload["editor"] = validate_editor(merged)
            (draft_dir / payload["source_name"]).write_text(source, encoding="utf-8")
            payload["source_sha256"] = _sha256_text(source)
            atomic_write_json(path, payload)
        return payload

    def update_draft_job(self, design_id: str, draft_id: str, **fields: Any) -> dict:
        """Update the mutable job block of a draft (status, pid, times, error)."""

        draft_dir = self._draft_dir(design_id, draft_id)
        path = draft_dir / "draft.json"
        with file_lock(path):
            payload = json.loads(path.read_text(encoding="utf-8"))
            job = payload["job"]
            for key, value in fields.items():
                if key not in job:
                    raise WorkbenchError(f"unknown job field {key!r}")
                if key == "status" and value not in JOB_STATUSES:
                    raise WorkbenchError(f"status must be one of {JOB_STATUSES}")
                if key == "error" and value is not None:
                    value = _check_text(value, "job.error", MAX_FEEDBACK_BYTES)
                job[key] = value
            atomic_write_json(path, payload)
        return payload

    def expire_drafts(self, ttl_s: int = DRAFT_TTL_S, *, now: Optional[float] = None) -> list[str]:
        """Delete finished drafts older than ``ttl_s``. Running or queued
        drafts are never touched. Returns ``design_id/draft_id`` strings."""

        now = time.time() if now is None else now
        removed = []
        if not self.root.is_dir():
            return removed
        for _design_id, design_dir in self._contained_children(self.root, "design.json"):
            for _draft_id, draft_dir in self._contained_children(design_dir / "drafts", "draft.json"):
                try:
                    payload = json.loads((draft_dir / "draft.json").read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if payload.get("job", {}).get("status") not in FINISHED_STATUSES:
                    continue
                created = datetime.fromisoformat(payload["created_at"]).timestamp()
                if now - created < ttl_s:
                    continue
                shutil.rmtree(draft_dir, ignore_errors=True)
                removed.append(f"{payload['design_id']}/{payload['draft_id']}")
        return removed

    # --- revisions --------------------------------------------------------

    @staticmethod
    def revision_id(
        *, design_id: str, parent_rev_id: Optional[str], source_sha256: str, editor: Mapping[str, Any]
    ) -> str:
        editor = dict(editor)
        key = {
            "design_id": design_id,
            "parent_rev_id": parent_rev_id,
            "source_sha256": source_sha256,
            "editor_kind": editor.get("kind"),
            "prompt_sha256": editor.get("prompt_sha256"),
            "changed": editor.get("changed"),
        }
        return "r-" + hashlib.sha256(_canonical(key).encode("utf-8")).hexdigest()[:16]

    def save_revision(
        self,
        design_id: str,
        *,
        draft_id: str,
        note: str = "",
        allow_failed: bool = False,
    ) -> dict:
        """Promote a finished draft to an immutable revision.

        The revision directory is created exclusively and the index line is
        appended under the index lock, so two savers never interleave and an
        existing ``rev_id`` is a :class:`Conflict` that changes no bytes.
        """

        note = _check_text(note, "note")
        design = self.read_design(design_id)
        draft_dir = self._draft_dir(design_id, draft_id)
        draft = json.loads((draft_dir / "draft.json").read_text(encoding="utf-8"))
        status = draft["job"]["status"]
        if status not in FINISHED_STATUSES:
            raise Conflict(f"draft is {status}; wait for it to finish")
        if status != "succeeded" and not allow_failed:
            raise Conflict(f"draft {status}; only a succeeded draft can be saved")
        parent = draft.get("parent_rev_id")
        source_name = draft["source_name"]
        editor = validate_editor(draft["editor"])
        revisions_dir = self.contained(design_id, "revisions")
        index_path = self.contained(design_id, "index.jsonl")
        with file_lock(index_path):
            self._reconcile_unindexed(design_id, index_path)
            if parent is not None and not self._revision_dir(design_id, parent):
                raise NotFound("unknown parent revision")
            # Provenance comes from the bytes being saved, hashed under the
            # lock, never from the draft's mutable metadata. A draft whose
            # source changed after it was created (or compiled) is refused.
            source_bytes = (draft_dir / source_name).read_bytes()
            source_sha256 = hashlib.sha256(source_bytes).hexdigest()
            if source_sha256 != draft.get("source_sha256"):
                raise Conflict("draft source changed since the draft was created; compile it again")
            rev_id = self.revision_id(
                design_id=design_id,
                parent_rev_id=parent,
                source_sha256=source_sha256,
                editor=editor,
            )
            rev_dir = self.contained(design_id, "revisions", rev_id)
            if rev_dir.exists():
                raise Conflict(f"already saved as {rev_id}")
            seq = max((int(r.get("seq") or 0) for r in self.list_revisions(design_id)), default=0) + 1
            # Stage the whole revision in a contained scratch directory whose
            # name can never be a revision id (ids have no dots), then publish
            # it with one rename. Any failure before the rename removes the
            # staging directory, so a retry is not a spurious Conflict.
            staging = revisions_dir / f".staging-{rev_id}-{secrets.token_hex(4)}"
            try:
                staging.mkdir(parents=False, exist_ok=False)
                (staging / source_name).write_bytes(source_bytes)
                objective_src = draft_dir / "objective.json"
                compile_summary: dict[str, Any] = {"status": status}
                if objective_src.is_file():
                    shutil.copyfile(objective_src, staging / "objective.json")
                    try:
                        objective = json.loads(objective_src.read_text(encoding="utf-8"))
                        compile_summary["render_ok"] = bool(objective.get("render_ok"))
                        compile_summary["warnings"] = list(
                            (objective.get("artifacts") or {}).get("warnings") or []
                        )[:50]
                        compile_summary["sandbox"] = objective.get("sandbox")
                    except (OSError, json.JSONDecodeError):
                        pass
                artifacts_src = draft_dir / "artifacts"
                if artifacts_src.is_dir() and not artifacts_src.is_symlink():
                    _copy_artifact_tree(artifacts_src, staging / "artifacts")
                if (draft_dir / "job.log").is_file():
                    shutil.copyfile(draft_dir / "job.log", staging / "job.log")
                payload = {
                    "schema": REVISION_SCHEMA,
                    "rev_id": rev_id,
                    "design_id": design_id,
                    "seq": seq,
                    "parent_rev_id": parent,
                    "backend": design["backend"],
                    "source_name": source_name,
                    "source_sha256": source_sha256,
                    "editor": editor,
                    "draft_kind": draft["kind"],
                    "from_draft_id": draft_id,
                    "note": note,
                    "origin": design["origin"] if parent is None else None,
                    "compile": compile_summary,
                    "created_at": _now(),
                }
                atomic_write_json(staging / "revision.json", payload)
                # The staged copy must hash to what the provenance claims.
                if hashlib.sha256((staging / source_name).read_bytes()).hexdigest() != source_sha256:
                    raise WorkbenchError("staged source does not match its recorded hash")
                try:
                    staging.rename(rev_dir)
                except FileExistsError:
                    raise Conflict(f"already saved as {rev_id}") from None
                except OSError as exc:
                    if rev_dir.exists():
                        raise Conflict(f"already saved as {rev_id}") from None
                    raise WorkbenchError(f"could not publish revision: {exc.strerror}") from exc
            finally:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
            # Publication is the index row. If appending it fails at any point
            # (before or after the bytes hit the file), cut the index back to
            # its pre-append length first, durably, and only then unpublish the
            # directory. If the rollback itself fails the directory stays: a
            # complete row keeps pointing at a readable revision, and a torn or
            # missing row is reconciled from revision.json on the next save. An
            # index row must never name a missing revision directory.
            index_length = index_path.stat().st_size if index_path.exists() else 0
            try:
                self._append_index_row(index_path, payload)
            except BaseException:
                if self._rollback_index(index_path, index_length):
                    shutil.rmtree(rev_dir, ignore_errors=True)
                raise
        return payload

    @staticmethod
    def _rollback_index(index_path: Path, length: int) -> bool:
        """Truncate the index back to ``length`` and sync it. False when the
        rollback itself failed (the caller then keeps the revision directory)."""

        try:
            if index_path.exists():
                os.truncate(index_path, length)
                fd = os.open(index_path, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except OSError:
            return False
        return True

    @staticmethod
    def _index_row(payload: Mapping[str, Any]) -> dict:
        row = {k: payload[k] for k in ("rev_id", "seq", "parent_rev_id", "source_sha256", "created_at", "note")}
        row["editor_kind"] = payload["editor"]["kind"]
        row["compile_status"] = (payload.get("compile") or {}).get("status")
        return row

    def _append_index_row(self, index_path: Path, payload: Mapping[str, Any]) -> None:
        line = json.dumps(self._index_row(payload), sort_keys=True) + "\n"
        # A torn last line (a crash mid-append whose rollback also failed) has
        # no newline; start this row on its own line so neither is corrupted.
        torn = False
        if index_path.is_file() and index_path.stat().st_size:
            with index_path.open("rb") as tail:
                tail.seek(-1, os.SEEK_END)
                torn = tail.read(1) != b"\n"
        with index_path.open("a", encoding="utf-8") as handle:
            if torn:
                handle.write("\n")
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def _reconcile_unindexed(self, design_id: str, index_path: Path) -> list[str]:
        """Runs under the index lock. A crash between the publish rename and
        the index append leaves a complete ``revisions/<rev_id>/`` with no
        index row; its ``revision.json`` carries everything the row needs,
        so it is indexed now (before the next ``seq`` is chosen)."""

        indexed = {r.get("rev_id") for r in self.list_revisions(design_id)}
        repaired = []
        revisions_dir = self.contained(design_id, "revisions")
        for rev_id, rev_dir in self._contained_children(revisions_dir, "revision.json"):
            if rev_id in indexed:
                continue
            try:
                payload = json.loads((rev_dir / "revision.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("rev_id") != rev_id:
                continue
            self._append_index_row(index_path, payload)
            repaired.append(rev_id)
        return repaired

    def list_revisions(self, design_id: str) -> list[dict]:
        index_path = self.contained(design_id, "index.jsonl")
        if not index_path.is_file():
            return []
        rows = []
        for line in index_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        rows.sort(key=lambda r: r.get("seq", 0))
        return rows

    def read_revision(self, design_id: str, rev_id: str) -> dict:
        rev_dir = self._revision_dir(design_id, rev_id)
        payload = json.loads((rev_dir / "revision.json").read_text(encoding="utf-8"))
        objective = rev_dir / "objective.json"
        payload["objective"] = None
        if objective.is_file():
            try:
                payload["objective"] = json.loads(objective.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        payload["artifacts"] = self._artifact_names(rev_dir)
        return payload

    def revision_source(self, design_id: str, rev_id: str) -> str:
        rev_dir = self._revision_dir(design_id, rev_id)
        payload = json.loads((rev_dir / "revision.json").read_text(encoding="utf-8"))
        return (rev_dir / payload["source_name"]).read_text(encoding="utf-8")

    def revision_dir(self, design_id: str, rev_id: str) -> Path:
        """Host path of a revision, for artifact serving and export."""

        return self._revision_dir(design_id, rev_id)

    def artifact_path(self, design_id: str, kind: str, item_id: str, name: str) -> Path:
        """A contained artifact file under a draft or revision, or NotFound."""

        if kind == "revisions":
            base = self._revision_dir(design_id, item_id)
        elif kind == "drafts":
            base = self._draft_dir(design_id, item_id)
        else:
            raise NotFound("unknown path")
        if not isinstance(name, str) or not name or "/" in name or "\\" in name or name in (".", ".."):
            raise NotFound("unknown artifact")
        artifacts = base / "artifacts"
        candidate = artifacts / name
        if artifacts.is_symlink() or candidate.is_symlink():
            raise NotFound("unknown artifact")
        path = candidate.resolve()
        if path != candidate or not path.is_relative_to(artifacts) or not path.is_file():
            raise NotFound("unknown artifact")
        return path

    @staticmethod
    def _artifact_names(item_dir: Path) -> list[str]:
        artifacts = item_dir / "artifacts"
        if artifacts.is_symlink() or not artifacts.is_dir():
            return []
        return sorted(p.name for p in artifacts.iterdir() if p.is_file() and not p.is_symlink())

    def compare(self, design_id: str, a: str, b: str) -> dict:
        """Source diff plus both provenance and objective payloads."""

        ra, rb = self.read_revision(design_id, a), self.read_revision(design_id, b)
        sa, sb = self.revision_source(design_id, a), self.revision_source(design_id, b)
        return {
            "a": ra,
            "b": rb,
            "diff": source_diff(sa, sb),
            "objective_delta": _objective_delta(ra.get("objective"), rb.get("objective")),
        }

    def compare_draft(self, design_id: str, rev_id: str, draft_id: str) -> dict:
        """A saved revision (``a``) against an unsaved draft (``b``): the
        Revise tab's compare-on-success (W6). Same shape as :meth:`compare`;
        ``b`` carries ``kind: "draft"`` and the draft id so the UI serves the
        draft's artifacts."""

        ra = self.read_revision(design_id, rev_id)
        rb = self.read_draft(design_id, draft_id)
        rb["kind_of_item"] = "draft"
        sa, sb = self.revision_source(design_id, rev_id), self.draft_source(design_id, draft_id)
        return {
            "a": ra,
            "b": rb,
            "diff": source_diff(sa, sb),
            "objective_delta": _objective_delta(ra.get("objective"), rb.get("objective")),
        }

    # --- curation ---------------------------------------------------------

    def add_curation(
        self,
        design_id: str,
        *,
        rev_id: Optional[str] = None,
        pick: Optional[bool] = None,
        title: Optional[str] = None,
        note: Optional[str] = None,
        voter: str = "unknown",
    ) -> dict:
        self._design_dir(design_id)
        if rev_id is not None:
            self._revision_dir(design_id, rev_id)
        row = {
            "schema": CURATION_SCHEMA,
            "rev_id": rev_id,
            "pick": None if pick is None else bool(pick),
            "title": None if title is None else _check_text(title, "title"),
            "note": None if note is None else _check_text(note, "note"),
            "voter": _check_text(voter, "voter"),
            "created_at": _now(),
        }
        path = self.contained(design_id, "curation.jsonl")
        with file_lock(path):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        return row

    def curation_history(self, design_id: str) -> list[dict]:
        path = self.contained(design_id, "curation.jsonl")
        if not path.is_file():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def curation_state(self, design_id: str) -> dict:
        """The effective pick, title and note: the last value set for each."""

        state: dict[str, Any] = {"pick": None, "title": None, "note": None, "updated_at": None}
        for row in self.curation_history(design_id):
            if row.get("pick") is True and row.get("rev_id"):
                state["pick"] = row["rev_id"]
            elif row.get("pick") is False and row.get("rev_id") == state["pick"]:
                state["pick"] = None
            if row.get("title") is not None:
                state["title"] = row["title"]
            if row.get("note") is not None:
                state["note"] = row["note"]
            state["updated_at"] = row.get("created_at")
        return state


def _objective_delta(a: Optional[Mapping[str, Any]], b: Optional[Mapping[str, Any]]) -> dict:
    """Which gates flipped between two objective payloads, and the pass rates."""

    def gates(payload: Optional[Mapping[str, Any]]) -> dict[str, Optional[bool]]:
        if not payload:
            return {}
        objective = payload.get("objective") or {}
        checks = objective.get("checks") or objective.get("gates") or {}
        out: dict[str, Optional[bool]] = {}
        if isinstance(checks, Mapping):
            for name, value in checks.items():
                passed = value.get("passed") if isinstance(value, Mapping) else value
                out[str(name)] = bool(passed) if passed is not None else None
        return out

    def rate(payload: Optional[Mapping[str, Any]]) -> Optional[float]:
        if not payload:
            return None
        objective = payload.get("objective") or {}
        value = objective.get("pass_rate")
        return float(value) if isinstance(value, (int, float)) else None

    ga, gb = gates(a), gates(b)
    flipped = {name: [ga.get(name), gb.get(name)] for name in sorted(set(ga) | set(gb)) if ga.get(name) != gb.get(name)}
    return {"pass_rate": [rate(a), rate(b)], "flipped": flipped}
