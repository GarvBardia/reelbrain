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


def test_taxonomy_delegates_to_live_notion_taxonomy(monkeypatch):
    """INCIDENT (2026-08-09): get_taxonomy used to read the local `tags` table
    directly, which is wiped on every redeploy and had 4 rows against 212 real
    rows in Notion — silently starving every extraction call's candidate list
    and collapsing the taxonomy to ~190 near-singletons. It's now a thin
    wrapper delegating to notion_writer.get_live_taxonomy (the durable,
    Notion-backed source) — see that function's own tests in
    test_notion_writer.py for the frequency/merge/exclusion/caching behavior
    itself."""
    calls = []

    def _fake_live_taxonomy(limit):
        calls.append(limit)
        return ["ai-workflows", "fitness"]

    monkeypatch.setattr(notion_writer, "get_live_taxonomy", _fake_live_taxonomy)

    taxonomy = store.get_taxonomy(limit=40)

    assert taxonomy == ["ai-workflows", "fitness"]
    assert calls == [40]


def test_daily_fetch_count_and_record_fetch():
    assert store.get_daily_fetch_count() == 0
    store.record_fetch()
    store.record_fetch()
    assert store.get_daily_fetch_count() == 2
    assert store.get_last_fetch_at() is not None


def _seed_gate(shortcode: str, note: str | None = None, main_point: str | None = None,
               gate_keyword: str | None = None) -> None:
    store.insert_processing(shortcode, f"https://www.instagram.com/reel/{shortcode}/", note=note)
    fields = {"status": "awaiting_dm"}
    if main_point is not None:
        fields["extraction_json"] = json.dumps({"main_point": main_point})
    if gate_keyword is not None:
        fields["gate_keyword"] = gate_keyword
    store.update_save(shortcode, **fields)


# --- resolve_exact_shortcode: the ONLY auto-commit path (see PROGRESS.md) ------
#
# The substring/"sole Awaiting DM row" fallback tiers were REMOVED entirely
# after a real cross-attachment (a resource landed on a different,
# coincidentally-similar-sounding reel, reported as a genuine "success" — no
# ambiguity was ever detected because there was only one candidate). Anything
# short of an exact shortcode now goes through store.get_attach_candidates() +
# app/attach_matching.py's scoring instead (tested separately below), which
# never auto-commits.

def test_exact_shortcode_match_resolves_regardless_of_other_rows():
    """An exact shortcode match is always unambiguous (shortcode is a primary
    key) regardless of how many other rows are simultaneously awaiting_dm."""
    _seed_gate("EXACT1")
    _seed_gate("EXACT2")
    _seed_gate("EXACT3")

    exists, row = store.resolve_exact_shortcode("EXACT2")
    assert exists is True
    assert row["shortcode"] == "EXACT2"


def test_exact_shortcode_not_found_anywhere_returns_exists_false():
    _seed_gate("SOMEROW")
    exists, row = store.resolve_exact_shortcode("does-not-exist-anywhere")
    assert exists is False
    assert row is None


# --- resolve_exact_shortcode: NEVER resolves to a different row ---------------
#
# CRITICAL incident (BUG 3, still the same non-negotiable safety property post-
# redesign): /attach with an explicit shortcode_or_note that IS a real
# shortcode must resolve to THAT row, or to nothing — never a substitute.

def test_exact_shortcode_not_awaiting_dm_never_substitutes_a_different_row():
    """The precise incident shape: the requested shortcode's row EXISTS locally
    but isn't awaiting_dm (e.g. already attached, or never gated), while a
    totally unrelated OTHER row is the sole awaiting_dm entry. Must return
    (True, None) — never silently substitute the unrelated row."""
    store.insert_processing("TARGET1", "https://www.instagram.com/reel/TARGET1/")
    store.update_save("TARGET1", status="done")  # not awaiting_dm
    _seed_gate("UNRELATED1")  # the only awaiting_dm row

    exists, row = store.resolve_exact_shortcode("TARGET1")
    assert exists is True
    assert row is None


