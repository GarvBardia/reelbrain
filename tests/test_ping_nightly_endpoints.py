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
    body = resp.json()
    assert body["marked_failed"] == ["HTTPSTUCK"]
    assert body["marked_gate_expired"] == []
    assert body["marked_archived"] == []
    assert body["cookie_alert"]["cookie_health"] == "ok"
    assert store.get_by_shortcode("HTTPSTUCK")["status"] == "failed"


def test_nightly_empty_run_returns_empty_lists(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/nightly", json={"secret": "test-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["marked_failed"] == []
    assert body["marked_gate_expired"] == []
    assert body["marked_archived"] == []
    assert body["cookie_alert"] == {"cookie_health": "ok", "alert_sent": False}


def test_daily_digest_rejects_bad_secret(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/daily-digest", json={"secret": "wrong"})
    assert resp.status_code == 401


def test_daily_digest_runs_and_reports(monkeypatch):
    from app import digest
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "parent-page-id")
    client = _client(monkeypatch)

    store.insert_processing("HTTPDIGEST1", "https://www.instagram.com/reel/HTTPDIGEST1/")
    store.update_save(
        "HTTPDIGEST1", status="done",
        extraction_json='{"main_point": "a daily point", "topic_tags": ["sleep"], "priority": "High", "value_score": 5}',
    )

    resp = client.post("/daily-digest", json={"secret": "test-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["save_count"] == 1
    assert body["high_priority_count"] == 1
    assert body["notion_page"] is not None
    assert "a daily point" in body["markdown"]


def test_daily_digest_empty_day_still_succeeds(monkeypatch):
    from app import digest
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "parent-page-id")
    client = _client(monkeypatch)

    resp = client.post("/daily-digest", json={"secret": "test-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["save_count"] == 0
    assert "Nothing saved today." in body["markdown"]


def test_weekly_digest_rejects_bad_secret(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/weekly-digest", json={"secret": "wrong"})
    assert resp.status_code == 401


def test_weekly_digest_runs_and_reports(monkeypatch):
    from app import digest
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "parent-page-id")
    client = _client(monkeypatch)

    store.insert_processing("HTTPWEEKLY1", "https://www.instagram.com/reel/HTTPWEEKLY1/")
    store.update_save(
        "HTTPWEEKLY1", creator="jane", status="done",
        extraction_json='{"main_point": "a weekly point"}',
        notion_page_url="https://notion.so/page-HTTPWEEKLY1",
    )
    store.set_tags("HTTPWEEKLY1", ["sleep"])

    resp = client.post("/weekly-digest", json={"secret": "test-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["save_count"] == 1
    assert body["notion_page"] is not None
    assert "a weekly point" in body["markdown"]


def test_weekly_digest_empty_week_still_succeeds(monkeypatch):
    from app import digest
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "parent-page-id")
    client = _client(monkeypatch)

    resp = client.post("/weekly-digest", json={"secret": "test-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["save_count"] == 0
    assert "No reels saved this week." in body["markdown"]
