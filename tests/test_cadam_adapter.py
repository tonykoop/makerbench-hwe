import json
from pathlib import Path

import pytest
from PIL import Image

from makerbench.cadam_adapter import (
    CadamClient,
    CadamConfig,
    CadamRecoveryRequiredError,
    _extract_build_code,
)


def _image(path: Path, size=(2200, 1600)) -> Path:
    Image.new("RGB", size, (112, 82, 54)).save(path)
    return path


def test_extract_build_code_uses_structured_tool_part():
    payload = [
        {
            "parts": [
                {"type": "text", "text": "not code"},
                {
                    "type": "tool-build_parametric_model",
                    "input": {"code": "cube([1,2,3]);"},
                },
            ]
        }
    ]
    assert _extract_build_code(payload) == "cube([1,2,3]);"


def test_headless_cadam_round_trip(tmp_path):
    calls = []
    chat_started = False
    assistant_rows = [
        {
            "id": "assistant-1",
            "metadata": {"billingTokens": 66},
            "parts": [
                {
                    "type": "tool-build_parametric_model",
                    "input": {"code": "$fn=48; sphere(12);"},
                }
            ],
        }
    ]

    def transport(method, url, headers, body):
        nonlocal chat_started
        calls.append((method, url, headers, body))
        if method == "GET" and "/rest/v1/messages?" in url:
            return json.dumps(assistant_rows if chat_started else []).encode()
        if "/api/parametric-chat" in url:
            chat_started = True
            return b"data: streamed-response"
        return b"{}"

    client = CadamClient(
        CadamConfig(
            base_url="http://cadam.test",
            supabase_url="http://supabase.test",
            user_id="user-1",
            access_token="user-token",
            service_role_key="service-token",
        ),
        transport=transport,
        sleep_fn=lambda _seconds: None,
    )
    result = client.generate(
        prompt="Build a historical harp",
        reference_image=_image(tmp_path / "source.png"),
        output_dir=tmp_path / "out",
    )

    assert result.scad_path.read_text() == "$fn=48; sphere(12);\n"
    assert result.cost_usd == 0.66
    assert result.prepared_image_path.stat().st_size < 4_000_000
    with Image.open(result.prepared_image_path) as prepared:
        assert max(prepared.size) <= 1400
    assert any("/storage/v1/object/images/" in url for _, url, _, _ in calls)
    chat = next(call for call in calls if "/api/parametric-chat" in call[1])
    assert chat[2]["Authorization"] == "Bearer user-token"
    assert b"anthropic/claude-fable-5" in chat[3]


def test_cadam_requires_structured_code(tmp_path):
    def transport(method, url, _headers, _body):
        if method == "GET" and "/rest/v1/messages?" in url:
            return b'[{"parts":[{"type":"text","text":"done"}]}]'
        return b"{}"

    client = CadamClient(
        CadamConfig(
            base_url="http://cadam.test",
            supabase_url="http://supabase.test",
            user_id="user-1",
            access_token="user-token",
            service_role_key="service-token",
        ),
        transport=transport,
        sleep_fn=lambda _seconds: None,
    )
    try:
        client.generate(
            prompt="build",
            reference_image=_image(tmp_path / "source.png", (400, 400)),
            output_dir=tmp_path / "out",
        )
    except RuntimeError as exc:
        assert "structured build_parametric_model" in str(exc)
    else:
        raise AssertionError("missing CADAM tool code should fail")


