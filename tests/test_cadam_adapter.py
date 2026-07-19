import json
from pathlib import Path

from PIL import Image

from makerbench.cadam_adapter import CadamClient, CadamConfig, _extract_build_code


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
        calls.append((method, url, headers, body))
        if method == "GET" and "/rest/v1/messages?" in url:
            return json.dumps(assistant_rows).encode()
        if "/api/parametric-chat" in url:
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
