"""Task 5 rejection paths: strict models, malformed inputs, rate limit."""
from fastapi.testclient import TestClient

from app import main, notion_writer
from tests.test_pipeline import FakeClient


def _client(monkeypatch):
    monkeypatch.setattr(main, "CAPTURE_SECRET", "test-secret")
    monkeypatch.setattr(notion_writer, "_client", lambda: FakeClient())
    return TestClient(main.app)


def test_capture_rejects_unknown_fields(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/capture", json={
        "url": "https://www.instagram.com/reel/ABC123/", "secret": "test-secret",
        "note": None, "sneaky_extra": "x",
    })
    assert resp.status_code == 422


def test_capture_rejects_oversized_url(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/capture", json={
        "url": "https://www.instagram.com/reel/" + "A" * 3000, "secret": "test-secret", "note": None,
    })
    assert resp.status_code == 422


def test_capture_rejects_empty_url(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/capture", json={"url": "", "secret": "test-secret", "note": None})
    assert resp.status_code == 422


def test_capture_malformed_url_gets_clear_400(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/capture", json={"url": "https://example.com/nope", "secret": "test-secret", "note": None})
    assert resp.status_code == 400
    assert "shortcode" in resp.json()["detail"]


def test_attach_rejects_non_http_resource_url(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/attach", json={
        "shortcode_or_note": None, "resource_url": "javascript:alert(1)", "secret": "test-secret",
    })
    assert resp.status_code == 422


def test_attach_rejects_unknown_fields(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/attach", json={
        "shortcode_or_note": None, "resource_url": "https://x.com/y", "secret": "test-secret", "extra": 1,
    })
    assert resp.status_code == 422


def test_retry_rejects_malformed_shortcode(monkeypatch):
    # (Encoded path-traversal like /retry/..%2F..%2Fetc never even routes —
    # starlette 404s it. This tests the shortcode-format gate for junk that
    # DOES reach the handler.)
    client = _client(monkeypatch)
    resp = client.post("/retry/bad!code$here")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "malformed shortcode"


def test_retry_rejects_overlong_shortcode(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/retry/" + "A" * 31)
    assert resp.status_code == 400


def test_nightly_rejects_unknown_fields(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post("/nightly", json={"secret": "test-secret", "dry_run": True})
    assert resp.status_code == 422


def test_rate_limit_trips_at_threshold_per_ip(monkeypatch):
    client = _client(monkeypatch)
    # /nightly with a wrong secret: cheap (no side effects), but each request
    # still passes through the rate limiter before the secret check.
    for i in range(main.RATE_LIMIT_MAX_PER_MINUTE):
        resp = client.post("/nightly", json={"secret": "wrong"})
        assert resp.status_code == 401, f"request {i} unexpectedly {resp.status_code}"
    resp = client.post("/nightly", json={"secret": "wrong"})
    assert resp.status_code == 429


def test_rate_limit_window_expires(monkeypatch):
    client = _client(monkeypatch)
    for _ in range(main.RATE_LIMIT_MAX_PER_MINUTE):
        client.post("/nightly", json={"secret": "wrong"})
    assert client.post("/nightly", json={"secret": "wrong"}).status_code == 429

    # simulate the window passing by rewinding every recorded timestamp
    for bucket in main._rate_buckets.values():
        for i in range(len(bucket)):
            bucket[i] -= main.RATE_LIMIT_WINDOW_SECONDS + 1

    assert client.post("/nightly", json={"secret": "wrong"}).status_code == 401  # limited no more
