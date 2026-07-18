import json

import pytest

from app import notion_writer, store
from tests.test_pipeline import FakeClient


@pytest.fixture(autouse=True)
def _empty_notion_by_default(monkeypatch):
    """find_pending_gate's exact-shortcode check (the BUG 3 fix, see
    store._resolve_exact_shortcode) now always tries Notion directly whenever
    a shortcode_or_note doesn't match anything locally. This file is about the
    LOCAL substring/ambiguity logic, so give it an empty Notion by default —
    the Notion-fallback behavior itself is covered in test_notion_fallback.py."""
    monkeypatch.setattr(notion_writer, "_client", lambda: FakeClient())


def test_insert_and_get_by_shortcode():
    store.insert_processing("ABC123", "https://www.instagram.com/reel/ABC123/", note="hi")
    row = store.get_by_shortcode("ABC123")
    assert row is not None
    assert row["status"] == "processing"
    assert row["note"] == "hi"


def test_get_by_shortcode_missing_returns_none():
    assert store.get_by_shortcode("does-not-exist") is None


def test_dedupe_flow_marks_existing_notion_page():
    store.insert_processing("DUP001", "https://www.instagram.com/reel/DUP001/")
    store.update_save("DUP001", status="done", notion_page_id="page-1", notion_page_url="https://notion.so/page-1")

    row = store.get_by_shortcode("DUP001")
    assert row["notion_page_id"] == "page-1"
    # This is exactly what /capture checks to decide "duplicate, don't re-process"
    assert bool(row["notion_page_id"]) is True


def test_update_save_is_partial():
    store.insert_processing("PART001", "https://www.instagram.com/reel/PART001/")
    store.update_save("PART001", status="failed")
    row = store.get_by_shortcode("PART001")
    assert row["status"] == "failed"
    assert row["permalink"] == "https://www.instagram.com/reel/PART001/"


def test_taxonomy_orders_by_frequency():
    store.insert_processing("T1", "https://www.instagram.com/reel/T1/")
    store.insert_processing("T2", "https://www.instagram.com/reel/T2/")
    store.insert_processing("T3", "https://www.instagram.com/reel/T3/")
    store.set_tags("T1", ["ai-workflows", "productivity"])
    store.set_tags("T2", ["ai-workflows"])
    store.set_tags("T3", ["fitness"])

    taxonomy = store.get_taxonomy(limit=40)
    assert taxonomy[0] == "ai-workflows"
    assert "fitness" in taxonomy


def test_daily_fetch_count_and_record_fetch():
    assert store.get_daily_fetch_count() == 0
    store.record_fetch()
    store.record_fetch()
    assert store.get_daily_fetch_count() == 2
    assert store.get_last_fetch_at() is not None


def test_get_most_recent_awaiting_dm():
    store.insert_processing("G1", "https://www.instagram.com/reel/G1/")
    store.update_save("G1", status="awaiting_dm")
    row = store.get_most_recent_awaiting_dm()
    assert row["shortcode"] == "G1"


# --- find_pending_gate: refuse to guess on ambiguity (safety fix) --------------
#
# Real incident: with several rows simultaneously Awaiting DM, the note/title
# substring fallback was too loose and attached a resource to the wrong pending
# entry. These are the two exact scenarios that must now 409 instead of guessing.

def _seed_gate(shortcode: str, note: str | None = None, main_point: str | None = None) -> None:
    store.insert_processing(shortcode, f"https://www.instagram.com/reel/{shortcode}/", note=note)
    fields = {"status": "awaiting_dm"}
    if main_point is not None:
        fields["extraction_json"] = json.dumps({"main_point": main_point})
    store.update_save(shortcode, **fields)


def test_omitted_shortcode_with_three_awaiting_dm_rows_refuses_to_guess():
    """Requirement: 3 awaiting_dm rows, omitted shortcode_or_note -> ambiguous,
    listing all 3 — never a silent pick of one (e.g. 'most recent')."""
    _seed_gate("ROW1")
    _seed_gate("ROW2")
    _seed_gate("ROW3")

    with pytest.raises(store.AmbiguousGateMatch) as exc_info:
        store.find_pending_gate(None)

    assert set(exc_info.value.candidates) == {"ROW1", "ROW2", "ROW3"}
    assert len(exc_info.value.candidates) == 3
    # no side effects — every row must still be untouched, still awaiting_dm
    for shortcode in ("ROW1", "ROW2", "ROW3"):
        assert store.get_by_shortcode(shortcode)["status"] == "awaiting_dm"


def test_two_rows_sharing_a_word_in_note_refuses_to_guess():
    """Requirement: 2 rows both containing the same word in their note ->
    ambiguous, not a silent first-match."""
    _seed_gate("NOTEROW1", note="check out this ai workflow doc from Jane")
    _seed_gate("NOTEROW2", note="another ai workflow tip from Bob")

    with pytest.raises(store.AmbiguousGateMatch) as exc_info:
        store.find_pending_gate("ai workflow")

    assert set(exc_info.value.candidates) == {"NOTEROW1", "NOTEROW2"}