def test_exact_shortcode_not_awaiting_dm_never_substitutes_among_many_others():
    """Same as above, but with several OTHER awaiting_dm rows, to prove the
    explicit shortcode still refuses to guess even when there are many other
    candidates it could theoretically fall back to."""
    store.insert_processing("TARGET2", "https://www.instagram.com/reel/TARGET2/")
    store.update_save("TARGET2", status="failed")
    _seed_gate("UNRELATED2")
    _seed_gate("UNRELATED3")

    exists, row = store.resolve_exact_shortcode("TARGET2")
    assert exists is True
    assert row is None


def test_exact_shortcode_awaiting_dm_wins_even_with_other_rows_present():
    """Sanity companion: when the requested shortcode genuinely IS awaiting_dm,
    it must still resolve correctly regardless of other awaiting_dm rows."""
    _seed_gate("REALTARGET")
    _seed_gate("OTHERROW")

    exists, row = store.resolve_exact_shortcode("REALTARGET")
    assert exists is True
    assert row["shortcode"] == "REALTARGET"


# --- get_attach_candidates / resolve_attachable_by_shortcode ------------------

def test_local_attach_candidates_includes_awaiting_dm_rows():
    _seed_gate("CAND1", note="the ai workflow doc", main_point="AI Workflow Guide")
    candidates = store._local_attach_candidates()
    assert [c["shortcode"] for c in candidates] == ["CAND1"]
    assert candidates[0]["title"] == "AI Workflow Guide"
    assert candidates[0]["note"] == "the ai workflow doc"


def test_local_attach_candidates_includes_inbox_rows_with_unfulfilled_keyword():
    """The BUG2 edge case: a row routed to Inbox despite having a gate_keyword
    (keyword set without detected flipping true) must still be a candidate —
    it has an unfulfilled gate even though its status isn't awaiting_dm."""
    store.insert_processing("INBOXKW", "https://www.instagram.com/reel/INBOXKW/")
    store.update_save("INBOXKW", status="done", gate_keyword="SEND")
    candidates = store._local_attach_candidates()
    assert [c["shortcode"] for c in candidates] == ["INBOXKW"]


def test_local_attach_candidates_excludes_fulfilled_or_plain_inbox_rows():
    store.insert_processing("FULFILLED", "https://www.instagram.com/reel/FULFILLED/")
    store.update_save("FULFILLED", status="done", gate_keyword="SEND", gate_resource_url="https://x.com/r")
    store.insert_processing("PLAININBOX", "https://www.instagram.com/reel/PLAININBOX/")
    store.update_save("PLAININBOX", status="done")
    assert store._local_attach_candidates() == []


def test_resolve_attachable_by_shortcode_accepts_awaiting_dm():
    _seed_gate("ATTACHABLE1")
    row = store.resolve_attachable_by_shortcode("ATTACHABLE1")
    assert row is not None
    assert row["shortcode"] == "ATTACHABLE1"


def test_resolve_attachable_by_shortcode_accepts_inbox_with_unfulfilled_keyword():
    store.insert_processing("ATTACHABLE2", "https://www.instagram.com/reel/ATTACHABLE2/")
    store.update_save("ATTACHABLE2", status="done", gate_keyword="SEND")
    row = store.resolve_attachable_by_shortcode("ATTACHABLE2")
    assert row is not None


def test_resolve_attachable_by_shortcode_rejects_fulfilled_row():
    store.insert_processing("DONE1", "https://www.instagram.com/reel/DONE1/")
    store.update_save("DONE1", status="done", gate_keyword="SEND", gate_resource_url="https://x.com/r")
    assert store.resolve_attachable_by_shortcode("DONE1") is None


def test_resolve_attachable_by_shortcode_rejects_never_gated_row():
    """Mirrors the Da8IIonEhGR incident shape directly: a row that exists but
    was never gated at all (no keyword, plain Inbox/other status) must never
    be accepted as an attach target, even by exact shortcode."""
    store.insert_processing("NEVERGATED", "https://www.instagram.com/reel/NEVERGATED/")
    store.update_save("NEVERGATED", status="photo_manual")
    assert store.resolve_attachable_by_shortcode("NEVERGATED") is None
