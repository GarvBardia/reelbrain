import json

from fastapi.testclient import TestClient

from app import main, notion_writer, store
from tests.test_pipeline import FakeClient

RESOURCE_URL = "https://instagram.com/direct/t/17800000000000000/"


def _client(monkeypatch):
    monkeypatch.setattr(main, "CAPTURE_SECRET", "test-secret")
    fake = FakeClient()
    monkeypatch.setattr(notion_writer, "_client", lambda: fake)
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


def test_attach_rejects_bad_secret(monkeypatch):
    client, _ = _client(monkeypatch)
    resp = client.post("/attach", json={"shortcode_or_note": None, "resource_url": RESOURCE_URL, "secret": "wrong"})
    assert resp.status_code == 401


def test_attach_by_explicit_shortcode(monkeypatch):
    client, fake = _client(monkeypatch)
    _seed_awaiting_dm("GATE001")

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": "GATE001", "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "attached", "shortcode": "GATE001", "notion_url": "https://notion.so/page-GATE001"}

    row = store.get_by_shortcode("GATE001")
    assert row["status"] == "done"
    assert row["gate_resource_url"] == RESOURCE_URL

    assert len(fake.pages.updated) == 1
    update_call = fake.pages.updated[0]
    assert update_call["page_id"] == "page-GATE001"
    assert update_call["properties"]["Status"]["select"]["name"] == "📥 Inbox"
    assert update_call["properties"]["Gate resource"]["url"] == RESOURCE_URL


def test_attach_omitted_shortcode_auto_picks_when_exactly_one_pending(monkeypatch):
    """Safe to auto-pick ONLY when there's no ambiguity to begin with."""
    client, fake = _client(monkeypatch)
    _seed_awaiting_dm("SOLE001")

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": None, "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["shortcode"] == "SOLE001"
    assert store.get_by_shortcode("SOLE001")["status"] == "done"


def test_attach_omitted_shortcode_with_multiple_pending_refuses_to_guess(monkeypatch):
    """The safety fix itself: with 2+ rows Awaiting DM and no shortcode given,
    refuse to guess — 409 listing every candidate, never a silent pick.
    (Requirement #3's 3-row case is covered directly against store.find_pending_gate
    in tests/test_notion_fallback.py; this is the same guarantee through the
    actual HTTP endpoint.)
    """
    client, fake = _client(monkeypatch)
    _seed_awaiting_dm("OLD001")
    _seed_awaiting_dm("NEW001")  # inserted later -> more recently updated, but that no longer matters

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": None, "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 409
    body = resp.json()["detail"]
    assert set(body["candidates"]) == {"OLD001", "NEW001"}
    # neither row was touched — a 409 must never have a side effect
    assert store.get_by_shortcode("OLD001")["status"] == "awaiting_dm"
    assert store.get_by_shortcode("NEW001")["status"] == "awaiting_dm"


def test_attach_matches_by_note_substring(monkeypatch):
    client, fake = _client(monkeypatch)
    _seed_awaiting_dm("NOTE001", note="the ai workflow one from Jane")

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": "ai workflow", "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["shortcode"] == "NOTE001"


def test_attach_ambiguous_note_substring_refuses_to_guess(monkeypatch):
    """The exact reported bug: two rows both containing the same word in their
    note — must 409 with both listed, not silently attach to whichever matched
    first."""
    client, fake = _client(monkeypatch)
    _seed_awaiting_dm("DUPNOTE1", note="check out this ai workflow doc from Jane")
    _seed_awaiting_dm("DUPNOTE2", note="another ai workflow tip from Bob")

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": "ai workflow", "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 409
    body = resp.json()["detail"]
    assert set(body["candidates"]) == {"DUPNOTE1", "DUPNOTE2"}
    assert "message" in body
    assert store.get_by_shortcode("DUPNOTE1")["status"] == "awaiting_dm"
    assert store.get_by_shortcode("DUPNOTE2")["status"] == "awaiting_dm"


def test_attach_ambiguous_title_substring_refuses_to_guess(monkeypatch):
    """Same as the note case, but the shared word is in the (locally-derived)
    title — extraction_json's main_point — rather than the note."""
    client, fake = _client(monkeypatch)
    for shortcode in ("DUPTITLE1", "DUPTITLE2"):
        _seed_awaiting_dm(shortcode)
        store.update_save(
            shortcode,
            extraction_json=json.dumps({"main_point": f"Growth hacking secret #{shortcode[-1]}"}),
        )

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": "growth hacking", "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 409
    assert set(resp.json()["detail"]["candidates"]) == {"DUPTITLE1", "DUPTITLE2"}