def test_two_rows_sharing_a_word_in_title_refuses_to_guess():
    """Same as above, but the shared word is in the title (extraction_json's
    main_point) rather than the note — the local title-derived match."""
    _seed_gate("TITLEROW1", main_point="The Growth Hacking Playbook")
    _seed_gate("TITLEROW2", main_point="Another Growth Hacking Guide")

    with pytest.raises(store.AmbiguousGateMatch) as exc_info:
        store.find_pending_gate("growth hacking")

    assert set(exc_info.value.candidates) == {"TITLEROW1", "TITLEROW2"}


def test_exact_shortcode_match_is_never_ambiguous_even_with_many_rows():
    """An exact shortcode match is always unambiguous (shortcode is a primary
    key) regardless of how many other rows are simultaneously awaiting_dm."""
    _seed_gate("EXACT1")
    _seed_gate("EXACT2")
    _seed_gate("EXACT3")

    row = store.find_pending_gate("EXACT2")
    assert row["shortcode"] == "EXACT2"


def test_substring_match_unique_among_many_still_auto_picks():
    """A substring match that's unique — even with several OTHER unrelated rows
    also awaiting_dm — is safe to auto-pick; ambiguity is about the MATCH count,
    not the total row count."""
    _seed_gate("UNIQUE1", note="the only one mentioning pineapple recipes")
    _seed_gate("OTHER1", note="something about sleep")
    _seed_gate("OTHER2", note="something about finance")

    row = store.find_pending_gate("pineapple")
    assert row["shortcode"] == "UNIQUE1"


def test_omitted_shortcode_with_single_row_still_auto_picks():
    """Only auto-pick when exactly one row is in awaiting_dm."""
    _seed_gate("SOLE1")
    row = store.find_pending_gate(None)
    assert row["shortcode"] == "SOLE1"


# --- find_pending_gate: exact shortcode NEVER resolves to a different row -----
#
# CRITICAL incident (BUG 3): /attach with an explicit shortcode_or_note=
# "DZSFkNppVW_" landed the resource on a completely unrelated row
# (Dap3IoNo4Kt) instead. Root cause: the exact-shortcode check was scoped to
# only rows already in `awaiting_dm` status. If the requested row wasn't in
# that set for any reason, the exact-match search silently found nothing and
# execution fell through to the "single remaining awaiting_dm row" auto-pick
# meant only for an OMITTED shortcode_or_note. These tests reproduce that
# exact shape locally and assert the fixed, non-negotiable safety property:
# an explicit exact shortcode resolves to THAT row, or to nothing — never a
# substitute.

def test_exact_shortcode_not_awaiting_dm_never_substitutes_a_different_row():
    """The precise incident shape: the requested shortcode's row EXISTS locally
    but isn't awaiting_dm (e.g. already attached, or never gated), while a
    totally unrelated OTHER row is the sole awaiting_dm entry. Must return
    None (404) — never silently attach to the unrelated row."""
    store.insert_processing("TARGET1", "https://www.instagram.com/reel/TARGET1/")
    store.update_save("TARGET1", status="done")  # not awaiting_dm
    _seed_gate("UNRELATED1")  # the only awaiting_dm row

    row = store.find_pending_gate("TARGET1")
    assert row is None


def test_exact_shortcode_not_awaiting_dm_never_substitutes_among_many_others():
    """Same as above, but with several OTHER awaiting_dm rows (the ambiguous-
    fallback path) to prove the explicit shortcode still refuses to guess even
    when the fallback-if-omitted logic would otherwise raise/pick among them."""
    store.insert_processing("TARGET2", "https://www.instagram.com/reel/TARGET2/")
    store.update_save("TARGET2", status="failed")
    _seed_gate("UNRELATED2")
    _seed_gate("UNRELATED3")

    row = store.find_pending_gate("TARGET2")
    assert row is None


def test_exact_shortcode_awaiting_dm_wins_even_with_other_rows_present():
    """Sanity companion: when the requested shortcode genuinely IS awaiting_dm,
    it must still resolve correctly regardless of other awaiting_dm rows."""
    _seed_gate("REALTARGET")
    _seed_gate("OTHERROW")

    row = store.find_pending_gate("REALTARGET")
    assert row["shortcode"] == "REALTARGET"


def test_shortcode_missing_everywhere_falls_through_to_substring_as_before():
    """A value that isn't a real shortcode anywhere (local or Notion) must still
    work as a plain note/title search fragment — the fix must not break the
    ordinary substring-match use case."""
    _seed_gate("SUBROW1", note="the ai workflow doc one")
    row = store.find_pending_gate("ai workflow")
    assert row["shortcode"] == "SUBROW1"
