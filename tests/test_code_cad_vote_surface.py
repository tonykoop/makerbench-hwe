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