def test_attach_404_when_nothing_pending(monkeypatch):
    client, _ = _client(monkeypatch)
    resp = client.post(
        "/attach",
        json={"shortcode_or_note": None, "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 404


# --- real incident repro: Da8IIonEhGR (write "never landed") + DbFDY3yTwlI ------
# (misattributed to an unrelated reel) -- see PROGRESS.md for the full
# investigation. Both trace back to the SAME root cause: the documented
# Shortcut recipe hardcodes shortcode_or_note: null, so /attach never actually
# targets a specific reel -- it always relies on "the sole Awaiting DM row"
# auto-pick, which is blind to whether that row is the SEMANTICALLY correct
# one. These tests reproduce the exact mechanics, they do not fix them.

def _seed_photo_manual(shortcode: str) -> None:
    """A photo/carousel placeholder that never had a caption recovered -- its
    comment-gate (if the real Instagram post has one) was never programmatically
    detected, so it never passed through Awaiting DM at all. Mirrors
    Da8IIonEhGR's real live state exactly (verified via a live /attach
    reproduction against the deployed app, see PROGRESS.md)."""
    store.insert_processing(shortcode, f"https://www.instagram.com/reel/{shortcode}/")
    store.update_save(
        shortcode,
        status="photo_manual",
        notion_page_id=f"page-{shortcode}",
        notion_page_url=f"https://notion.so/page-{shortcode}",
    )


def test_attach_explicit_shortcode_on_never_gated_row_404s_not_a_wrong_row(monkeypatch):
    """Case 1 (Da8IIonEhGR) reproduced: an explicit shortcode targeting a row
    that exists but was never Awaiting DM (a photo/carousel placeholder whose
    gate, if any, was never detected) must 404 -- and must NEVER fall through
    to a different, unrelated Awaiting DM row. This is the exact live behavior
    confirmed by reproducing Da8IIonEhGR's case against the deployed app: a
    clean 404, not a silent wrong-row attach."""
    client, fake = _client(monkeypatch)
    _seed_photo_manual("PHOTOMANUAL1")
    _seed_awaiting_dm("UNRELATED1")  # a totally different, genuinely-pending gate

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": "PHOTOMANUAL1", "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 404
    # critically: the unrelated pending row must be untouched -- no silent
    # substitution just because something else happened to be Awaiting DM
    assert store.get_by_shortcode("UNRELATED1")["status"] == "awaiting_dm"
    assert store.get_by_shortcode("PHOTOMANUAL1")["status"] == "photo_manual"


def test_attach_omitted_shortcode_attaches_to_wrong_row_when_intended_target_never_gated(monkeypatch):
    """The actual incident mechanism, reproduced: the DOCUMENTED Shortcut
    recipe (README.md, Shortcut 2) hardcodes shortcode_or_note: null in its
    request body -- it never sends an identifying value at all. When the
    user's REAL intended target was never Awaiting DM to begin with (case 1),
    the null-shortcode fallback silently and "successfully" attaches the
    resource to whatever ELSE happens to be the sole Awaiting DM row instead
    -- a genuinely different reel, with no error surfaced anywhere. This is
    not a bug in the matching logic (there's no tie, so AmbiguousGateMatch
    correctly doesn't fire) -- it's the auto-pick fallback itself being blind
    to whether the row it grabbed is the semantically right one."""
    client, fake = _client(monkeypatch)
    _seed_photo_manual("INTENDEDTARGET")  # what the user actually meant
    _seed_awaiting_dm("WRONGPICK")  # the only row that's actually Awaiting DM

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": None, "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 200  # "success" -- but for the WRONG row
    assert resp.json()["shortcode"] == "WRONGPICK"
    assert store.get_by_shortcode("WRONGPICK")["gate_resource_url"] == RESOURCE_URL
    assert store.get_by_shortcode("INTENDEDTARGET")["status"] == "photo_manual"
    assert store.get_by_shortcode("INTENDEDTARGET")["gate_resource_url"] is None


def test_attach_omitted_shortcode_picks_unrelated_row_that_coincidentally_shares_a_keyword(monkeypatch):
    """Case 2 (DbFDY3yTwlI/Higgsfield) reproduced: TWO rows both mention the
    same tool name in their title, but only ONE is actually Awaiting DM at
    attach-time -- so there's no tie, no 409, and (per the documented
    shortcode_or_note: null recipe) no substring matching even runs. The
    null-shortcode fallback picks the sole Awaiting DM row with full
    "confidence" even though it's the WRONG one -- exactly the "safety net
    catches ties, not confident-wrong-guesses" gap."""
    client, fake = _client(monkeypatch)

    # The REAL intended target: mentions "Higgsfield", but already resolved
    # (done) -- e.g. a previous /attach already completed it, or it simply
    # isn't pending right now for any reason.
    store.insert_processing("REALTARGET", "https://www.instagram.com/reel/REALTARGET/")
    store.update_save(
        "REALTARGET", status="done",
        extraction_json=json.dumps({"main_point": "Higgsfield is offering 24 hours of free access."}),
        notion_page_id="page-REALTARGET",
    )

    # A coincidentally-similar, unrelated row that IS awaiting_dm right now.
    _seed_awaiting_dm("COINCIDENCE")
    store.update_save(
        "COINCIDENCE",
        extraction_json=json.dumps({"main_point": "An app built with Higgsfield AI applies filters."}),
    )

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": None, "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["shortcode"] == "COINCIDENCE"  # wrong -- REALTARGET was never even considered
    assert store.get_by_shortcode("REALTARGET")["gate_resource_url"] is None


def test_attach_survives_notion_failure(monkeypatch):
    client, fake = _client(monkeypatch)
    _seed_awaiting_dm("NOTIONDOWN")

    def _boom(**kwargs):
        raise RuntimeError("notion is down")

    fake.pages.update = _boom

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": "NOTIONDOWN", "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    # SQLite is the source of truth for /attach's own success — a Notion hiccup
    # shouldn't make the endpoint lie about whether the attach was recorded.
    assert resp.status_code == 200
    assert store.get_by_shortcode("NOTIONDOWN")["status"] == "done"
