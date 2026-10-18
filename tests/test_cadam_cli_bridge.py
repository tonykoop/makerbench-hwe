"""#1100: the CADAM CLI bridge, end to end over real HTTP against fake `claude` / `codex` CLIs."""

import base64
import http.client
import json
import logging
import stat
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.cadam_cli_bridge import BridgeConfig, ModelRoute, load_config, make_server  # noqa: E402
from tools.cadam_cli_bridge.prompt import extract_json, parse_envelope, EnvelopeError  # noqa: E402

KEY = "bridge-test-key-0123456789"
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
TOOLS = [{"name": "build_parametric_model", "description": "Build the model", "input_schema": {
    "type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "apply_parameter_changes", "description": "Tweak", "input_schema": {"type": "object"}}]

FAKE_CLAUDE = '''#!{py}
import json, sys, time
from pathlib import Path
here = Path(__file__).parent
log = here / "calls.jsonl"
stdin = sys.stdin.read()
with log.open("a") as f:
    f.write(json.dumps({{"cli": "claude", "argv": sys.argv[1:], "stdin": stdin}}) + "\\n")
plan = json.loads((here / "plan.json").read_text())
n = sum(1 for _ in log.open())  # 1-based call number
answer = plan[min(n, len(plan)) - 1]
if answer == "SLEEP":
    time.sleep(30)
if answer == "FAIL":
    sys.stderr.write("claude blew up\\n"); sys.exit(3)
print(json.dumps({{"type": "system", "subtype": "init", "tools": []}}))
print(json.dumps({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": answer}}]}}}}))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": answer,
                  "usage": {{"input_tokens": 11, "cache_read_input_tokens": 100, "output_tokens": 7}}}}))
'''
FAKE_CODEX = '''#!{py}
import json, sys
from pathlib import Path
here = Path(__file__).parent
log = here / "calls.jsonl"
argv = sys.argv[1:]
with log.open("a") as f:
    f.write(json.dumps({{"cli": "codex", "argv": argv, "stdin_isatty": sys.stdin.isatty(), "stdin": sys.stdin.read()}}) + "\\n")
plan = json.loads((here / "plan.json").read_text())
n = sum(1 for _ in log.open())
Path(argv[argv.index("-o") + 1]).write_text(plan[min(n, len(plan)) - 1])
'''


def _install(tmp_path, name, template, plan):
    folder = tmp_path / name
    folder.mkdir()
    exe = folder / name
    exe.write_text(template.format(py=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    (folder / "plan.json").write_text(json.dumps(plan))
    return exe


def _calls(tmp_path, name):
    path = tmp_path / name / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class Bridge:
    def __init__(self, tmp_path, claude_plan=("hello",), codex_plan=("hi",), timeout_s=20):
        self.tmp = tmp_path
        claude = _install(tmp_path, "claude", FAKE_CLAUDE, list(claude_plan))
        codex = _install(tmp_path, "codex", FAKE_CODEX, list(codex_plan))
        config = BridgeConfig(
            models={"anthropic/claude-sonnet-5.5": ModelRoute("claude", "claude-sonnet-5-5"),
                    "openai/gpt-6.1-sol": ModelRoute("codex", "gpt-6.1-sol")},
            port=0, timeout_s=timeout_s, claude_bin=str(claude), codex_bin=str(codex), api_key=KEY)
        self.server = make_server(config)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def post(self, body, key=KEY, header="x-api-key", path="/v1/messages"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Content-Type": "application/json"}
        if key is not None:
            headers[header] = key if header == "x-api-key" else f"Bearer {key}"
        conn.request("POST", path, json.dumps(body), headers)
        response = conn.getresponse()
        return response.status, response.read().decode(), response.getheader("Content-Type")

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def make(tmp_path):
    made = []

    def build(**kwargs):
        bridge = Bridge(tmp_path, **kwargs)
        made.append(bridge)
        return bridge

    yield build
    for bridge in made:
        bridge.close()


def _body(**extra):
    return {"model": "anthropic/claude-sonnet-5.5", "max_tokens": 1024, "system": "You are a CAD assistant.",
            "messages": [{"role": "user", "content": "make a whistle"}], **extra}


def _events(raw):
    out = []
    for chunk in raw.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


# ---- text -------------------------------------------------------------------------------


def test_plain_text_round_trip_and_claude_is_run_with_no_tools_and_the_request_system_prompt(make, tmp_path):
    bridge = make(claude_plan=["a simple whistle"])
    status, raw, ctype = bridge.post(_body())
    assert status == 200 and ctype == "application/json"
    message = json.loads(raw)
    assert message["type"] == "message" and message["role"] == "assistant" and message["model"] == "anthropic/claude-sonnet-5.5"
    assert message["content"] == [{"type": "text", "text": "a simple whistle"}]
    assert message["stop_reason"] == "end_turn" and message["usage"] == {"input_tokens": 111, "output_tokens": 7}
    (call,) = _calls(tmp_path, "claude")
    argv = call["argv"]
    assert argv[:1] == ["-p"] and "--tools=" in argv and argv[argv.index("--model") + 1] == "claude-sonnet-5-5"
    assert argv[argv.index("--system-prompt") + 1] == "You are a CAD assistant."
    assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv and "--disable-slash-commands" in argv
    event = json.loads(call["stdin"].splitlines()[0])
    assert event["type"] == "user" and "make a whistle" in event["message"]["content"][0]["text"]


def test_a_prefixed_or_unprefixed_path_both_work_and_bearer_auth_is_accepted(make):
    bridge = make()
    assert bridge.post(_body(), path="/messages")[0] == 200
    assert bridge.post(_body(), header="Authorization")[0] == 200


# ---- tool calls ---------------------------------------------------------------------------


def _envelope(**kwargs):
    return json.dumps({"text": kwargs.get("text", ""), "tool_calls": kwargs["calls"]})


def test_forced_tool_call_becomes_a_tool_use_block(make, tmp_path):
    bridge = make(claude_plan=[_envelope(calls=[{"name": "build_parametric_model", "input": {"code": "cube(1);"}}])])
    status, raw, _ = bridge.post(_body(tools=TOOLS, tool_choice={"type": "tool", "name": "build_parametric_model"}))
    message = json.loads(raw)
    assert status == 200 and message["stop_reason"] == "tool_use"
    (block,) = message["content"]
    assert block["type"] == "tool_use" and block["name"] == "build_parametric_model" and block["input"] == {"code": "cube(1);"}
    assert block["id"].startswith("toolu_")
    prompt = json.loads(_calls(tmp_path, "claude")[0]["stdin"].splitlines()[0])["message"]["content"][0]["text"]
    assert "build_parametric_model" in prompt and "You MUST call the tool" in prompt and "input_schema" in prompt


def test_prose_around_a_fenced_envelope_is_tolerated_and_text_is_kept(make):
    wrapped = "Sure!\n```json\n" + _envelope(text="building it", calls=[{"name": "build_parametric_model", "input": {"code": "x"}}]) + "\n```"
    bridge = make(claude_plan=[wrapped])
    message = json.loads(bridge.post(_body(tools=TOOLS))[1])
    assert [b["type"] for b in message["content"]] == ["text", "tool_use"]


def test_malformed_json_is_retried_once_with_the_reason_then_succeeds(make, tmp_path):
    good = _envelope(calls=[{"name": "build_parametric_model", "input": {"code": "ok"}}])
    bridge = make(claude_plan=["not json at all", good])
    status, raw, _ = bridge.post(_body(tools=TOOLS, tool_choice={"type": "any"}))
    assert status == 200 and json.loads(raw)["content"][0]["input"] == {"code": "ok"}
    first, second = _calls(tmp_path, "claude")
    assert "rejected" not in first["stdin"] and "previous answer was rejected" in second["stdin"]


@pytest.mark.parametrize("bad", ["still not json", _envelope(calls=[{"name": "unknown_tool", "input": {}}]),
                                 _envelope(calls=[])])
def test_two_bad_answers_give_a_clean_api_error(make, tmp_path, bad):
    bridge = make(claude_plan=[bad, bad])
    status, raw, _ = bridge.post(_body(tools=TOOLS, tool_choice={"type": "any"}))
    error = json.loads(raw)
    assert status == 502 and error["type"] == "error" and error["error"]["type"] == "api_error"
    assert "tool-call envelope" in error["error"]["message"]
    assert len(_calls(tmp_path, "claude")) == 2  # exactly one retry


def test_tool_results_in_history_are_rendered_for_the_model(make, tmp_path):
    bridge = make(claude_plan=[_envelope(text="done", calls=[])])
    messages = [
        {"role": "user", "content": "make a whistle"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "build_parametric_model", "input": {"code": "c"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": [{"type": "text", "text": "compiled ok"}]}]},
    ]
    assert bridge.post(_body(messages=messages, tools=TOOLS, tool_choice={"type": "auto"}))[0] == 200
    prompt = json.loads(_calls(tmp_path, "claude")[0]["stdin"].splitlines()[0])["message"]["content"][0]["text"]
    assert "tool call build_parametric_model" in prompt and "compiled ok" in prompt


# ---- streaming ----------------------------------------------------------------------------


def test_streaming_text_emits_the_anthropic_event_sequence(make):
    bridge = make(claude_plan=["x" * 900])
    status, raw, ctype = bridge.post(_body(stream=True))
    assert status == 200 and ctype == "text/event-stream"
    events = _events(raw)
    names = [name for name, _ in events]
    assert names[0] == "message_start" and names[1] == "content_block_start" and names[-2:] == ["message_delta", "message_stop"]
    assert names.count("content_block_delta") == 3 and "content_block_stop" in names
    assert "".join(e["delta"]["text"] for n, e in events if n == "content_block_delta") == "x" * 900
    assert dict(events)["message_delta"]["delta"]["stop_reason"] == "end_turn"


def test_streaming_tool_call_uses_input_json_delta(make):
    call = {"name": "build_parametric_model", "input": {"code": "cube(2);"}}
    bridge = make(claude_plan=[_envelope(calls=[call])])
    events = _events(bridge.post(_body(stream=True, tools=TOOLS, tool_choice={"type": "tool", "name": "build_parametric_model"}))[1])
    start = next(e for n, e in events if n == "content_block_start")
    assert start["content_block"]["type"] == "tool_use" and start["content_block"]["input"] == {}
    delta = next(e for n, e in events if n == "content_block_delta")
    assert delta["delta"]["type"] == "input_json_delta" and json.loads(delta["delta"]["partial_json"]) == call["input"]
    assert dict(events)["message_delta"]["delta"]["stop_reason"] == "tool_use"


def test_streaming_failure_after_headers_is_an_sse_error_event(make):
    bridge = make(claude_plan=["FAIL"])
    status, raw, ctype = bridge.post(_body(stream=True))
    assert status == 200 and ctype == "text/event-stream"
    names = [n for n, _ in _events(raw)]
    assert names == ["message_start", "error"]


# ---- failures -----------------------------------------------------------------------------


def test_cli_timeout_is_a_504_and_the_process_is_killed(make):
    bridge = make(claude_plan=["SLEEP"], timeout_s=0.6)
    status, raw, _ = bridge.post(_body())
    assert status == 504 and json.loads(raw)["error"]["type"] == "timeout_error"


def test_cli_crash_is_a_502_with_only_the_exit_status(make):
    status, raw, _ = make(claude_plan=["FAIL"]).post(_body())
    assert status == 502 and json.loads(raw)["error"]["message"] == "claude exited with status 3"
    assert "blew up" not in raw  # stderr text is never forwarded


def test_stderr_secrets_never_reach_the_client_logs_or_sse(make, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    bridge = make(claude_plan=["FAIL"])
    (tmp_path / "claude" / "claude").write_text((tmp_path / "claude" / "claude").read_text().replace(
        'sys.stderr.write("claude blew up\\n")', 'sys.stderr.write("token=sk-SECRET-MARKER prompt=PRIVATE-FRAGMENT\\n")'))
    status, raw, _ = bridge.post(_body())
    streamed = bridge.post(_body(stream=True))[1]
    logs = "\n".join(r.getMessage() for r in caplog.records)
    for text in (raw, streamed, logs):
        assert "SECRET-MARKER" not in text and "PRIVATE-FRAGMENT" not in text
    assert status == 502 and "exited with status 3" in streamed and "withheld" in logs


@pytest.mark.parametrize("key", [None, "wrong-key-0123456789ab", ""])
def test_missing_or_wrong_key_is_rejected_before_any_cli_runs(make, tmp_path, key):
    bridge = make()
    status, raw, _ = bridge.post(_body(), key=key)
    assert status == 401 and json.loads(raw)["error"]["type"] == "authentication_error"
    assert _calls(tmp_path, "claude") == []


def test_unmapped_model_and_bad_requests_are_clean_errors(make):
    bridge = make()
    assert bridge.post(_body(model="anthropic/unknown"))[0] == 404
    status, raw, _ = bridge.post({"model": "anthropic/claude-sonnet-5.5", "messages": []})
    assert status == 400 and json.loads(raw)["error"]["type"] == "invalid_request_error"
    status, _, _ = bridge.post(_body(tools=TOOLS, tool_choice={"type": "tool", "name": "nope"}))
    assert status == 400
    conn = http.client.HTTPConnection("127.0.0.1", bridge.port)
    conn.request("POST", "/v1/messages", "{not json", {"x-api-key": KEY, "Content-Type": "application/json"})
    assert conn.getresponse().status == 400


def test_health_endpoint_lists_models_without_a_key(make):
    bridge = make()
    conn = http.client.HTTPConnection("127.0.0.1", bridge.port)
    conn.request("GET", "/health")
    body = json.loads(conn.getresponse().read())
    assert body["status"] == "ok" and "anthropic/claude-sonnet-5.5" in body["models"]


# ---- codex + images -----------------------------------------------------------------------


def test_codex_runs_read_only_with_stdin_closed_and_returns_its_last_message(make, tmp_path):
    bridge = make(codex_plan=["codex says hi"])
    status, raw, _ = bridge.post(_body(model="openai/gpt-6.1-sol"))
    assert status == 200 and json.loads(raw)["content"] == [{"type": "text", "text": "codex says hi"}]
    (call,) = _calls(tmp_path, "codex")
    argv = call["argv"]
    assert argv[0] == "exec" and argv[argv.index("--sandbox") + 1] == "read-only"
    assert "--skip-git-repo-check" in argv and argv[argv.index("-m") + 1] == "gpt-6.1-sol"
    assert call["stdin"] == "" and "You are a CAD assistant." in argv[-1]  # </dev/null; system folded into the prompt


def test_images_reach_both_runtimes(make, tmp_path):
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "match this"}, {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}}]}]
    bridge = make(claude_plan=["ok"], codex_plan=["ok"])
    bridge.post(_body(messages=messages))
    bridge.post(_body(messages=messages, model="openai/gpt-6.1-sol"))
    claude_in = json.loads(_calls(tmp_path, "claude")[0]["stdin"].splitlines()[0])["message"]["content"]
    assert claude_in[1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG}}
    codex_argv = _calls(tmp_path, "codex")[0]["argv"]
    assert "-i" in codex_argv and codex_argv[codex_argv.index("-i") + 1].endswith(".png")


def test_huge_prompts_go_through_a_file_for_codex_not_argv(make, tmp_path):
    bridge = make(codex_plan=["ok"])
    bridge.post(_body(model="openai/gpt-6.1-sol", messages=[{"role": "user", "content": "z" * 150_000}]))
    assert "PROMPT.md" in _calls(tmp_path, "codex")[0]["argv"][-1]


# ---- safety -------------------------------------------------------------------------------


def test_prompts_are_never_logged(make, caplog):
    caplog.set_level(logging.DEBUG)
    bridge = make(claude_plan=["secret answer text"])
    bridge.post(_body(messages=[{"role": "user", "content": "PRIVATE-PROMPT-MARKER"}]))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "PRIVATE-PROMPT-MARKER" not in text and "secret answer text" not in text and KEY not in text
    assert "status=200" in text


def test_bridge_refuses_non_loopback_binds(tmp_path):
    config = BridgeConfig(models={"m": ModelRoute("claude", "m")}, host="0.0.0.0", port=0, api_key=KEY)
    with pytest.raises(ValueError, match="loopback"):
        make_server(config)


def test_config_requires_a_long_enough_key_and_valid_runtimes(tmp_path):
    path = tmp_path / "bridge.json"
    path.write_text(json.dumps({"models": {"a/b": {"runtime": "claude", "cli_model": "m"}}}))
    with pytest.raises(ValueError, match="shared key"):
        load_config(path, environ={})
    with pytest.raises(ValueError, match="shared key"):
        load_config(path, environ={"CADAM_CLI_BRIDGE_KEY": "short"})
    config = load_config(path, environ={"CADAM_CLI_BRIDGE_KEY": KEY})
    assert config.host == "127.0.0.1" and KEY not in repr(config)
    path.write_text(json.dumps({"models": {"a/b": {"runtime": "gemini", "cli_model": "m"}}}))
    with pytest.raises(ValueError, match="runtime"):
        load_config(path, environ={"CADAM_CLI_BRIDGE_KEY": KEY})
    example = ROOT / "tools" / "cadam_cli_bridge" / "bridge.example.json"
    assert load_config(example, environ={"CADAM_CLI_BRIDGE_KEY": KEY}).models


def test_envelope_helpers():
    assert extract_json('noise {"a": {"b": 1}} trailing') == {"a": {"b": 1}}
    with pytest.raises(EnvelopeError):
        parse_envelope("{}", [{"name": "t"}], {"type": "any"})
    assert parse_envelope("{}", [{"name": "t"}], None) == [{"type": "text", "text": ""}]


# ---- request shape validation -------------------------------------------------------------


@pytest.mark.parametrize("patch", [
    {"messages": [None]}, {"messages": ["hi"]}, {"messages": "hi"}, {"messages": [{"role": "user", "content": 5}]},
    {"messages": [{"role": "user", "content": [None]}]}, {"messages": [{"role": "robot", "content": "x"}]},
    {"tools": TOOLS, "tool_choice": "auto"}, {"tools": TOOLS, "tool_choice": {"type": "bogus"}},
    {"tools": TOOLS, "tool_choice": {"type": "tool"}}, {"tools": "nope"}, {"tools": [None]},
    {"tools": [{"description": "no name"}]}, {"tools": [{"name": "t", "input_schema": "x"}]}, {"system": 5},
    {"messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "a"}}]}]},
    {"messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "text/html", "data": PNG}}]}]},
    {"messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "url", "url": "http://x"}}]}]},
    {"model": 5},
])
def test_malformed_requests_get_a_clean_json_error_not_a_dropped_connection(make, tmp_path, patch):
    bridge = make()
    for stream in (False, True):
        status, raw, ctype = bridge.post({**_body(), **patch, "stream": stream})
        assert status in {400, 404} and ctype == "application/json", (patch, status, raw[:200])
        assert json.loads(raw)["type"] == "error"
    assert _calls(tmp_path, "claude") == []  # rejected before any CLI started


def test_unexpected_internal_errors_are_a_json_500_not_a_traceback(make, monkeypatch):
    bridge = make()
    monkeypatch.setattr(type(bridge.server), "complete", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    status, raw, _ = bridge.post(_body())
    assert status == 500 and json.loads(raw)["error"] == {"type": "api_error", "message": "internal bridge error"}
    streamed = _events(bridge.post(_body(stream=True))[1])
    assert [n for n, _ in streamed] == ["message_start", "error"] and "boom" not in json.dumps(streamed)


def test_streaming_reports_the_same_usage_as_the_plain_response(make):
    bridge = make(claude_plan=["same answer", "same answer"])
    plain = json.loads(bridge.post(_body())[1])["usage"]
    events = _events(bridge.post(_body(stream=True))[1])
    assert dict(events)["message_delta"]["usage"] == plain == {"input_tokens": 111, "output_tokens": 7}
