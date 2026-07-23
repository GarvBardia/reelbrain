"""Ephemeral-disk recovery: /attach and /retry fall back to querying Notion
directly (and re-persisting locally) when the local SQLite row is missing —
Render wipes its ephemeral disk on every redeploy, Notion doesn't. All mocked;
no live Notion calls anywhere in this file.
"""
import pytest
from fastapi.testclient import TestClient

from app import main, notion_writer, store
from tests.test_pipeline import FakeClient


def _rt(text):
    return [{"plain_text": text}]


def _saves_page(shortcode, *, status="⏳ Awaiting DM", note="my note about it",
                title="Some Reel Title", gate_keyword="SEND", page_id=None):
    return {
        "id": page_id or f"pg-{shortcode}",
        "url": f"https://notion.so/{page_id or f'pg-{shortcode}'}",
        "properties": {
            "Shortcode": {"rich_text": _rt(shortcode)},
            "Title": {"title": _rt(title)},
            "My note": {"rich_text": _rt(note) if note else []},
            "Status": {"select": {"name": status}},
            "Reel URL": {"url": f"https://www.instagram.com/reel/{shortcode}/"},
            "Gate keyword": {"rich_text": _rt(gate_keyword) if gate_keyword else []},
        },
    }


class _RaisingDataSources:
    """Proves a code path never touches Notion at all — any call fails the test."""

    def query(self, **kwargs):
        raise AssertionError(f"Notion should not have been queried: {kwargs}")


class _FilteringDataSources:
    """A Notion data-source fake that actually respects the query filter, mirroring
    real server-side filtering. find_page_by_shortcode trusts results[0] without
    any client-side re-filtering (unlike the old local Python fallback logic it's
    now used from — see store._resolve_exact_shortcode), so a fake that just
    returns a fixed page list regardless of filter would silently "match" on
    values that aren't the requested shortcode at all."""

    def __init__(self, pages):
        self.pages = pages

    def query(self, **kwargs):
        filt = kwargs.get("filter", {})
        if filt.get("property") == "Shortcode":
            wanted = filt["rich_text"]["equals"]
            return {"results": [
                p for p in self.pages
                if notion_writer.extract_saves_fields(p)["shortcode"] == wanted
            ]}
        return {"results": self.pages}


# =================================================================================
# notion_writer.py: the read-back helpers
# =================================================================================

def test_rt_text_handles_both_shapes():
    assert notion_writer._rt_text([{"plain_text": "hello"}]) == "hello"
    assert notion_writer._rt_text([{"text": {"content": "world"}}]) == "world"
    assert notion_writer._rt_text([]) == ""
    assert notion_writer._rt_text(None) == ""


def test_status_label_from_notion_maps_known_and_unknown():
    assert notion_writer.status_label_from_notion("⏳ Awaiting DM") == "awaiting_dm"
    assert notion_writer.status_label_from_notion("📥 Inbox") == "done"
    assert notion_writer.status_label_from_notion("✅ Processed/Reviewed") is None


def test_extract_saves_fields_pulls_everything_needed():
    page = _saves_page("ABC123", status="⏳ Awaiting DM", note="the ai workflow one",
                       title="AI Workflow Doc", gate_keyword="SEND")
    fields = notion_writer.extract_saves_fields(page)
    assert fields["shortcode"] == "ABC123"
    assert fields["permalink"] == "https://www.instagram.com/reel/ABC123/"
    assert fields["note"] == "the ai workflow one"
    assert fields["status_label"] == "⏳ Awaiting DM"
    assert fields["gate_keyword"] == "SEND"
    assert fields["title"] == "AI Workflow Doc"
    assert fields["page_id"] == "pg-ABC123"
    assert fields["url"] == "https://notion.so/pg-ABC123"


def test_extract_saves_fields_handles_missing_optional_properties():
    page = {"id": "pg-X", "url": "https://notion.so/pg-X", "properties": {
        "Shortcode": {"rich_text": _rt("X")},
    }}
    fields = notion_writer.extract_saves_fields(page)
    assert fields["shortcode"] == "X"
    assert fields["note"] is None
    assert fields["gate_keyword"] is None
    assert fields["permalink"] == ""


def test_find_page_by_shortcode_queries_and_returns_first_match(monkeypatch):
    calls = []

    class _DS:
        def query(self, **kwargs):
            calls.append(kwargs)
            return {"results": [_saves_page("ABC123")]}

    client = FakeClient()
    client.data_sources = _DS()
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    page = notion_writer.find_page_by_shortcode("ABC123")
    assert page["id"] == "pg-ABC123"
    assert calls[0]["filter"] == {"property": "Shortcode", "rich_text": {"equals": "ABC123"}}


