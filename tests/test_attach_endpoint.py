import json

from fastapi.testclient import TestClient

from app import main, notion_writer, resource_lookup, store
from tests.test_pipeline import FakeClient

RESOURCE_URL = "https://instagram.com/direct/t/17800000000000000/"


def _client(monkeypatch):
    monkeypatch.setattr(main, "CAPTURE_SECRET", "test-secret")
    fake = FakeClient()
    monkeypatch.setattr(notion_writer, "_client", lambda: fake)
    # Every candidate-resolution test controls this explicitly; default to
    # "nothing fetched" so tests that don't care about scoring aren't
    # accidentally sensitive to whatever the (blocked) real httpx.get would do.
    monkeypatch.setattr(resource_lookup, "fetch_resource_title_and_description", lambda url: ("", ""))
    return TestClient(main.app), fake


def _seed_awaiting_dm(shortcode: str, note: str | None = None) -> None:
    store.insert_processing(shortcode, f"https://www.instagram.com/reel/{shortcode}/", note=note)
    store.update_save(
        shortcode,
        status="awaiting_dm",
        gate_keyword="SEND",
        notion_page_id=f"page-{shortcode}",
        notion_page_url=f"https://notion.so/page-{shortcode}",
    )


def _rt(text):
    return [{"plain_text": text}]


def _candidate_page(shortcode, *, status="⏳ Awaiting DM", main_point="Some point",
                     gate_keyword="SEND", topics=(), created="2026-07-20T00:00:00.000Z"):
    """A Notion-shaped page for get_attach_candidates()'s Notion-primary
    query — the FakeClient's default data_sources always returns empty
    results regardless of filter, so tests exercising candidate scoring at
    the HTTP layer need to install a fake that actually returns these."""
    return {
        "id": f"pg-{shortcode}",
        "url": f"https://notion.so/pg-{shortcode}",
        "created_time": created,
        "properties": {
            "Shortcode": {"rich_text": _rt(shortcode)},
            "Title": {"title": _rt(main_point)},
            "My note": {"rich_text": []},
            "Status": {"select": {"name": status}},
            "Reel URL": {"url": f"https://www.instagram.com/reel/{shortcode}/"},
            "Gate keyword": {"rich_text": _rt(gate_keyword) if gate_keyword else []},
            "Gate resource": {"url": None},
            "Topics": {"multi_select": [{"name": t} for t in topics]},
        },
    }


class _CandidateDataSources:
    def __init__(self, pages):
        self.pages = pages

    def query(self, **kwargs):
        return {"results": self.pages}


def _seed_photo_manual(shortcode: str) -> None:
    """A photo/carousel placeholder that never had a caption recovered — its
    comment-gate (if the real Instagram post has one) was never
    programmatically detected, so it never passed through Awaiting DM at all.
    Mirrors Da8IIonEhGR's real live state exactly (verified via a live
    /attach reproduction against the deployed app, see PROGRESS.md)."""
    store.insert_processing(shortcode, f"https://www.instagram.com/reel/{shortcode}/")
    store.update_save(
        shortcode,
        status="photo_manual",
        notion_page_id=f"page-{shortcode}",
        notion_page_url=f"https://notion.so/page-{shortcode}",
    )


def test_attach_rejects_bad_secret(monkeypatch):
    client, _ = _client(monkeypatch)
    resp = client.post("/attach", json={"shortcode_or_note": None, "resource_url": RESOURCE_URL, "secret": "wrong"})
    assert resp.status_code == 401


# --- tier 1: exact shortcode is the ONLY auto-commit path ----------------------

def test_attach_by_explicit_shortcode(monkeypatch):
    client, fake = _client(monkeypatch)
    _seed_awaiting_dm("GATE001")

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": "GATE001", "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "action": "NOTIFY",
        "message": "✅ Attached to GATE001: https://notion.so/page-GATE001",
        "menu_items": [],
    }

    row = store.get_by_shortcode("GATE001")
    assert row["status"] == "done"
    assert row["gate_resource_url"] == RESOURCE_URL

    assert len(fake.pages.updated) == 1
    update_call = fake.pages.updated[0]
    assert update_call["page_id"] == "page-GATE001"
    assert update_call["properties"]["Status"]["select"]["name"] == "📥 Inbox"
    assert update_call["properties"]["Gate resource"]["url"] == RESOURCE_URL


