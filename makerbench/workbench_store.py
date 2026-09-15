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

**Revisions are immutable.** A revision directory is created with
``mkdir(exist_ok=False)``; an existing ``rev_id`` is a :class:`Conflict` and
no byte changes. Revision ids are content-addressed
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
        if confinement not in ("verified", "unconfined", "restricted-tools"):
            raise WorkbenchError("editor.confinement must be verified, unconfined or restricted-tools")
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
        if not resolved.is_relative_to(self.root):
            raise NotFound("unknown path")
        return resolved

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
        design_id = f"d-{instrument_id}-{secrets.token_hex(3)}"[:64]
        design_dir = self.contained(design_id)
        design_dir.mkdir(parents=True, exist_ok=False)
        (design_dir / "revisions").mkdir()
        (design_dir / "drafts").mkdir()
        payload = {
            "schema": DESIGN_SCHEMA,
            "design_id": design_id,
            "instrument_id": instrument_id,
            "backend": backend,
            "title": title,
            "origin": validate_origin(origin),
            "created_at": _now(),
        }
        atomic_write_json(design_dir / "design.json", payload)
        return payload

    def read_design(self, design_id: str) -> dict:
        design_dir = self._design_dir(design_id)
        return json.loads((design_dir / "design.json").read_text(encoding="utf-8"))

    def list_designs(self) -> list[dict]:
        rows = []
        if not self.root.is_dir():
            return rows
        for design_json in sorted(self.root.glob("*/design.json")):
            design_id = design_json.parent.name
            if not is_valid_id(design_id):
                continue
            try:
                # A symlinked design dir resolves outside the root: skip it.
                self.contained(design_id, "design.json")
                design = json.loads(design_json.read_text(encoding="utf-8"))
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
        if parent_rev_id is not None:
            self._revision_dir(design_id, parent_rev_id)
        elif self.list_revisions(design_id):
            raise Conflict("this design already has revisions; name a parent_rev_id")
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
            "editor": validate_editor(editor),
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
        for draft_json in sorted((design_dir / "drafts").glob("*/draft.json")):
            try:
                rows.append(json.loads(draft_json.read_text(encoding="utf-8")))
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
        for draft_json in self.root.glob("*/drafts/*/draft.json"):
            try:
                payload = json.loads(draft_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("job", {}).get("status") not in FINISHED_STATUSES:
                continue
            created = datetime.fromisoformat(payload["created_at"]).timestamp()
            if now - created < ttl_s:
                continue
            draft_dir = draft_json.parent
            if not draft_dir.resolve().is_relative_to(self.root):
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
        rev_id = self.revision_id(
            design_id=design_id,
            parent_rev_id=parent,
            source_sha256=draft["source_sha256"],
            editor=draft["editor"],
        )
        rev_dir = self.contained(design_id, "revisions", rev_id)
        index_path = self.contained(design_id, "index.jsonl")
        with file_lock(index_path):
            if parent is not None and not self._revision_dir(design_id, parent):
                raise NotFound("unknown parent revision")
            try:
                rev_dir.mkdir(parents=False, exist_ok=False)
            except FileExistsError:
                raise Conflict(f"already saved as {rev_id}") from None
            seq = len(self.list_revisions(design_id)) + 1
            source_name = draft["source_name"]
            shutil.copyfile(draft_dir / source_name, rev_dir / source_name)
            objective_src = draft_dir / "objective.json"
            compile_summary: dict[str, Any] = {"status": status}
            if objective_src.is_file():
                shutil.copyfile(objective_src, rev_dir / "objective.json")
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
            if artifacts_src.is_dir():
                shutil.copytree(artifacts_src, rev_dir / "artifacts")
            if (draft_dir / "job.log").is_file():
                shutil.copyfile(draft_dir / "job.log", rev_dir / "job.log")
            payload = {
                "schema": REVISION_SCHEMA,
                "rev_id": rev_id,
                "design_id": design_id,
                "seq": seq,
                "parent_rev_id": parent,
                "backend": design["backend"],
                "source_name": source_name,
                "source_sha256": draft["source_sha256"],
                "editor": draft["editor"],
                "draft_kind": draft["kind"],
                "from_draft_id": draft_id,
                "note": note,
                "origin": design["origin"] if parent is None else None,
                "compile": compile_summary,
                "created_at": _now(),
            }
            atomic_write_json(rev_dir / "revision.json", payload)
            index_row = {
                k: payload[k]
                for k in ("rev_id", "seq", "parent_rev_id", "source_sha256", "created_at", "note")
            }
            index_row["editor_kind"] = payload["editor"]["kind"]
            index_row["compile_status"] = compile_summary["status"]
            with index_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(index_row, sort_keys=True) + "\n")
        return payload

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
        path = (base / "artifacts" / name).resolve()
        if not path.is_relative_to(base / "artifacts") or not path.is_file():
            raise NotFound("unknown artifact")
        return path

    @staticmethod
    def _artifact_names(item_dir: Path) -> list[str]:
        artifacts = item_dir / "artifacts"
        if not artifacts.is_dir():
            return []
        return sorted(p.name for p in artifacts.iterdir() if p.is_file())

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
