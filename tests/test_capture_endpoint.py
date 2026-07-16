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