def test_resume_only_recovers_same_conversation_without_reposting(tmp_path):
    calls = []
    conversation_id = "125b4ed7-c76f-5f23-a331-83ea5fbb51b6"
    assistant_rows = [
        {
            "metadata": {"billingTokens": 21},
            "parts": [
                {
                    "type": "tool-build_parametric_model",
                    "input": {"code": "cube(8);"},
                }
            ],
        }
    ]

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        if method == "GET" and "/rest/v1/messages?" in url:
            return json.dumps(assistant_rows).encode()
        raise AssertionError(f"resume made an unsafe request: {method} {url}")

    client = CadamClient(
        CadamConfig(
            base_url="http://cadam.test",
            supabase_url="http://supabase.test",
            user_id="user-1",
            access_token="user-token",
            service_role_key="service-token",
        ),
        transport=transport,
        sleep_fn=lambda _seconds: None,
    )
    result = client.generate(
        prompt="recover",
        reference_image=_image(tmp_path / "source.png", (400, 400)),
        output_dir=tmp_path / "out",
        conversation_id=conversation_id,
        resume_only=True,
    )

    assert result.conversation_id == conversation_id
    assert result.cost_usd == 0.21
    assert result.scad_path.read_text() == "cube(8);\n"
    assert {method for method, *_ in calls} == {"GET"}


def test_resume_only_refuses_to_repost_unresolved_paid_dispatch(tmp_path):
    calls = []

    def transport(method, url, headers, body):
        calls.append((method, url, headers, body))
        return b"[]"

    client = CadamClient(
        CadamConfig(
            base_url="http://cadam.test",
            supabase_url="http://supabase.test",
            user_id="user-1",
            access_token="user-token",
            service_role_key="service-token",
        ),
        transport=transport,
        sleep_fn=lambda _seconds: None,
    )

    with pytest.raises(CadamRecoveryRequiredError, match="refusing automatic repost"):
        client.generate(
            prompt="recover",
            reference_image=_image(tmp_path / "source.png", (400, 400)),
            output_dir=tmp_path / "out",
            conversation_id="125b4ed7-c76f-5f23-a331-83ea5fbb51b6",
            resume_only=True,
        )

    assert {method for method, *_ in calls} == {"GET"}


# ---- #660: bot sign-in, static-token fallback, redaction -----------------------------------

import urllib.error  # noqa: E402

from makerbench.cadam_adapter import CadamAuthError, sign_in  # noqa: E402

BOT_PASSWORD = "bot-pass-SECRET-123"
SIGNED_IN_TOKEN = "signed-in-jwt-SECRET-456"
STATIC_TOKEN = "static-token-SECRET-789"
ANON_KEY = "anon-key-SECRET-000"


def _config(**overrides):
    base = dict(
        base_url="http://cadam.test",
        supabase_url="http://supabase.test",
        user_id="user-1",
        service_role_key="service-token",
    )
    base.update(overrides)
    return CadamConfig(**base)


def _bot_config(**overrides):
    return _config(bot_email="bot@example.test", bot_password=BOT_PASSWORD, anon_key=ANON_KEY, **overrides)


def _recording_transport(calls, *, sign_in_reply=None):
    rows = [{"id": "a", "metadata": {}, "parts": [{"type": "tool-build_parametric_model", "input": {"code": "cube(1);"}}]}]
    started: set[str] = set()  # conversations whose chat POST has happened

    def transport(method, url, headers, body):
        calls.append((method, url, dict(headers), body))
        if "/auth/v1/token" in url:
            if isinstance(sign_in_reply, Exception):
                raise sign_in_reply
            return sign_in_reply or json.dumps({"access_token": SIGNED_IN_TOKEN}).encode()
        if method == "GET" and "/rest/v1/messages?" in url:
            return json.dumps(rows if any(cid in url for cid in started) else []).encode()
        if "/api/parametric-chat" in url:
            started.add(json.loads(body)["conversationId"])
            return b"data: ok"
        return b"{}"

    return transport


