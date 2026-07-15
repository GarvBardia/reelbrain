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


def test_attach_falls_back_to_most_recent_awaiting_dm(monkeypatch):
    client, fake = _client(monkeypatch)
    _seed_awaiting_dm("OLD001")
    _seed_awaiting_dm("NEW001")  # inserted later -> more recently updated

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": None, "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["shortcode"] == "NEW001"
    assert store.get_by_shortcode("NEW001")["status"] == "done"
    assert store.get_by_shortcode("OLD001")["status"] == "awaiting_dm"  # untouched


def test_attach_matches_by_note_substring(monkeypatch):
    client, fake = _client(monkeypatch)
    _seed_awaiting_dm("NOTE001", note="the ai workflow one from Jane")

    resp = client.post(
        "/attach",
        json={"shortcode_or_note": "ai workflow", "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["shortcode"] == "NOTE001"


def test_attach_404_when_nothing_pending(monkeypatch):
    client, _ = _client(monkeypatch)
    resp = client.post(
        "/attach",
        json={"shortcode_or_note": None, "resource_url": RESOURCE_URL, "secret": "test-secret"},
    )
    assert resp.status_code == 404


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
