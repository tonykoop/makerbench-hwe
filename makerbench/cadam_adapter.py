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
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional


SCHEMA = "makerbench-cadam-headless-v1"
USD_PER_CADAM_BILLING_TOKEN = 0.01
HttpTransport = Callable[[str, str, Mapping[str, str], Optional[bytes]], bytes]


class CadamStreamFailedError(RuntimeError):
    """A complete stream explicitly failed before producing any output."""


def stream_failure(stream: bytes) -> Optional[str]:
    errors = []
    done = False
    for line in stream.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        if not line.startswith("data: ") or done:
            return None
        data = line[6:]
        if data == "[DONE]":
            done = True
            continue
        try:
            event = json.loads(data)
        except ValueError:
            return None
        if not isinstance(event, dict):
            return None
        if event.get("type") == "error" and isinstance(event.get("errorText"), str):
            errors.append(event["errorText"])
        elif event.get("type") != "start":
            return None  # Any output/tool event keeps the dispatch uncertain.
    return "\n".join(errors) if done and errors else None


class CadamRecoveryRequiredError(RuntimeError):
    """A persisted paid dispatch is unresolved and must never be auto-reposted."""


class CadamAuthError(RuntimeError):
    """Sign-in to the self-hosted CADAM Supabase failed. Never carries a token or password."""


class CadamTransportError(RuntimeError):
    """A CADAM/Supabase call failed. Fixed text only: never a header, token, key or response body."""


@dataclass(frozen=True)
class CadamConfig:
    """Secrets are ``repr=False`` so a stray ``print(config)`` or traceback cannot leak them."""

    base_url: str
    supabase_url: str
    user_id: str
    service_role_key: str = field(repr=False)
    access_token: str = field(default="", repr=False)
    model: str = "anthropic/claude-fable-5"
    timeout_s: int = 900
    max_image_bytes: int = 4_000_000
    max_image_dimension: int = 1400
    bot_email: str = field(default="", repr=False)
    bot_password: str = field(default="", repr=False)
    anon_key: str = field(default="", repr=False)

    @property
    def has_bot_credentials(self) -> bool:
        return bool(self.bot_email.strip() and self.bot_password.strip())

    @property
    def has_partial_bot_credentials(self) -> bool:
        return bool(self.bot_email.strip() or self.bot_password.strip()) and not self.has_bot_credentials

    def validate(self) -> None:
        for name in ("base_url", "supabase_url", "user_id", "service_role_key", "model"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"CADAM {name} is required")
        if self.has_partial_bot_credentials:  # never fall back to the static token behind a half-set bot login
            raise ValueError("CADAM bot_email and bot_password must be set together")
        if self.has_bot_credentials:
            if not self.anon_key.strip():
                raise ValueError("CADAM anon_key is required with bot_email/bot_password")
        elif not self.access_token.strip():
            raise ValueError("CADAM access_token (or bot_email/bot_password) is required")


def sign_in(
    supabase_url: str,
    anon_key: str,
    email: str,
    password: str,
    transport: "HttpTransport",
) -> str:
    """Password grant against Supabase auth; returns the access token.

    Every failure is re-raised as ``CadamAuthError`` with a fixed message (HTTP status at most):
    the response body, the request body and the token never reach an exception or a log.
    """

    url = f"{supabase_url.rstrip('/')}/auth/v1/token?grant_type=password"
    headers = {"apikey": anon_key, "Content-Type": "application/json"}
    token: object = None
    failure = ""
    try:
        raw = transport("POST", url, headers, _json_body({"email": email, "password": password}))
        token = json.loads(raw.decode("utf-8")).get("access_token")
    except urllib.error.HTTPError as exc:
        failure = f"CADAM sign-in rejected (HTTP {exc.code})"
    except (OSError, ValueError, AttributeError):
        failure = "CADAM sign-in failed (auth endpoint unreachable or malformed reply)"
    # raised outside the ``except`` so the original error (whose text could echo a credential)
    # is not chained as ``__context__``
    if failure:
        raise CadamAuthError(failure)
    if not isinstance(token, str) or not token:
        raise CadamAuthError("CADAM sign-in reply carried no access token")
    return token


@dataclass(frozen=True)
class CadamResult:
    conversation_id: str
    scad_path: Path
    stream_path: Path
    prepared_image_path: Optional[Path]
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

    def _send(self, method: str, url: str, headers: Mapping[str, str], body: Optional[bytes]) -> bytes:
        """``transport`` with failures reduced to fixed text (headers may carry a live bearer token
        or service key, and servers/proxies echo them in reasons). Raised outside the ``except`` so
        the original error is not chained as ``__context__``."""

        failure = ""
        try:
            return self.transport(method, url, headers, body)
        except urllib.error.HTTPError as exc:
            failure = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - any transport failure is redacted the same way
            failure = type(exc).__name__
        path = urllib.parse.urlsplit(url).path
        raise CadamTransportError(f"CADAM {method} {path} failed ({failure})")

    def _access_token(self) -> str:
        """Fresh bot sign-in when bot credentials are set; the static token only otherwise."""

        config = self.config
        if config.has_bot_credentials:
            return sign_in(
                config.supabase_url, config.anon_key, config.bot_email, config.bot_password, self.transport
            )
        return config.access_token

    def _rest_insert(self, table: str, payload: object) -> None:
        headers = {
            **self._service_headers,
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        self._send(
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
        self._send(
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
        raw = self._send(
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
        reference_image: Optional[Path] = None,
        output_dir: Path,
        conversation_id: Optional[str] = None,
        resume_only: bool = False,
    ) -> CadamResult:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        image_path: Optional[Path] = None
        image_sha = ""
        if reference_image is not None:
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
            access_token = self._access_token()  # sign in before any row is written or paid call made
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
            parts: list[dict] = [{"type": "text", "text": prompt}]
            if image_path is not None:
                self._upload_image(conversation_id, image_id, image_path)
                parts.append(
                    {
                        "type": "file",
                        "mediaType": "image/jpeg",
                        "filename": f"{image_id}.jpg",
                        "url": (
                            "/storage/v1/object/public/images/"
                            f"{self.config.user_id}/{conversation_id}/{image_id}"
                        ),
                    }
                )
            self._rest_insert(
                "messages",
                {
                    "id": message_id,
                    "conversation_id": conversation_id,
                    "role": "user",
                    "parts": parts,
                    "metadata": {"model": self.config.model},
                    "parent_message_id": None,
                },
            )
            stream = self._send(
                "POST",
                f"{self.config.base_url.rstrip('/')}/api/parametric-chat",
                {
                    "Authorization": f"Bearer {access_token}",
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
            failure = stream_failure(stream_path.read_bytes()) if stream_path.exists() else None
            if failure:
                raise CadamStreamFailedError(failure)
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
