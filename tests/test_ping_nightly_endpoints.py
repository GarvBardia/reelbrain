from datetime import timedelta

from fastapi.testclient import TestClient

from app import main, notion_writer, store
from app.store import _utc_naive_now
from tests.test_pipeline import FakeClient


def _client(monkeypatch):
    monkeypatch.setattr(main, "CAPTURE_SECRET", "test-secret")
    monkeypatch.setattr(notion_writer, "_client", lambda: FakeClient())
    return TestClient(main.app)


def test_ping_needs_no_auth(monkeypatch):
    client = _client(monkeypatch)
    resp = client.get("/ping")
    assert resp.status_code == 200
    assert resp.json() == {"pong": True}


def test_nightly_rejects_bad_secret(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/nightly", json={"secret": "wrong"})
    assert resp.status_code == 401


def test_nightly_runs_cleanup_and_reports(monkeypatch):
    client = _client(monkeypatch)

    store.insert_processing("HTTPSTUCK", "https://www.instagram.com/reel/HTTPSTUCK/")
    store.update_save("HTTPSTUCK", notion_page_id="page-HTTPSTUCK", notion_page_url="https://notion.so/page-HTTPSTUCK")
    backdated = (_utc_naive_now() - timedelta(minutes=90)).isoformat()
    with store.get_connection() as conn:
        conn.execute("UPDATE saves SET created_at = ?, updated_at = ? WHERE shortcode = 'HTTPSTUCK'", (backdated, backdated))

    resp = client.post("/nightly", json={"secret": "test-secret"})
    assert resp.status_code == 200
    assert resp.json() == {"marked_failed": ["HTTPSTUCK"], "marked_gate_expired": []}
    assert store.get_by_shortcode("HTTPSTUCK")["status"] == "failed"


def test_nightly_empty_run_returns_empty_lists(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/nightly", json={"secret": "test-secret"})
    assert resp.status_code == 200
    assert resp.json() == {"marked_failed": [], "marked_gate_expired": []}
