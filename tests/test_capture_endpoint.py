"""Exercises the actual HTTP surface: secret check, dedupe, background pipeline run."""
from fastapi.testclient import TestClient

from app import main, notion_writer
from tests.test_pipeline import FakeClient

URL = "https://www.instagram.com/reel/EP001abc/"
GATED_URL = "https://www.instagram.com/reel/EPGATE01/"


def _client(monkeypatch, tutorial_reel, tutorial_extraction):
    monkeypatch.setattr(main, "CAPTURE_SECRET", "test-secret")
    monkeypatch.setattr(notion_writer, "_client", lambda: FakeClient())
    monkeypatch.setattr(
        main.fetcher, "fetch_reel",
        lambda shortcode, permalink: tutorial_reel.model_copy(
            update={"shortcode": shortcode, "permalink": permalink}
        ),
    )
    monkeypatch.setattr(
        main.gemini_pipe, "run_extraction",
        lambda reel, note, taxonomy: tutorial_extraction,
    )
    return TestClient(main.app)


def test_health_reports_sqlite_vec(monkeypatch, tutorial_reel, tutorial_extraction):
    client = _client(monkeypatch, tutorial_reel, tutorial_extraction)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # sqlite-vec is installed in the test environment, so the probe must succeed
    assert body["sqlite_vec"] is True