def test_attach_explicit_shortcode_on_never_gated_row_404s_not_a_wrong_row(monkeypatch):
    """Case 1 (Da8IIonEhGR) reproduced: an explicit shortcode targeting a row
    that exists but was never Awaiting DM (a photo/carousel placeholder whose
    gate, if any, was never detected) must 404 — and must NEVER fall through
    to a different, unrelated Awaiting DM row."""
    client, fake = _client(monkeypatch)
    _seed_photo_manual("PHOTOMANUAL1")
    _seed_awaiting_dm("UNRELATED1")  # a totally different, genuinely-pending gate

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": "PHOTOMANUAL1", "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 200  # flattened: a business outcome, not an error (see PROGRESS.md)
    body = resp.json()
    assert body["action"] == "NOTIFY"
    assert body["menu_items"] == []
    # critically: the unrelated pending row must be untouched — no silent
    # substitution just because something else happened to be Awaiting DM
    assert store.get_by_shortcode("UNRELATED1")["status"] == "awaiting_dm"
    assert store.get_by_shortcode("PHOTOMANUAL1")["status"] == "photo_manual"


# --- requirement 2: /attach must NEVER report success without a real write ----

def test_attach_notion_failure_now_reports_failure_not_false_success(monkeypatch):
    """The redesigned behavior (see PROGRESS.md): the OLD code updated local
    SQLite first and swallowed a Notion failure, returning 200 regardless —
    on Render's ephemeral disk that "success" could vanish with zero durable
    trace, exactly the shape suspected for the Da8IIonEhGR incident. Now: a
    Notion write failure must return a real error, and local status must NOT
    be flipped to done (no lying about state)."""
    client, fake = _client(monkeypatch)
    _seed_awaiting_dm("NOTIONDOWN")

    def _boom(**kwargs):
        raise RuntimeError("notion is down")

    fake.pages.update = _boom

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": "NOTIONDOWN", "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 502
    assert resp.json()["detail"]["status"] == "failed"
    assert store.get_by_shortcode("NOTIONDOWN")["status"] == "awaiting_dm"  # NOT flipped to done
    assert store.get_by_shortcode("NOTIONDOWN")["gate_resource_url"] is None


def test_attach_missing_notion_page_id_reports_failure_not_false_success(monkeypatch):
    """Another shape of the same requirement: if no notion_page_id can be
    resolved at all (locally or via Notion), there is no durable write to
    make — must fail cleanly, never report "attached" for a write that only
    ever happened in ephemeral local SQLite."""
    client, fake = _client(monkeypatch)
    store.insert_processing("NOPAGEID", "https://www.instagram.com/reel/NOPAGEID/")
    store.update_save("NOPAGEID", status="awaiting_dm")  # no notion_page_id set
    fake.data_sources = type("Empty", (), {"query": lambda self, **kw: {"results": []}})()

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": "NOPAGEID", "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 502
    assert store.get_by_shortcode("NOPAGEID")["status"] == "awaiting_dm"


# --- tier 2: candidate scoring, never an auto-commit --------------------------