def test_find_page_by_shortcode_returns_none_when_empty(monkeypatch):
    client = FakeClient()  # default FakeDataSources().query -> {"results": []}
    monkeypatch.setattr(notion_writer, "_client", lambda: client)
    assert notion_writer.find_page_by_shortcode("NOPE") is None


def test_find_awaiting_dm_pages_filters_by_status_sorted_recent_first(monkeypatch):
    calls = []
    pages = [_saves_page("OLD1"), _saves_page("NEW1")]

    class _DS:
        def query(self, **kwargs):
            calls.append(kwargs)
            return {"results": pages}

    client = FakeClient()
    client.data_sources = _DS()
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    result = notion_writer.find_awaiting_dm_pages()
    assert result == pages
    assert calls[0]["filter"] == {"property": "Status", "select": {"equals": "⏳ Awaiting DM"}}
    assert calls[0]["sorts"] == [{"timestamp": "last_edited_time", "direction": "descending"}]


# =================================================================================
# store.py: upsert_from_notion + the two fallback-aware lookups
# =================================================================================

def test_upsert_from_notion_creates_a_real_local_row():
    row = store.upsert_from_notion(
        shortcode="NEW1", permalink="https://www.instagram.com/reel/NEW1/",
        note="a note", status="awaiting_dm",
        notion_page_id="page-NEW1", notion_page_url="https://notion.so/page-NEW1",
        gate_keyword="SEND",
    )
    assert row["shortcode"] == "NEW1"
    # genuinely persisted, not just returned in-memory
    refetched = store.get_by_shortcode("NEW1")
    assert refetched["notion_page_id"] == "page-NEW1"
    assert refetched["gate_keyword"] == "SEND"


def test_upsert_from_notion_does_not_clobber_existing_note_on_conflict():
    store.insert_processing("DUP1", "https://www.instagram.com/reel/DUP1/", note="original note")
    store.upsert_from_notion(
        shortcode="DUP1", permalink="https://www.instagram.com/reel/DUP1/",
        note="notion's note", status="done",
        notion_page_id="page-DUP1", notion_page_url="https://notion.so/page-DUP1",
    )
    assert store.get_by_shortcode("DUP1")["note"] == "original note"


def test_get_by_shortcode_or_notion_local_hit_never_touches_notion(monkeypatch):
    store.insert_processing("LOCAL1", "https://www.instagram.com/reel/LOCAL1/")
    client = FakeClient()
    client.data_sources = _RaisingDataSources()
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    row = store.get_by_shortcode_or_notion("LOCAL1")
    assert row["shortcode"] == "LOCAL1"


def test_get_by_shortcode_or_notion_falls_back_and_persists(monkeypatch):
    class _DS:
        def query(self, **kwargs):
            return {"results": [_saves_page("REMOTE1", status="⚠️ Failed — retry", note="orig note")]}

    client = FakeClient()
    client.data_sources = _DS()
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    assert store.get_by_shortcode("REMOTE1") is None  # sanity: genuinely absent locally

    row = store.get_by_shortcode_or_notion("REMOTE1")
    assert row["shortcode"] == "REMOTE1"
    assert row["notion_page_id"] == "pg-REMOTE1"
    assert row["permalink"] == "https://www.instagram.com/reel/REMOTE1/"
    assert row["note"] == "orig note"

    # round-trips locally: a second lookup no longer needs Notion at all
    client.data_sources = _RaisingDataSources()
    again = store.get_by_shortcode_or_notion("REMOTE1")
    assert again["shortcode"] == "REMOTE1"


def test_get_by_shortcode_or_notion_both_miss_returns_none(monkeypatch):
    client = FakeClient()  # empty results
    monkeypatch.setattr(notion_writer, "_client", lambda: client)
    assert store.get_by_shortcode_or_notion("GHOST1") is None


def test_get_by_shortcode_or_notion_survives_notion_error(monkeypatch):
    def _boom():
        raise RuntimeError("notion is down")
    monkeypatch.setattr(notion_writer, "_client", _boom)
    assert store.get_by_shortcode_or_notion("WHATEVER") is None


def test_resolve_exact_shortcode_local_hit_never_touches_notion(monkeypatch):
    store.insert_processing("LOCALGATE", "https://www.instagram.com/reel/LOCALGATE/")
    store.update_save("LOCALGATE", status="awaiting_dm")
    client = FakeClient()
    client.data_sources = _RaisingDataSources()
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    exists, row = store.resolve_exact_shortcode("LOCALGATE")
    assert exists is True
    assert row["shortcode"] == "LOCALGATE"


def test_resolve_exact_shortcode_falls_back_by_exact_shortcode(monkeypatch):
    client = FakeClient()
    client.data_sources = _FilteringDataSources(
        [_saves_page("REMOTEGATE1"), _saves_page("REMOTEGATE2")]
    )
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    exists, row = store.resolve_exact_shortcode("REMOTEGATE2")
    assert exists is True
    assert row["shortcode"] == "REMOTEGATE2"


