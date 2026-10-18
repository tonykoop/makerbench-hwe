"""The two CLI runtimes behind the bridge. Both run in an empty scratch directory with no tools."""

from __future__ import annotations

import base64
import json
import logging
import os
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .prompt import Image

log = logging.getLogger("cadam_cli_bridge")

MAX_ARG_BYTES = 100_000  # a single argv string must stay well under the kernel's 128 KiB limit


class BackendError(RuntimeError):
    """The CLI failed (non-zero exit, error result, unreadable output)."""


class BackendTimeout(BackendError):
    """The CLI did not finish in time and was killed."""


@dataclass
class CliResult:
    text: str
    input_tokens: int
    output_tokens: int


def run_cli(argv: Sequence[str], *, stdin: Optional[bytes], timeout: float, cwd: Path, env: Optional[dict] = None) -> tuple[str, str]:
    """Run ``argv`` in its own process group; kill the whole group on timeout. Returns (stdout, stderr)."""
    try:
        proc = subprocess.Popen(
            list(argv), stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=env, start_new_session=True,
        )
    except OSError as exc:
        raise BackendError(f"cannot start {Path(argv[0]).name}: {exc.strerror or exc}") from None
    try:
        out, err = proc.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise BackendTimeout(f"{Path(argv[0]).name} did not finish within {timeout:g}s") from None
    if proc.returncode != 0:
        # stderr can carry credentials or prompt fragments: only its size is kept, never its text
        log.warning("%s exited %s (%d bytes of stderr withheld)", Path(argv[0]).name, proc.returncode, len(err))
        raise BackendError(f"{Path(argv[0]).name} exited with status {proc.returncode}")
    return out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


class ClaudeBackend:
    """``claude -p`` with built-in tools, MCP servers, settings and slash commands all switched off."""

    name = "claude"

    def __init__(self, binary: str = "claude") -> None:
        self.binary = binary

    def argv(self, model: str, system: str) -> list[str]:
        return [
            self.binary, "-p", "--output-format", "stream-json", "--input-format", "stream-json", "--verbose",
            "--tools=", "--no-session-persistence", "--model", model, "--system-prompt", system,
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "", "--disable-slash-commands",
        ]

    def run(self, *, model: str, system: str, transcript: str, images: Sequence[Image], timeout: float) -> CliResult:
        if len(system.encode("utf-8")) > MAX_ARG_BYTES:  # too big for argv: send it with the conversation
            transcript, system = f"# System instructions\n{system}\n\n{transcript}", "Follow the instructions in the conversation."
        content: list[dict] = [{"type": "text", "text": transcript}]
        content += [
            {"type": "image", "source": {"type": "base64", "media_type": img.media_type, "data": img.data_b64}}
            for img in images
        ]
        line = json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"
        with tempfile.TemporaryDirectory(prefix="cadam-bridge-") as scratch:
            out, _ = run_cli(self.argv(model, system), stdin=line.encode("utf-8"), timeout=timeout, cwd=Path(scratch))
        return self.parse(out)

    @staticmethod
    def parse(stdout: str) -> CliResult:
        assistant_text: list[str] = []
        result: Optional[dict] = None
        for raw in stdout.splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "assistant":
                for block in (event.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        assistant_text.append(str(block.get("text", "")))
            elif event.get("type") == "result":
                result = event
        if result is None:
            raise BackendError("claude produced no result event")
        if result.get("is_error"):
            raise BackendError(f"claude reported an error ({result.get('subtype') or 'unknown'})")
        usage = result.get("usage") or {}
        tokens_in = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        text = result.get("result")
        return CliResult(text if isinstance(text, str) else "".join(assistant_text), tokens_in, int(usage.get("output_tokens") or 0))


class CodexBackend:
    """``codex exec`` in a read-only sandbox, scratch cwd, stdin closed (``</dev/null``)."""

    name = "codex"

    def __init__(self, binary: str = "codex") -> None:
        self.binary = binary

    def run(self, *, model: str, system: str, transcript: str, images: Sequence[Image], timeout: float) -> CliResult:
        prompt = f"# System instructions\n{system}\n\n{transcript}" if system else transcript
        with tempfile.TemporaryDirectory(prefix="cadam-bridge-") as scratch:
            scratch_dir = Path(scratch)
            if len(prompt.encode("utf-8")) > MAX_ARG_BYTES:
                (scratch_dir / "PROMPT.md").write_text(prompt, encoding="utf-8")
                prompt = "Read PROMPT.md in the current directory and do exactly what it says. Reply with the final answer only."
            argv = [self.binary, "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral",
                    "-C", scratch, "-m", model, "-o", str(scratch_dir / "answer.txt")]
            for index, img in enumerate(images):
                suffix = ".jpg" if "jpeg" in img.media_type else ".png"
                path = scratch_dir / f"image{index}{suffix}"
                path.write_bytes(base64.b64decode(img.data_b64))
                argv += ["-i", str(path)]
            argv += ["--", prompt]
            run_cli(argv, stdin=None, timeout=timeout, cwd=scratch_dir)
            answer = scratch_dir / "answer.txt"
            if not answer.is_file():
                raise BackendError("codex wrote no final message")
            text = answer.read_text(encoding="utf-8", errors="replace")
        return CliResult(text, max(1, len(prompt) // 4), max(1, len(text) // 4))  # codex reports no usage: estimated
