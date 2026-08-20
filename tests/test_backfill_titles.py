"""scripts/backfill_titles.py — the pure candidate-selection predicate.
No Notion, no network."""
from scripts import backfill_titles as bt


def test_truncated_prefix_is_candidate():
    title = "Build a complete four-agent Claude council that debates any decision"  # 67
    callout = title + " and returns one reconciled recommendation."
    assert bt.is_truncated_prefix(title, callout)


def test_complete_title_equal_to_callout_is_not_candidate():
    t = "A short complete main point."
    assert not bt.is_truncated_prefix(t, t)


def test_callout_shorter_is_not_candidate():
    assert not bt.is_truncated_prefix("longer title here", "short")


def test_hand_edited_divergent_title_is_not_candidate():
    # Callout does NOT start with the title -> someone edited the title; leave it.
    assert not bt.is_truncated_prefix("My own headline", "The model's original main point, longer.")


def test_empty_inputs_are_not_candidates():
    assert not bt.is_truncated_prefix("", "something")
    assert not bt.is_truncated_prefix("something", "")


def test_new_title_respects_200_cap():
    # A 250-char callout must be trimmed to NEW_CAP when written.
    long = "x" * 250
    assert len(long[: bt.NEW_CAP]) == 200