def test_attach_no_exact_shortcode_zero_candidates_is_a_clear_failure(monkeypatch):
    """Zero pending gates at all -> a clear 'unresolved' 404, never a false
    success and never an auto-pick."""
    client, _ = _client(monkeypatch)
    resp = client.post(
        "/attach",
        json={"shortcode_or_note": None, "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 200  # flattened business outcome
    body = resp.json()
    assert body["action"] == "NOTIFY"
    assert body["menu_items"] == []


def test_attach_no_exact_shortcode_below_threshold_is_unresolved_not_a_guess(monkeypatch):
    """Pending gates exist, but nothing about the resource matches any of
    them — must still refuse to guess, not fall back to 'the only one' or
    'the most recent'. This is exactly the old auto-pick fallback's failure
    mode, now closed."""
    client, fake = _client(monkeypatch)
    _seed_awaiting_dm("SOLE001", note="completely unrelated content")
    fake.data_sources = _CandidateDataSources([
        _candidate_page("SOLE001", main_point="A cooking recipe video about pasta"),
    ])
    monkeypatch.setattr(
        resource_lookup, "fetch_resource_title_and_description",
        lambda url: ("Quarterly financial spreadsheet template", "budget planning workbook download"),
    )

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": None, "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 200  # flattened business outcome
    body = resp.json()
    assert body["action"] == "NOTIFY"
    assert body["menu_items"] == []
    assert store.get_by_shortcode("SOLE001")["status"] == "awaiting_dm"  # untouched


def test_attach_no_exact_shortcode_returns_ranked_candidates_with_real_detail(monkeypatch):
    """Case 2 (DbFDY3yTwlI/Higgsfield) reproduced under the NEW design: two
    rows both mention "Higgsfield", scored against the resource's fetched
    title/description. Both are real candidates -- the endpoint must return
    them for a human to pick, with enough distinguishing detail to actually
    tell them apart (shortcode, created date, topics, main_point snippet) --
    NOT three identically-labeled options, and NEVER an auto-commit."""
    client, fake = _client(monkeypatch)

    # Local rows so the final "never touched" assertions have something to
    # check against; the candidate POOL itself comes from the Notion-primary
    # query, so the fake data source below is what actually drives scoring.
    store.insert_processing("REALTARGET", "https://www.instagram.com/reel/REALTARGET/", note=None)
    store.update_save("REALTARGET", status="done", notion_page_id="pg-REALTARGET")
    store.insert_processing("COINCIDENCE", "https://www.instagram.com/reel/COINCIDENCE/", note=None)
    store.update_save("COINCIDENCE", status="awaiting_dm", notion_page_id="pg-COINCIDENCE")

    fake.data_sources = _CandidateDataSources([
        _candidate_page(
            "REALTARGET", status="📥 Inbox", gate_keyword="VIDEO",
            main_point="Higgsfield is offering 24 hours of free access.",
            topics=("ai-tools", "higgsfield"), created="2026-07-20T00:00:00.000Z",
        ),
        _candidate_page(
            "COINCIDENCE", status="⏳ Awaiting DM", gate_keyword="SEND",
            main_point="An app built with Higgsfield AI applies filters.",
            topics=("ai-tools", "filters"), created="2026-07-21T00:00:00.000Z",
        ),
    ])

    monkeypatch.setattr(
        resource_lookup, "fetch_resource_title_and_description",
        lambda url: ("Higgsfield filmmaker grant announcement", "Details about the Higgsfield AI filmmaker grant program"),
    )

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": None, "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 200  # flattened business outcome
    body = resp.json()
    assert body["action"] == "MENU"
    assert body["message"]

    menu_items = body["menu_items"]
    assert len(menu_items) == 2
    # main_point FIRST and prominent, shortcode reduced to a trailing suffix
    # (see PROGRESS.md) -- each item is "<main_point> | <shortcode>", split on
    # "|" taking the LAST segment to recover the shortcode for /attach/confirm.
    by_shortcode = {item.rsplit("|", 1)[1].strip(): item for item in menu_items}
    assert set(by_shortcode) == {"REALTARGET", "COINCIDENCE"}
    assert by_shortcode["REALTARGET"].startswith("Higgsfield is offering")
    assert by_shortcode["COINCIDENCE"].startswith("An app built with Higgsfield")

    # NEVER an auto-commit -- neither row touched
    assert store.get_by_shortcode("REALTARGET")["gate_resource_url"] is None
    assert store.get_by_shortcode("COINCIDENCE")["gate_resource_url"] is None


# --- /attach/confirm: the second step that actually commits -------------------

def test_attach_confirm_commits_the_chosen_candidate(monkeypatch):
    client, fake = _client(monkeypatch)
    _seed_awaiting_dm("PICKME")

    resp = client.post(
        "/attach/confirm",
        json={"shortcode": "PICKME", "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "action": "NOTIFY",
        "message": "✅ Attached to PICKME: https://notion.so/page-PICKME",
        "menu_items": [],
    }
    assert store.get_by_shortcode("PICKME")["status"] == "done"
    assert store.get_by_shortcode("PICKME")["gate_resource_url"] == RESOURCE_URL


def test_attach_confirm_rejects_a_shortcode_that_isnt_a_pending_target(monkeypatch):
    client, _ = _client(monkeypatch)
    store.insert_processing("NOTPENDING", "https://www.instagram.com/reel/NOTPENDING/")
    store.update_save("NOTPENDING", status="done")

    resp = client.post(
        "/attach/confirm",
        json={"shortcode": "NOTPENDING", "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 200  # flattened business outcome
    body = resp.json()
    assert body["action"] == "NOTIFY"
    assert body["menu_items"] == []


def test_attach_confirm_notion_failure_reports_failure_not_false_success(monkeypatch):
    client, fake = _client(monkeypatch)
    _seed_awaiting_dm("CONFIRMFAIL")

    def _boom(**kwargs):
        raise RuntimeError("notion is down")

    fake.pages.update = _boom

    resp = client.post(
        "/attach/confirm",
        json={"shortcode": "CONFIRMFAIL", "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 502
    assert store.get_by_shortcode("CONFIRMFAIL")["status"] == "awaiting_dm"


def test_attach_confirm_accepts_inbox_row_with_unfulfilled_keyword(monkeypatch):
    """The broadened candidate pool (BUG2 edge case): a row routed to Inbox
    despite having a real gate_keyword must still be confirmable."""
    client, fake = _client(monkeypatch)
    store.insert_processing("INBOXKW", "https://www.instagram.com/reel/INBOXKW/")
    store.update_save(
        "INBOXKW", status="done", gate_keyword="SEND",
        notion_page_id="page-INBOXKW", notion_page_url="https://notion.so/page-INBOXKW",
    )

    resp = client.post(
        "/attach/confirm",
        json={"shortcode": "INBOXKW", "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 200
    assert store.get_by_shortcode("INBOXKW")["gate_resource_url"] == RESOURCE_URL


# --- flattened responses: outcome is now only distinguishable via logging -----
#
# Every business outcome is HTTP 200 now (see PROGRESS.md), so the status
# code alone can no longer tell "attached" apart from "needs_confirmation"/
# "not_found"/"unresolved" in server logs — each outcome must log its own
# resolved status explicitly.

import logging as _logging


def test_logs_attached_outcome(monkeypatch, caplog):
    client, _fake = _client(monkeypatch)
    _seed_awaiting_dm("LOGATTACH")
    with caplog.at_level(_logging.INFO):
        client.post("/attach", json={
            "shortcode_or_note": "LOGATTACH", "resource_url": RESOURCE_URL, "secret": "test-secret",
        })
    assert any("attach resolved: attached" in r.message for r in caplog.records)


def test_logs_not_found_outcome(monkeypatch, caplog):
    client, _fake = _client(monkeypatch)
    _seed_photo_manual("LOGNOTFOUND")
    with caplog.at_level(_logging.INFO):
        client.post("/attach", json={
            "shortcode_or_note": "LOGNOTFOUND", "resource_url": RESOURCE_URL, "secret": "test-secret",
        })
    assert any("attach resolved: not_found" in r.message for r in caplog.records)


def test_logs_unresolved_outcome(monkeypatch, caplog):
    client, _fake = _client(monkeypatch)
    with caplog.at_level(_logging.INFO):
        client.post("/attach", json={
            "shortcode_or_note": None, "resource_url": RESOURCE_URL, "secret": "test-secret",
        })
    assert any("attach resolved: unresolved" in r.message for r in caplog.records)


def test_logs_needs_confirmation_outcome(monkeypatch, caplog):
    client, fake = _client(monkeypatch)
    store.insert_processing("LOGCAND", "https://www.instagram.com/reel/LOGCAND/", note=None)
    store.update_save("LOGCAND", status="awaiting_dm", notion_page_id="pg-LOGCAND")
    fake.data_sources = _CandidateDataSources([
        _candidate_page("LOGCAND", main_point="Scrollworld animation toolkit repository"),
    ])
    monkeypatch.setattr(
        resource_lookup, "fetch_resource_title_and_description",
        lambda url: ("Scrollworld animation toolkit", "A repository for scrollworld animation toolkit"),
    )
    with caplog.at_level(_logging.INFO):
        client.post("/attach", json={
            "shortcode_or_note": None, "resource_url": RESOURCE_URL, "secret": "test-secret",
        })
    assert any("attach resolved: needs_confirmation" in r.message for r in caplog.records)


def test_logs_confirm_attached_outcome(monkeypatch, caplog):
    client, _fake = _client(monkeypatch)
    _seed_awaiting_dm("LOGCONFIRM")
    with caplog.at_level(_logging.INFO):
        client.post("/attach/confirm", json={
            "shortcode": "LOGCONFIRM", "resource_url": RESOURCE_URL, "secret": "test-secret",
        })
    assert any("attach resolved: attached" in r.message for r in caplog.records)


def test_logs_confirm_not_found_outcome(monkeypatch, caplog):
    client, _fake = _client(monkeypatch)
    with caplog.at_level(_logging.INFO):
        client.post("/attach/confirm", json={
            "shortcode": "NOSUCHROW", "resource_url": RESOURCE_URL, "secret": "test-secret",
        })
    assert any("attach resolved: not_found" in r.message for r in caplog.records)
