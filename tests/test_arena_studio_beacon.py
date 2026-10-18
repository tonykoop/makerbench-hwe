"""The Control Room beacon the instrument library probes for "Run tonight" (#1112)."""

from __future__ import annotations

import io

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from makerbench.arena_studio import create_studio_app  # noqa: E402
from makerbench.arena_studio.app import BEACON_PATH, BEACON_SIZE  # noqa: E402


def test_the_beacon_is_a_fixed_size_image_any_page_may_load(tmp_path):
    client = TestClient(create_studio_app(repo_root=tmp_path), base_url="http://127.0.0.1")
    response = client.get(BEACON_PATH, headers={"Origin": "https://tonykoop.github.io"})
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["cross-origin-resource-policy"] == "cross-origin"
    with Image.open(io.BytesIO(response.content)) as image:
        image.load()
        assert image.size == BEACON_SIZE == (23, 7)
    assert "access-control-allow-origin" not in response.headers  # nothing readable cross-origin
    assert client.get(BEACON_PATH, headers={"Host": "evil.example"}).status_code == 400  # DNS-rebinding guard stays
