"""Tests for Code-CAD Arena blind A/B vote surface (#424)."""

import json

import pytest

from makerbench import code_cad_vote_surface as vote


def _candidate(candidate_id, model_id):
    return vote.VoteCandidate(
        candidate_id=candidate_id,
        model_id=model_id,
        trial_id=f"lyre-s0-{candidate_id}",
        render_path=f"renders/{candidate_id}.png",
        provenance={"provider": model_id.split("-", 1)[0]},
    )


def test_blind_pair_payload_hides_model_ids():
    pair = vote.build_blind_pair(
        _candidate("a", "gpt-5.5"),
        _candidate("b", "sonnet"),
        pair_seed="lyre",
    )

    payload = vote.blind_pair_payload(pair)
    encoded = json.dumps(payload)

    assert payload["schema"] == vote.SCHEMA
    assert "gpt-5.5" not in encoded
    assert "sonnet" not in encoded
    assert payload["left"]["render_path"].startswith("renders/")
    assert payload["right"]["trial_id"].startswith("lyre-s0-")


def test_vote_surface_html_is_blind_but_usable():
    pair = vote.build_blind_pair(
        _candidate("a", "gpt-5.5"),
        _candidate("b", "sonnet"),
        pair_seed="lyre",
    )

    html = vote.render_vote_surface(pair)

    assert "gpt-5.5" not in html
    assert "sonnet" not in html
    assert 'data-vote="left"' in html
    assert 'data-vote="draw"' in html
    assert 'data-vote="right"' in html
    assert "renders/" in html


def test_vote_record_persists_without_reveal(tmp_path):
    pair = vote.build_blind_pair(
        _candidate("a", "gpt-5.5"),
        _candidate("b", "sonnet"),
        pair_seed="lyre",
    )
    record = vote.record_vote(pair, winner="left", voter_id="tony")
    path = tmp_path / "votes.jsonl"

    vote.append_vote_record(path, record)
    saved = json.loads(path.read_text(encoding="utf-8"))

    assert saved["winner"] == "left"
    assert saved["voter_id"] == "tony"
    assert "model_id" not in json.dumps(saved)
    assert saved["left"]["candidate_id"] in {"a", "b"}


def test_partner_peek_reveal_adds_model_id_after_vote():
    pair = vote.build_blind_pair(
        _candidate("a", "gpt-5.5"),
        _candidate("b", "sonnet"),
        pair_seed="lyre",
    )
    record = vote.record_vote(pair, winner="right", voter_id="tony")

    revealed = vote.reveal_vote(pair, record)

    models = {revealed["reveal"]["left"]["model_id"], revealed["reveal"]["right"]["model_id"]}
    assert models == {"gpt-5.5", "sonnet"}
    assert revealed["winner"] == "right"
    assert revealed["reveal"]["left"]["provenance"]


def test_invalid_pair_and_vote_inputs_rejected():
    candidate = _candidate("a", "gpt-5.5")
    with pytest.raises(ValueError, match="must differ"):
        vote.build_blind_pair(candidate, candidate, pair_seed="same")

    pair = vote.build_blind_pair(candidate, _candidate("b", "sonnet"), pair_seed="lyre")
    with pytest.raises(ValueError, match="winner"):
        vote.record_vote(pair, winner="middle", voter_id="tony")  # type: ignore[arg-type]


def test_doc_names_twingrid_and_private_boundary():
    text = open("docs/CODE_CAD_VOTE_SURFACE.md", encoding="utf-8").read()
    assert "TwinGrid" in text
    assert "Partner-Peek" in text
    assert "Selecta internals" in text


