"""Matching-accuracy regression suite (Phase 4 / agent E1).

The fixture is REAL data captured from the live system:
  - `pairs`: 12 resources whose correct reel is already known, because that
    reel has this exact URL attached as its Gate resource. Each carries the
    resource's genuinely-fetched text (title + first 600 words).
  - `pool`: all 92 rows that carry a gate keyword or an attached resource —
    the same crowded field a real /attach scores against.

This is the guard on the accuracy claim: the scorer must find the KNOWN-correct
reel, competing against 91 wrong ones, on real text. Nothing here is invented.
"""
import json
from pathlib import Path

from app import attach_matching

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures_attach_pairs.json").read_text(encoding="utf-8")
)
POOL = FIXTURE["pool"]
PAIRS = FIXTURE["pairs"]

# MEASURED on this exact fixture (see PROGRESS.md for the full before/after):
#   before Phase 4:  top-1 33%,  top-3 50%
#   after  Phase 4:  top-1 42%,  top-3 58%
# These are a RATCHET at the measured values, not an aspiration -- they exist
# so a future change can't quietly undo the gain. They are also honestly low:
# the biggest intended lever (scoring against the reel's named_entities) is
# inert on live data because named_entities is never persisted to Notion.
# See PROGRESS.md; raising these needs that fixed first.
MIN_TOP1_ACCURACY = 0.41
MIN_TOP3_ACCURACY = 0.58


def _score_pair(pair: dict) -> list[dict]:
    return attach_matching.rank_candidates(pair["title"], pair["full_text"], POOL)


def _accuracy() -> tuple[float, float, list[str]]:
    top1 = top3 = 0
    misses = []
    for pair in PAIRS:
        ranked = _score_pair(pair)
        shortcodes = [c["shortcode"] for c in ranked]
        if shortcodes[:1] == [pair["shortcode"]]:
            top1 += 1
        elif pair["shortcode"] in shortcodes:
            misses.append(f"{pair['shortcode']}: rank {shortcodes.index(pair['shortcode']) + 1}")
        else:
            misses.append(f"{pair['shortcode']}: absent (got {shortcodes[:3]})")
        if pair["shortcode"] in shortcodes:
            top3 += 1
    return top1 / len(PAIRS), top3 / len(PAIRS), misses


def test_fixture_is_real_and_substantial():
    assert len(PAIRS) >= 8, "the suite needs at least 8 real pairs to mean anything"
    assert len(POOL) >= 50, "a small pool would make matching trivially easy"
    for pair in PAIRS:
        assert pair["full_text"].split(), f"{pair['shortcode']} has no fetched text"


def test_top1_accuracy_does_not_regress():
    top1, _, misses = _accuracy()
    assert top1 >= MIN_TOP1_ACCURACY, (
        f"top-1 accuracy {top1:.0%} < {MIN_TOP1_ACCURACY:.0%}; misses: {misses}"
    )


def test_top3_accuracy_does_not_regress():
    _, top3, misses = _accuracy()
    assert top3 >= MIN_TOP3_ACCURACY, (
        f"top-3 accuracy {top3:.0%} < {MIN_TOP3_ACCURACY:.0%}; misses: {misses}"
    )


import pytest


@pytest.mark.xfail(
    strict=True,
    reason="MEASURED: 3 of 12 real pairs would auto-attach to the WRONG reel at "
           "'high' confidence (25% wrong-write rate). This is exactly why "
           "main.AUTO_ATTACH_ENABLED defaults to 0. This test is the GATE: when "
           "it starts passing, auto-attach is safe to enable. strict=True so it "
           "fails loudly the moment it starts passing and this note goes stale.",
)
def test_auto_attach_never_fires_on_a_wrong_row():
    """The safety property that matters most for agent E1: whenever the scorer
    is confident enough to AUTO-COMMIT, it must be right. A high-confidence
    wrong answer is far worse than any number of low-confidence menus."""
    from app.main import AUTO_ATTACH_CONFIDENCE_THRESHOLD

    wrong_auto_attaches = []
    for pair in PAIRS:
        ranked = _score_pair(pair)
        if not ranked:
            continue
        confidence = attach_matching.confidence_for(ranked)
        would_auto = (
            confidence == AUTO_ATTACH_CONFIDENCE_THRESHOLD
            and ranked[0]["match_score"] >= attach_matching.GATE_KEYWORD_MATCH_WEIGHT
        )
        if would_auto and ranked[0]["shortcode"] != pair["shortcode"]:
            wrong_auto_attaches.append(
                f"{pair['shortcode']} -> would auto-attach to {ranked[0]['shortcode']}"
            )
    assert not wrong_auto_attaches, f"auto-attach would commit a WRONG row: {wrong_auto_attaches}"


def test_confidence_is_never_high_without_a_real_lead():
    for pair in PAIRS:
        ranked = _score_pair(pair)
        if len(ranked) < 2:
            continue
        if attach_matching.confidence_for(ranked) == "high":
            gap = ranked[0]["match_score"] - ranked[1]["match_score"]
            assert gap >= attach_matching.HIGH_CONFIDENCE_GAP