def test_generate_signs_in_with_the_bot_and_uses_the_fresh_token(tmp_path):
    calls = []
    client = CadamClient(_bot_config(access_token=STATIC_TOKEN), transport=_recording_transport(calls), sleep_fn=lambda _s: None)
    result = client.generate(prompt="a whistle", output_dir=tmp_path / "out")  # no image

    auth = next(c for c in calls if "/auth/v1/token" in c[1])
    assert auth[0] == "POST" and auth[1] == "http://supabase.test/auth/v1/token?grant_type=password"
    assert auth[2]["apikey"] == ANON_KEY
    assert json.loads(auth[3]) == {"email": "bot@example.test", "password": BOT_PASSWORD}
    chat = next(c for c in calls if "/api/parametric-chat" in c[1])
    assert chat[2]["Authorization"] == f"Bearer {SIGNED_IN_TOKEN}"  # bot token wins over the static one
    assert calls.index(auth) < calls.index(next(c for c in calls if c[1].endswith("/rest/v1/conversations")))
    assert result.scad_path.read_text() == "cube(1);\n"
    assert result.prepared_image_path is None
    assert not any("/storage/v1/object/images/" in c[1] for c in calls)


def test_each_generate_signs_in_again(tmp_path):
    calls = []
    client = CadamClient(_bot_config(), transport=_recording_transport(calls), sleep_fn=lambda _s: None)
    client.generate(prompt="one", output_dir=tmp_path / "a")
    client.generate(prompt="two", output_dir=tmp_path / "b")
    assert sum("/auth/v1/token" in c[1] for c in calls) == 2


def test_static_token_is_used_only_when_no_bot_credentials(tmp_path):
    calls = []
    client = CadamClient(_config(access_token=STATIC_TOKEN), transport=_recording_transport(calls), sleep_fn=lambda _s: None)
    client.generate(prompt="a whistle", output_dir=tmp_path / "out")
    assert not any("/auth/v1/token" in c[1] for c in calls)
    chat = next(c for c in calls if "/api/parametric-chat" in c[1])
    assert chat[2]["Authorization"] == f"Bearer {STATIC_TOKEN}"


def test_validate_accepts_bot_credentials_in_place_of_a_static_token():
    _bot_config().validate()
    _config(access_token=STATIC_TOKEN).validate()
    with pytest.raises(ValueError, match="access_token"):
        _config().validate()
    with pytest.raises(ValueError, match="anon_key"):
        _config(bot_email="bot@example.test", bot_password=BOT_PASSWORD).validate()


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.HTTPError("http://supabase.test", 400, f"bad {BOT_PASSWORD} {ANON_KEY}", {}, None),
        urllib.error.URLError(f"refused {BOT_PASSWORD}"),
        ConnectionError(f"reset {SIGNED_IN_TOKEN}"),
    ],
)
def test_sign_in_failure_is_redacted_and_stops_before_any_write(tmp_path, failure):
    calls = []
    client = CadamClient(_bot_config(), transport=_recording_transport(calls, sign_in_reply=failure), sleep_fn=lambda _s: None)
    with pytest.raises(CadamAuthError) as raised:
        client.generate(prompt="x", output_dir=tmp_path / "out")
    text = f"{raised.value!s} {raised.value!r} {raised.value.__cause__!r} {raised.value.__context__!r}"
    for secret in (BOT_PASSWORD, ANON_KEY, SIGNED_IN_TOKEN, "bot@example.test"):
        assert secret not in text
    assert not any(c[1].endswith("/rest/v1/conversations") for c in calls)  # nothing written, nothing paid


@pytest.mark.parametrize("reply", [b"not json", b"[]", b'{"access_token": ""}', b"{}"])
def test_sign_in_rejects_a_reply_without_a_token(reply):
    with pytest.raises(CadamAuthError):
        sign_in("http://supabase.test", ANON_KEY, "bot@example.test", BOT_PASSWORD, lambda *_a: reply)


def test_config_repr_never_contains_a_secret():
    text = repr(_bot_config(access_token=STATIC_TOKEN))
    for secret in (BOT_PASSWORD, ANON_KEY, STATIC_TOKEN, "bot@example.test", "service-token"):
        assert secret not in text