def test_health_reports_cookies_file_present(monkeypatch, tutorial_reel, tutorial_extraction, tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# netscape cookie file")
    monkeypatch.setattr(main.fetcher, "BURNER_COOKIES_FILE", str(cookies))
    client = _client(monkeypatch, tutorial_reel, tutorial_extraction)
    assert client.get("/health").json()["cookies_file"] is True


def test_health_reports_cookies_file_missing(monkeypatch, tutorial_reel, tutorial_extraction, tmp_path):
    monkeypatch.setattr(main.fetcher, "BURNER_COOKIES_FILE", str(tmp_path / "nope.txt"))
    monkeypatch.setattr(main.fetcher, "RENDER_SECRETS_COOKIES_FILE", str(tmp_path / "also-nope.txt"))
    client = _client(monkeypatch, tutorial_reel, tutorial_extraction)
    assert client.get("/health").json()["cookies_file"] is False


def test_health_reports_cookie_health_ok_by_default(monkeypatch, tutorial_reel, tutorial_extraction):
    client = _client(monkeypatch, tutorial_reel, tutorial_extraction)
    assert client.get("/health").json()["cookie_health"] == "ok"


def test_health_reports_cookie_health_degraded(monkeypatch, tutorial_reel, tutorial_extraction):
    monkeypatch.setattr(main.fetcher, "AUTH_FAILURE_THRESHOLD", 3)
    for _ in range(3):
        main.fetcher.record_cookie_auth_failure()
    client = _client(monkeypatch, tutorial_reel, tutorial_extraction)
    assert client.get("/health").json()["cookie_health"] == "degraded"


def test_capture_rejects_bad_secret(monkeypatch, tutorial_reel, tutorial_extraction):
    client = _client(monkeypatch, tutorial_reel, tutorial_extraction)
    resp = client.post("/capture", json={"url": URL, "note": None, "secret": "wrong"})
    assert resp.status_code == 401


def test_capture_processes_then_dedupes(monkeypatch, tutorial_reel, tutorial_extraction):
    client = _client(monkeypatch, tutorial_reel, tutorial_extraction)

    first = client.post("/capture", json={"url": URL, "note": None, "secret": "test-secret"})
    assert first.status_code == 202
    assert first.json()["status"] == "processing"

    second = client.post("/capture", json={"url": URL, "note": None, "secret": "test-secret"})
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert second.json()["url"]  # points at the page created by the first request


def test_capture_rejects_non_instagram_url(monkeypatch, tutorial_reel, tutorial_extraction):
    client = _client(monkeypatch, tutorial_reel, tutorial_extraction)
    resp = client.post("/capture", json={"url": "https://example.com/x", "note": None, "secret": "test-secret"})
    assert resp.status_code == 400


def test_capture_dedupe_surfaces_gate_keyword_for_comment_gate_assist(
    monkeypatch, gated_reel, gated_extraction
):
    """BUILD_SPEC 2.2: re-sharing an already-gated link is how the Shortcut learns
    the keyword, since /capture responds long before the gate is known."""
    monkeypatch.setattr(main, "CAPTURE_SECRET", "test-secret")
    monkeypatch.setattr(notion_writer, "_client", lambda: FakeClient())
    monkeypatch.setattr(
        main.fetcher, "fetch_reel",
        lambda shortcode, permalink: gated_reel.model_copy(
            update={"shortcode": shortcode, "permalink": permalink}
        ),
    )
    monkeypatch.setattr(main.gemini_pipe, "run_extraction", lambda reel, note, taxonomy: gated_extraction)
    client = TestClient(main.app)

    first = client.post("/capture", json={"url": GATED_URL, "note": None, "secret": "test-secret"})
    assert first.status_code == 202

    second = client.post("/capture", json={"url": GATED_URL, "note": None, "secret": "test-secret"})
    assert second.status_code == 200
    body = second.json()
    assert body["status"] == "duplicate"
    assert body["capture_status"] == "awaiting_dm"
    assert body["gate_keyword"] == "SEND"
    assert body["permalink"] == GATED_URL


# --- FIX: dedupe falls back to Notion (real DabVtQoCI2p duplicate incident) -----

def _saves_page_for_dedupe(shortcode):
    return {
        "id": f"pg-{shortcode}", "url": f"https://notion.so/pg-{shortcode}",
        "properties": {
            "Shortcode": {"rich_text": [{"plain_text": shortcode}]},
            "Title": {"title": [{"plain_text": "An existing save"}]},
            "My note": {"rich_text": []},
            "Status": {"select": {"name": "📥 Inbox"}},
            "Reel URL": {"url": f"https://www.instagram.com/reel/{shortcode}/"},
            "Gate keyword": {"rich_text": [{"plain_text": "SEND"}]},
        },
    }


def test_capture_dedupes_via_notion_when_local_sqlite_wiped(
    monkeypatch, tutorial_reel, tutorial_extraction
):
    """The exact incident: a redeploy wiped local SQLite between two shares of
    the same post — the second share must come back 'duplicate' from the
    Notion fallback, NOT create a second page."""
    from tests.test_notion_fallback import _FilteringDataSources

    client = _client(monkeypatch, tutorial_reel, tutorial_extraction)
    fake = FakeClient()
    fake.data_sources = _FilteringDataSources([_saves_page_for_dedupe("DUPWIPE01")])
    monkeypatch.setattr(notion_writer, "_client", lambda: fake)
    # local SQLite is empty (fresh tmp_db) — the wiped-disk condition

    resp = client.post("/capture", json={
        "url": "https://www.instagram.com/reel/DUPWIPE01/", "note": None, "secret": "test-secret",
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "duplicate"
    assert body["url"] == "https://notion.so/pg-DUPWIPE01"
    assert body["gate_keyword"] == "SEND"
    assert len(fake.pages.created) == 0  # crucially: no second page


def test_capture_proceeds_as_new_when_notion_lookup_fails(
    monkeypatch, tutorial_reel, tutorial_extraction
):
    """Fail-open: a Notion hiccup during the dedupe lookup must not reject the
    capture — it proceeds as new (the pre-fix behavior, worst case)."""
    client = _client(monkeypatch, tutorial_reel, tutorial_extraction)

    def _boom():
        raise RuntimeError("notion down")
    monkeypatch.setattr(notion_writer, "_client", _boom)

    resp = client.post("/capture", json={
        "url": "https://www.instagram.com/reel/NEWROW01/", "note": None, "secret": "test-secret",
    })
    assert resp.status_code == 202
    assert resp.json()["status"] == "processing"
