"""run_log_io by-id helpers replace the raw lookups that used to read ``["trials"]`` directly (#1085)."""

from __future__ import annotations

from makerbench.run_log_io import find_trial, has_trial, summary_trials, trial_rows, trials_by_id

LOG = {
    "config": {"backend": "openscad"},
    "trials": [
        {"trial_id": "a", "model_id": "m"},
        {"trial_id": "b", "model_id": "m", "track": "external_step"},
        {"trial_id": "b", "model_id": "dup"},
    ],
}


def test_find_trial_sees_every_stored_row_including_off_track_ones():
    assert find_trial(LOG, "a")["model_id"] == "m"
    assert find_trial(LOG, "missing") is None
    assert has_trial(LOG, "b") and not has_trial(LOG, "zzz")
    # lookups are by id over ALL rows, unlike the filtered aggregate reader
    assert [r["trial_id"] for r in trial_rows(LOG)] == ["a", "b"]


def test_unique_refuses_ambiguous_ids():
    assert find_trial(LOG, "b", unique=True) is None
    assert find_trial(LOG, "a", unique=True)["trial_id"] == "a"
    assert find_trial({"trials": None}, "a") is None


def test_trials_by_id_and_summary_trials():
    assert set(trials_by_id(LOG)) == {"a", "b"}
    assert summary_trials({"trials": [1, 2]}) == [1, 2]
    assert summary_trials({}) == []
