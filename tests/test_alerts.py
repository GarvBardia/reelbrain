"""Cookie-health alerting: Notion via the shared FakeClient, ntfy.sh via a
monkeypatched httpx.post (conftest's autouse guard blocks the real thing), and
the once-per-day dedup. All mocked — no live Notion or ntfy calls."""
from app import alerts, fetcher, notion_writer, store
from tests.test_pipeline import FakeClient


def _degrade_cookies(monkeypatch, threshold=3):
    monkeypatch.setattr(fetcher, "AUTH_FAILURE_THRESHOLD", threshold)
    for _ in range(threshold):
        fetcher.record_cookie_auth_failure()


def _install_notion(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(notion_writer, "_client", lambda: fake)
    return fake


class _FakeNtfyResponse:
    status_code = 200

    def raise_for_status(self):
        return None


# --- check_and_alert: the gate itself ------------------------------------------

def test_no_alert_when_healthy(monkeypatch):
    _install_notion(monkeypatch)
    result = alerts.check_and_alert()
    assert result == {"cookie_health": "ok", "alert_sent": False}


def test_alert_fires_when_degraded(monkeypatch):
    fake = _install_notion(monkeypatch)
    monkeypatch.setattr(alerts, "NOTION_PARENT_PAGE_ID", "parent-page-id")
    _degrade_cookies(monkeypatch)

    result = alerts.check_and_alert()

    assert result["cookie_health"] == "degraded"
    assert result["alert_sent"] is True
    assert result["notion_page"] is not None
    assert len(fake.pages.created) == 1


def test_alert_only_fires_once_per_day(monkeypatch):
    _install_notion(monkeypatch)
    monkeypatch.setattr(alerts, "NOTION_PARENT_PAGE_ID", "parent-page-id")
    _degrade_cookies(monkeypatch)

    first = alerts.check_and_alert()
    second = alerts.check_and_alert()

    assert first["alert_sent"] is True
    assert second["alert_sent"] is False
    assert second["reason"] == "already alerted today"


def test_alert_fires_again_on_a_new_day(monkeypatch):
    _install_notion(monkeypatch)
    monkeypatch.setattr(alerts, "NOTION_PARENT_PAGE_ID", "parent-page-id")
    _degrade_cookies(monkeypatch)
    alerts.check_and_alert()

    monkeypatch.setattr(alerts, "_today", lambda: "2099-01-01")  # simulate tomorrow
    result = alerts.check_and_alert()
    assert result["alert_sent"] is True


# --- send_notion_alert -----------------------------------------------------------

def test_send_notion_alert_creates_page_under_parent(monkeypatch):
    fake = _install_notion(monkeypatch)
    monkeypatch.setattr(alerts, "NOTION_PARENT_PAGE_ID", "parent-page-id")

    result = alerts.send_notion_alert("cookies are dead")

    assert result is not None
    call = fake.pages.created[0]
    assert call["parent"] == {"type": "page_id", "page_id": "parent-page-id"}
    assert "System Alert" in call["properties"]["title"]["title"][0]["text"]["content"]
    assert "cookies likely expired" in call["properties"]["title"]["title"][0]["text"]["content"]
    callout = call["children"][0]
    assert callout["type"] == "callout"
    assert callout["callout"]["rich_text"][0]["text"]["content"] == "cookies are dead"


def test_send_notion_alert_skips_without_parent_id(monkeypatch):
    monkeypatch.setattr(alerts, "NOTION_PARENT_PAGE_ID", "")
    assert alerts.send_notion_alert("x") is None


def test_send_notion_alert_survives_notion_failure(monkeypatch):
    def _boom():
        raise RuntimeError("notion is down")
    monkeypatch.setattr(notion_writer, "_client", _boom)
    monkeypatch.setattr(alerts, "NOTION_PARENT_PAGE_ID", "parent-page-id")

    assert alerts.send_notion_alert("x") is None  # never raises


def test_alert_survives_notion_failure_end_to_end(monkeypatch):
    """check_and_alert must still report a sent attempt even if Notion itself
    is unreachable — SQLite state is the source of truth for dedup, not Notion."""
    def _boom():
        raise RuntimeError("notion is down")
    monkeypatch.setattr(notion_writer, "_client", _boom)
    monkeypatch.setattr(alerts, "NOTION_PARENT_PAGE_ID", "parent-page-id")
    _degrade_cookies(monkeypatch)

    result = alerts.check_and_alert()
    assert result["alert_sent"] is True
    assert result["notion_page"] is None


# --- send_ntfy_alert ---------------------------------------------------------------

def test_send_ntfy_alert_posts_to_topic_url(monkeypatch):
    calls = []

    def _fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeNtfyResponse()

    import httpx
    monkeypatch.setattr(httpx, "post", _fake_post)
    monkeypatch.setattr(alerts, "NTFY_TOPIC", "my-secret-topic")

    assert alerts.send_ntfy_alert("cookies are dead") is True
    url, kwargs = calls[0]
    assert url == "https://ntfy.sh/my-secret-topic"
    assert kwargs["content"] == b"cookies are dead"
    assert kwargs["headers"]["Priority"] == "high"


def test_send_ntfy_alert_skipped_without_topic(monkeypatch):
    monkeypatch.setattr(alerts, "NTFY_TOPIC", "")
    assert alerts.send_ntfy_alert("x") is False


def test_send_ntfy_alert_never_raises_on_network_error(monkeypatch):
    import httpx

    def _boom(*a, **k):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(httpx, "post", _boom)
    monkeypatch.setattr(alerts, "NTFY_TOPIC", "my-secret-topic")

    assert alerts.send_ntfy_alert("x") is False  # never raises


def test_check_and_alert_uses_both_channels(monkeypatch):
    _install_notion(monkeypatch)
    monkeypatch.setattr(alerts, "NOTION_PARENT_PAGE_ID", "parent-page-id")
    monkeypatch.setattr(alerts, "NTFY_TOPIC", "my-secret-topic")
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeNtfyResponse())
    _degrade_cookies(monkeypatch)

    result = alerts.check_and_alert()
    assert result["notion_page"] is not None
    assert result["ntfy_sent"] is True


# --- build_alert_message ------------------------------------------------------

def test_build_alert_message_mentions_count_and_runbook():
    message = alerts.build_alert_message(5)
    assert "5" in message
    assert "COOKIES.md" in message