class TestModel3dVoteSurface:
    def _pair_with_3d(self):
        left = vote.VoteCandidate(
            candidate_id="a", model_id="gpt-5.5", trial_id="lyre-s0-a",
            render_path="blind/pair-x-left.png", model3d_path="blind/pair-x-left.glb",
        )
        right = vote.VoteCandidate(
            candidate_id="b", model_id="sonnet", trial_id="lyre-s0-b",
            render_path="blind/pair-x-right.png", model3d_path="blind/pair-x-right.glb",
        )
        return vote.build_blind_pair(left, right, pair_seed="lyre")

    def test_glb_candidates_get_rotatable_viewer_with_img_fallback(self):
        html = vote.render_vote_surface(self._pair_with_3d())
        assert html.count("<model-viewer") == 2
        assert "camera-controls" in html
        assert vote.MODEL_VIEWER_CDN in html
        # the static render nests inside the viewer as the no-JS fallback
        assert html.count("<img src=") == 2

    def test_png_only_pair_has_no_viewer_or_cdn_script(self):
        pair = vote.build_blind_pair(
            _candidate("a", "gpt-5.5"), _candidate("b", "sonnet"), pair_seed="lyre"
        )
        html = vote.render_vote_surface(pair)
        assert "<model-viewer" not in html
        assert vote.MODEL_VIEWER_CDN not in html

    def test_page_markup_never_leaks_candidate_or_trial_ids(self):
        # Trial ids embed entrant names; the blind page must not carry them
        # even in data attributes (#602 blindness hardening).
        html = vote.render_vote_surface(self._pair_with_3d())
        assert "lyre-s0-a" not in html
        assert "candidate-id" not in html

    def test_model3d_path_rides_blind_and_revealed_records(self):
        pair = self._pair_with_3d()
        record = vote.record_vote(pair, winner="left", voter_id="tony")
        assert record["left"]["model3d_path"].endswith(".glb")
        revealed = vote.reveal_vote(pair, record)
        assert revealed["reveal"]["left"]["model3d_path"].endswith(".glb")

    def _pair_with_frames(self):
        lframes = tuple(f"blind/pair-x-left-f{i:02d}.png" for i in range(16))
        rframes = tuple(f"blind/pair-x-right-f{i:02d}.png" for i in range(16))
        left = vote.VoteCandidate(
            candidate_id="a", model_id="opus", trial_id="lyre-s0-a",
            render_path=lframes[0], model3d_path="blind/pair-x-left.glb",
            frames=lframes,
        )
        right = vote.VoteCandidate(
            candidate_id="b", model_id="glm", trial_id="lyre-s0-b",
            render_path=rframes[0], frames=rframes,
        )
        return vote.build_blind_pair(left, right, pair_seed="lyre")

    def test_frames_render_webgl_free_turntable_not_model_viewer(self):
        html = vote.render_vote_surface(self._pair_with_frames())
        # both candidates use the turntable (frames win over GLB)
        assert html.count('class="turntable"') == 2
        assert "data-frames=" in html
        assert vote.TURNTABLE_JS.strip()[:20] in html
        # no WebGL viewer: left had a GLB but frames take precedence, right had none
        assert "<model-viewer" not in html
        assert vote.MODEL_VIEWER_CDN not in html

    def test_frames_ride_blind_and_revealed_records(self):
        pair = self._pair_with_frames()
        record = vote.record_vote(pair, winner="left", voter_id="tony")
        assert len(record["left"]["frames"]) == 16
        revealed = vote.reveal_vote(pair, record)
        assert len(revealed["reveal"]["left"]["frames"]) == 16

    def test_turntable_page_has_spin_toggle_and_pause_logic(self):
        html = vote.render_vote_surface(self._pair_with_frames())
        # a spin toggle control, distinct from the vote buttons
        assert 'id="spin-toggle"' in html
        assert 'data-role="spin"' in html
        assert 'data-vote=' not in html.split('id="spin-toggle"')[1][:60]
        # pause/play logic is wired in the turntable script
        assert "Pause spin" in html
        assert "aria-pressed" in html

    def test_no_frames_no_spin_toggle(self):
        pair = vote.build_blind_pair(
            _candidate("a", "gpt-5.5"), _candidate("b", "sonnet"), pair_seed="lyre"
        )
        html = vote.render_vote_surface(pair)
        assert "spin-toggle" not in html          # nothing to spin

    def test_surface_is_responsive(self):
        html = vote.render_vote_surface(self._pair_with_frames())
        # narrow screens stack the pair into one column
        assert "@media (max-width: 720px)" in html
        assert "grid-template-columns: 1fr" in html
