"""Headless CADAM/Fable adapter for image-conditioned arena entrants.

CADAM's parametric chat route is browser-streamed, but the authoritative model
output is persisted to local Supabase as a structured
``tool-build_parametric_model`` part.  This adapter creates the same local
conversation/message records as the browser, keeps the HTTP stream open until
completion, then reads the structured tool input from Supabase.  It never
scrapes the DOM.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional


SCHEMA = "makerbench-cadam-headless-v1"
USD_PER_CADAM_BILLING_TOKEN = 0.01
HttpTransport = Callable[[str, str, Mapping[str, str], Optional[bytes]], bytes]


class CadamRecoveryRequiredError(RuntimeError):
    """A persisted paid dispatch is unresolved and must never be auto-reposted."""


@dataclass(frozen=True)
class CadamConfig:
    base_url: str
    supabase_url: str
    user_id: str
    access_token: str
    service_role_key: str
    model: str = "anthropic/claude-fable-5"
    timeout_s: int = 900
    max_image_bytes: int = 4_000_000
    max_image_dimension: int = 1400

    def validate(self) -> None:
        for name in (
            "base_url",
            "supabase_url",
            "user_id",
            "access_token",
            "service_role_key",
            "model",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"CADAM {name} is required")


@dataclass(frozen=True)
class CadamResult:
    conversation_id: str
    scad_path: Path
    stream_path: Path
    prepared_image_path: Path
    image_sha256: str
    cost_usd: float
    model: str


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: Optional[bytes],
) -> bytes:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    with urllib.request.urlopen(request, timeout=900) as response:
        return response.read()


def _json_body(payload: object) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def prepare_reference_image(
    source: Path,
    destination: Path,
    *,
    max_dimension: int = 1400,
    max_bytes: int = 4_000_000,
) -> tuple[Path, str]:
    """Normalize a reference image to a provider-safe JPEG."""

    from PIL import Image

    source = Path(source)
    if not source.is_file():
        raise ValueError(f"reference image does not exist: {source}")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as opened:
        image = opened.convert("RGB")
        image.thumbnail((max_dimension, max_dimension))
        quality = 90
        while True:
            image.save(destination, format="JPEG", quality=quality, optimize=True)
            if destination.stat().st_size <= max_bytes or quality <= 55:
                break
            quality -= 5
    if destination.stat().st_size > max_bytes:
        raise ValueError(
            f"prepared CADAM image exceeds {max_bytes} bytes: {destination.stat().st_size}"
        )
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return destination, digest


def _extract_build_code(value: object) -> Optional[str]:
    """Find the longest structured build-parametric-model code payload."""

    found: list[str] = []

    def walk(item: object, in_build: bool = False) -> None:
        if isinstance(item, Mapping):
            marker = str(item.get("type") or item.get("toolName") or "")
            build = in_build or "build_parametric_model" in marker
            if build:
                for container_name in ("input", "args", "result", "output"):
                    container = item.get(container_name)
                    if isinstance(container, Mapping):
                        code = container.get("code")
                        if isinstance(code, str) and code.strip():
                            found.append(code)
                code = item.get("code")
                if isinstance(code, str) and code.strip():
                    found.append(code)
            for child in item.values():
                walk(child, build)
        elif isinstance(item, list):
            for child in item:
                walk(child, in_build)

    walk(value)
    return max(found, key=len) if found else None


class CadamClient:
    def __init__(
        self,
        config: CadamConfig,
        *,
        transport: HttpTransport = _default_transport,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        config.validate()
        self.config = config
        self.transport = transport
        self.sleep_fn = sleep_fn

    @property
    def _service_headers(self) -> dict[str, str]:
        return {
            "apikey": self.config.service_role_key,
            "Authorization": f"Bearer {self.config.service_role_key}",
        }

    def _rest_insert(self, table: str, payload: object) -> None:
        headers = {
            **self._service_headers,
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        self.transport(
            "POST",
            f"{self.config.supabase_url.rstrip('/')}/rest/v1/{table}",
            headers,
            _json_body(payload),
        )

    def _upload_image(self, conversation_id: str, image_id: str, image_path: Path) -> None:
        storage_path = f"{self.config.user_id}/{conversation_id}/{image_id}"
        headers = {
            **self._service_headers,
            "Content-Type": "image/jpeg",
            "x-upsert": "false",
        }
        quoted = urllib.parse.quote(storage_path, safe="/")
        self.transport(
            "POST",
            f"{self.config.supabase_url.rstrip('/')}/storage/v1/object/images/{quoted}",
            headers,
            image_path.read_bytes(),
        )

    def _assistant_rows(self, conversation_id: str) -> list[dict]:
        query = urllib.parse.urlencode(
            {
                "conversation_id": f"eq.{conversation_id}",
                "role": "eq.assistant",
                "select": "id,parts,metadata,created_at",
                "order": "created_at.desc",
                "limit": "8",
            }
        )
        raw = self.transport(
            "GET",
            f"{self.config.supabase_url.rstrip('/')}/rest/v1/messages?{query}",
            self._service_headers,
            None,
        )
        payload = json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload, list) else []

    def generate(
        self,
        *,
        prompt: str,
        reference_image: Path,
        output_dir: Path,
        conversation_id: Optional[str] = None,
        resume_only: bool = False,
    ) -> CadamResult:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        image_path, image_sha = prepare_reference_image(
            reference_image,
            output_dir / "reference-image.jpg",
            max_dimension=self.config.max_image_dimension,
            max_bytes=self.config.max_image_bytes,
        )
        conversation_id = conversation_id or str(uuid.uuid4())
        message_id = str(uuid.uuid5(uuid.UUID(conversation_id), "user-message"))
        image_id = str(uuid.uuid5(uuid.UUID(conversation_id), "reference-image"))
        stream_path = output_dir / "cadam-stream.bin"

        rows = self._assistant_rows(conversation_id)
        code = _extract_build_code(rows)
        if not code and not resume_only:
            self._rest_insert(
                "conversations",
                {
                    "id": conversation_id,
                    "user_id": self.config.user_id,
                    "title": "MakerBench nightly arena",
                    "type": "parametric",
                    "settings": {"model": self.config.model},
                },
            )
            self._upload_image(conversation_id, image_id, image_path)
            self._rest_insert(
                "messages",
                {
                    "id": message_id,
                    "conversation_id": conversation_id,
                    "role": "user",
                    "parts": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "file",
                            "mediaType": "image/jpeg",
                            "filename": f"{image_id}.jpg",
                            "url": (
                                "/storage/v1/object/public/images/"
                                f"{self.config.user_id}/{conversation_id}/{image_id}"
                            ),
                        },
                    ],
                    "metadata": {"model": self.config.model},
                    "parent_message_id": None,
                },
            )
            stream = self.transport(
                "POST",
                f"{self.config.base_url.rstrip('/')}/api/parametric-chat",
                {
                    "Authorization": f"Bearer {self.config.access_token}",
                    "Content-Type": "application/json",
                },
                _json_body(
                    {
                        "conversationId": conversation_id,
                        "model": self.config.model,
                        "thinking": True,
                    }
                ),
            )
            stream_path.write_bytes(stream)
        elif code and not stream_path.exists():
            stream_path.write_bytes(b"recovered from persisted assistant row\n")

        attempts = 60 if resume_only else 8
        for attempt in range(attempts):
            if code:
                break
            rows = self._assistant_rows(conversation_id)
            code = _extract_build_code(rows)
            if attempt < attempts - 1:
                self.sleep_fn(0.5)
        if not code:
            raise CadamRecoveryRequiredError(
                "paid CADAM dispatch has no persisted structured build_parametric_model result; "
                "refusing automatic repost "
                f"(conversation_id={conversation_id})"
            )

        scad_path = output_dir / "candidate.scad"
        scad_path.write_text(code.rstrip() + "\n", encoding="utf-8")
        billing_tokens = 0
        for row in rows:
            metadata = row.get("metadata") if isinstance(row, Mapping) else None
            if isinstance(metadata, Mapping):
                value = metadata.get("billingTokens")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    billing_tokens = max(billing_tokens, int(value))
        return CadamResult(
            conversation_id=conversation_id,
            scad_path=scad_path,
            stream_path=stream_path,
            prepared_image_path=image_path,
            image_sha256=image_sha,
            cost_usd=round(billing_tokens * USD_PER_CADAM_BILLING_TOKEN, 4),
            model=self.config.model,
        )