def test_resolve_exact_shortcode_recovers_via_notion_not_local_row(monkeypatch):
    """CRITICAL regression (BUG 3): the target shortcode is absent from local
    SQLite entirely (e.g. ephemeral disk wiped) but IS Awaiting DM in Notion,
    while a totally different row IS locally awaiting_dm. Must resolve to the
    Notion-recovered row for the REQUESTED shortcode — never the local one."""
    store.insert_processing("LOCALDECOY", "https://www.instagram.com/reel/LOCALDECOY/")
    store.update_save("LOCALDECOY", status="awaiting_dm")

    client = FakeClient()
    client.data_sources = _FilteringDataSources([_saves_page("REALTARGET", status="⏳ Awaiting DM")])
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    exists, row = store.resolve_exact_shortcode("REALTARGET")
    assert exists is True
    assert row["shortcode"] == "REALTARGET"


def test_resolve_exact_shortcode_found_but_not_awaiting_never_substitutes(monkeypatch):
    """CRITICAL regression (BUG 3): the target shortcode exists in Notion but
    isn't Awaiting DM (already attached, or never gated), while a different row
    IS locally awaiting_dm. Must return (True, None) — never silently attach
    to the unrelated local row."""
    store.insert_processing("LOCALDECOY2", "https://www.instagram.com/reel/LOCALDECOY2/")
    store.update_save("LOCALDECOY2", status="awaiting_dm")

    client = FakeClient()
    client.data_sources = _FilteringDataSources(
        [_saves_page("ALREADYDONE", status="📥 Inbox")]
    )
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    exists, row = store.resolve_exact_shortcode("ALREADYDONE")
    assert exists is True
    assert row is None


def test_resolve_exact_shortcode_both_miss_returns_exists_false(monkeypatch):
    client = FakeClient()  # empty results everywhere
    monkeypatch.setattr(notion_writer, "_client", lambda: client)
    exists, row = store.resolve_exact_shortcode("anything")
    assert exists is False
    assert row is None


def test_resolve_exact_shortcode_survives_notion_error(monkeypatch):
    """Fails CLOSED: a Notion error means we can't verify one way or the
    other, so the safe answer is "this shortcode is spoken for" (exists=True,
    row=None) — never falling through to guess among unrelated rows."""
    def _boom():
        raise RuntimeError("notion is down")
    monkeypatch.setattr(notion_writer, "_client", _boom)
    exists, row = store.resolve_exact_shortcode("whatever")
    assert exists is True
    assert row is None


# --- get_attach_candidates: Notion-primary, local fallback only on error ------

def test_get_attach_candidates_reads_from_notion(monkeypatch):
    client = FakeClient()
    client.data_sources = _FilteringDataSources(
        [_saves_page("CANDA", note="the ai workflow doc"), _saves_page("CANDB", note="something else")]
    )
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    candidates = store.get_attach_candidates()
    assert {c["shortcode"] for c in candidates} == {"CANDA", "CANDB"}


def test_get_attach_candidates_falls_back_to_local_on_notion_error(monkeypatch):
    def _boom():
        raise RuntimeError("notion is down")
    monkeypatch.setattr(notion_writer, "_client", _boom)
    store.insert_processing("LOCALCAND", "https://www.instagram.com/reel/LOCALCAND/")
    store.update_save("LOCALCAND", status="awaiting_dm")

    candidates = store.get_attach_candidates()
    assert [c["shortcode"] for c in candidates] == ["LOCALCAND"]


def test_resolve_attachable_by_shortcode_recovers_via_notion(monkeypatch):
    client = FakeClient()
    client.data_sources = _FilteringDataSources([_saves_page("REMOTEATTACH", status="⏳ Awaiting DM")])
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    row = store.resolve_attachable_by_shortcode("REMOTEATTACH")
    assert row is not None
    assert row["shortcode"] == "REMOTEATTACH"


def test_resolve_attachable_by_shortcode_rejects_fulfilled_notion_row(monkeypatch):
    page = _saves_page("REMOTEFULFILLED", status="📥 Inbox", gate_keyword="SEND")
    page["properties"]["Gate resource"] = {"url": "https://x.com/already-attached"}
    client = FakeClient()
    client.data_sources = _FilteringDataSources([page])
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    assert store.resolve_attachable_by_shortcode("REMOTEFULFILLED") is None


# =================================================================================
# main.py: the actual /attach and /retry endpoints, end to end
# =================================================================================

def _client(monkeypatch):
    monkeypatch.setattr(main, "CAPTURE_SECRET", "test-secret")
    return TestClient(main.app)


