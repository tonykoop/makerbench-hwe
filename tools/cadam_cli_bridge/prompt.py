"""Turn an Anthropic Messages request into one CLI prompt, and a CLI answer back into content blocks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence


class RequestError(ValueError):
    """The request is not something the bridge can serve (HTTP 400, ``invalid_request_error``)."""


class EnvelopeError(ValueError):
    """The CLI's answer was not the JSON envelope the request required."""


@dataclass
class Image:
    media_type: str
    data_b64: str


@dataclass
class Prompt:
    system: str
    transcript: str
    images: list[Image] = field(default_factory=list)
    tools_required: bool = False


def _blocks(content: Any) -> list[Mapping[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, Mapping)]
    raise RequestError("message content must be a string or a list of blocks")


def system_text(system: Any) -> str:
    if system is None:
        return ""
    return "\n\n".join(str(b.get("text", "")) for b in _blocks(system) if b.get("type") == "text")


def _tool_result_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "\n".join(str(b.get("text", "")) for b in _blocks(content) if b.get("type") == "text")


def render_transcript(messages: Sequence[Mapping[str, Any]], images: list[Image]) -> str:
    """The conversation as plain text; image blocks are collected into ``images`` and marked inline."""
    if not messages:
        raise RequestError("messages must not be empty")
    out: list[str] = []
    names: dict[str, str] = {}
    for message in messages:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            raise RequestError("message role must be user or assistant")
        parts: list[str] = []
        for block in _blocks(message.get("content")):
            kind = block.get("type")
            if kind == "text":
                parts.append(str(block.get("text", "")))
            elif kind == "image":
                source = block.get("source")
                if not isinstance(source, Mapping) or source.get("type") != "base64":
                    raise RequestError("only base64 image sources are supported")
                images.append(Image(str(source.get("media_type", "image/png")), str(source.get("data", ""))))
                parts.append(f"[image {len(images)} attached]")
            elif kind == "tool_use":
                names[str(block.get("id"))] = str(block.get("name"))
                parts.append(f"[tool call {block.get('name')} id={block.get('id')} input={json.dumps(block.get('input', {}))}]")
            elif kind == "tool_result":
                name = names.get(str(block.get("tool_use_id")), "tool")
                flag = " (error)" if block.get("is_error") else ""
                parts.append(f"[tool result for {name} id={block.get('tool_use_id')}{flag}]\n{_tool_result_text(block.get('content'))}")
            elif kind in {"thinking", "redacted_thinking"}:
                continue
            else:
                raise RequestError(f"unsupported content block type: {kind}")
        out.append(f"## {role}\n" + "\n".join(parts))
    return "\n\n".join(out)


def tool_instructions(tools: Sequence[Mapping[str, Any]], tool_choice: Optional[Mapping[str, Any]]) -> str:
    listing = [
        {"name": t.get("name"), "description": t.get("description", ""), "input_schema": t.get("input_schema", {})}
        for t in tools
    ]
    kind = (tool_choice or {}).get("type", "auto")
    if kind == "tool":
        rule = f'You MUST call the tool "{tool_choice.get("name")}" exactly once.'
    elif kind == "any":
        rule = "You MUST call at least one tool."
    else:
        rule = "Call a tool when it is the right way to answer; otherwise answer in text."
    return (
        "\n\n# Tools\nYou cannot run tools yourself. The caller will run them. These tools are available:\n"
        f"{json.dumps(listing, indent=1)}\n\n{rule}\n"
        "Reply with ONE JSON object and nothing else (no code fences, no commentary), in exactly this shape:\n"
        '{"text": "<optional prose, may be empty>", "tool_calls": [{"name": "<tool name>", "input": {<arguments matching the tool input_schema>}}]}\n'
        'Use an empty "tool_calls" list only when no tool is needed.'
    )


def build_prompt(body: Mapping[str, Any]) -> Prompt:
    if not isinstance(body.get("messages"), list):
        raise RequestError("messages is required")
    images: list[Image] = []
    transcript = render_transcript(body["messages"], images)
    tools = body.get("tools") or []
    tool_choice = body.get("tool_choice")
    use_tools = bool(tools) and (tool_choice or {}).get("type") != "none"
    if use_tools:
        if not all(isinstance(t, Mapping) and t.get("name") for t in tools):
            raise RequestError("every tool needs a name")
        if (tool_choice or {}).get("type") == "tool" and (tool_choice or {}).get("name") not in {t["name"] for t in tools}:
            raise RequestError("tool_choice names a tool that was not provided")
        transcript += tool_instructions(tools, tool_choice)
    transcript += "\n\n## assistant\n" if not use_tools else "\n"
    return Prompt(system=system_text(body.get("system")), transcript=transcript, images=images, tools_required=use_tools)


_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def extract_json(raw: str) -> Any:
    """The first complete JSON object in ``raw`` (tolerating fences and surrounding prose)."""
    text = _FENCE.sub("", raw.strip())
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            value, _ = decoder.raw_decode(text[start:])
            return value
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
    raise EnvelopeError("no JSON object in the answer")


def parse_envelope(raw: str, tools: Sequence[Mapping[str, Any]], tool_choice: Optional[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate the CLI's envelope into Anthropic content blocks (text first, then tool_use)."""
    data = extract_json(raw)
    if not isinstance(data, Mapping):
        raise EnvelopeError("the envelope must be a JSON object")
    text = data.get("text") or ""
    calls = data.get("tool_calls") or []
    if not isinstance(text, str) or not isinstance(calls, list):
        raise EnvelopeError("text must be a string and tool_calls a list")
    allowed = {t["name"] for t in tools}
    blocks: list[dict[str, Any]] = []
    if text.strip():
        blocks.append({"type": "text", "text": text})
    for index, call in enumerate(calls):
        if not isinstance(call, Mapping) or call.get("name") not in allowed:
            raise EnvelopeError(f"tool call {index} names an unknown tool")
        arguments = call.get("input", {})
        if not isinstance(arguments, Mapping):
            raise EnvelopeError(f"tool call {index} input must be an object")
        blocks.append({"type": "tool_use", "id": f"toolu_bridge_{index:02d}", "name": call["name"], "input": dict(arguments)})
    kind = (tool_choice or {}).get("type", "auto")
    used = [b["name"] for b in blocks if b["type"] == "tool_use"]
    if kind == "any" and not used:
        raise EnvelopeError("a tool call is required")
    if kind == "tool" and used != [tool_choice.get("name")]:
        raise EnvelopeError(f'exactly one call to "{tool_choice.get("name")}" is required')
    if not blocks:
        blocks.append({"type": "text", "text": ""})
    return blocks