def test_retry_falls_back_to_notion_when_local_row_missing(monkeypatch, tutorial_reel, tutorial_extraction):
    """The DajFASZODlj failure mode: a redeploy wiped SQLite, but the Notion page
    (and thus the row /retry needs) still exists."""
    class _DS:
        def query(self, **kwargs):
            return {"results": [_saves_page(
                "DAJFASZODLJ", status="⚠️ Failed — retry", note="a real note", gate_keyword=None,
            )]}

    fake = FakeClient()
    fake.data_sources = _DS()
    monkeypatch.setattr(notion_writer, "_client", lambda: fake)
    monkeypatch.setattr(main.fetcher, "fetch_reel", lambda s, p: tutorial_reel.model_copy(
        update={"shortcode": s, "permalink": p}
    ))
    monkeypatch.setattr(main.gemini_pipe, "run_extraction", lambda r, n, t: tutorial_extraction)

    client = _client(monkeypatch)
    resp = client.post("/retry/DAJFASZODLJ")

    assert resp.status_code == 202
    assert resp.json()["shortcode"] == "DAJFASZODLJ"
    # the row now exists locally, having run the real pipeline against the
    # permalink recovered from Notion
    row = store.get_by_shortcode("DAJFASZODLJ")
    assert row is not None
    assert row["status"] == "done"


def test_retry_still_404s_when_neither_sqlite_nor_notion_has_it(monkeypatch):
    fake = FakeClient()  # empty everywhere
    monkeypatch.setattr(notion_writer, "_client", lambda: fake)
    client = _client(monkeypatch)

    resp = client.post("/retry/TOTALLYUNKNOWN")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "unknown shortcode"


def test_attach_falls_back_to_notion_when_local_row_missing(monkeypatch):
    """The DZSFkNppVW_ failure mode: /attach's shortcode_or_note matches nothing
    locally because SQLite was wiped, but Notion still has the Awaiting DM page."""
    class _DS:
        def query(self, **kwargs):
            return {"results": [_saves_page(
                "DZSFKNPPVW", status="⏳ Awaiting DM", note="the resource drop", gate_keyword="LINK",
            )]}

    fake = FakeClient()
    fake.data_sources = _DS()
    monkeypatch.setattr(notion_writer, "_client", lambda: fake)
    client = _client(monkeypatch)

    resp = client.post("/attach", json={
        "shortcode_or_note": "DZSFKNPPVW",
        "resource_url": "https://instagram.com/direct/t/12345/",
        "secret": "test-secret",
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "NOTIFY"
    assert "DZSFKNPPVW" in body["message"]

    # SQLite update actually happened against the recovered row
    row = store.get_by_shortcode("DZSFKNPPVW")
    assert row["status"] == "done"
    assert row["gate_resource_url"] == "https://instagram.com/direct/t/12345/"

    # Notion was updated too, on the page_id recovered from the fallback query
    update_call = fake.pages.updated[0]
    assert update_call["page_id"] == "pg-DZSFKNPPVW"
    assert update_call["properties"]["Status"]["select"]["name"] == "📥 Inbox"


def test_attach_still_unresolved_when_neither_sqlite_nor_notion_has_it(monkeypatch):
    """Flattened response (see PROGRESS.md): "unresolved" is a business
    outcome, not an error -- always HTTP 200 now."""
    fake = FakeClient()  # empty everywhere
    monkeypatch.setattr(notion_writer, "_client", lambda: fake)
    client = _client(monkeypatch)

    resp = client.post("/attach", json={
        "shortcode_or_note": None,
        "resource_url": "https://instagram.com/direct/t/1/",
        "secret": "test-secret",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "NOTIFY"
    assert body["menu_items"] == []


def test_attach_round_trip_second_call_hits_locally_no_notion_query(monkeypatch):
    """After the Notion-fallback attach, a second /attach for a DIFFERENT gated
    row must not accidentally reuse stale fallback state — and the first row's
    local data must be exactly what a normal local /attach would have produced."""
    class _DS:
        def query(self, **kwargs):
            return {"results": [_saves_page("ROUNDTRIP1", status="⏳ Awaiting DM", note="n1")]}

    fake = FakeClient()
    fake.data_sources = _DS()
    monkeypatch.setattr(notion_writer, "_client", lambda: fake)
    client = _client(monkeypatch)

    first = client.post("/attach", json={
        "shortcode_or_note": "ROUNDTRIP1", "resource_url": "https://x.com/r1", "secret": "test-secret",
    })
    assert first.status_code == 200

    # now local-only: raising data_sources proves the second lookup for the SAME
    # shortcode doesn't need Notion anymore (it's status 'done' now, so it won't
    # match find_pending_gate's awaiting_dm search at all — confirms cleanly)
    fake.data_sources = _RaisingDataSources()
    row = store.get_by_shortcode("ROUNDTRIP1")
    assert row["status"] == "done"
    assert row["gate_resource_url"] == "https://x.com/r1"
