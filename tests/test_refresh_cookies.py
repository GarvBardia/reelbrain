"""scripts/refresh_cookies.py: the parts that CAN be tested without a real
browser session or real Render credentials — Netscape export shape, the
Render request shape, and the /health polling logic. This is a local-only
tool; the actual end-to-end run (real Chrome cookie store -> real Render
service -> real restart) is NOT and cannot be exercised here — see
PROGRESS.md and the module docstring.
"""
import http.cookiejar
import time as time_module

import browser_cookie3
import httpx
import pytest

from scripts import refresh_cookies


def _fake_cookie(name: str, value: str, domain: str = ".instagram.com") -> http.cookiejar.Cookie:
    return http.cookiejar.Cookie(
        version=0, name=name, value=value, port=None, port_specified=False,
        domain=domain, domain_specified=True, domain_initial_dot=True,
        path="/", path_specified=True, secure=True, expires=int(time_module.time()) + 3600,
        discard=False, comment=None, comment_url=None, rest={}, rfc2109=False,
    )


# --- export_netscape_cookies ---------------------------------------------------

def test_export_netscape_cookies_writes_real_netscape_file(monkeypatch, tmp_path):
    cookies = [_fake_cookie("sessionid", "abc123"), _fake_cookie("csrftoken", "xyz789")]
    monkeypatch.setattr(browser_cookie3, "chrome", lambda domain_name: cookies)

    output_path = tmp_path / "cookies.txt"
    count = refresh_cookies.export_netscape_cookies("chrome", output_path)

    assert count == 2
    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8")
    assert "Netscape" in content  # the standard MozillaCookieJar header
    assert "sessionid" in content and "abc123" in content

    # genuinely readable back as a real Netscape cookie file -- exactly what
    # yt-dlp's --cookies flag expects.
    readback = http.cookiejar.MozillaCookieJar(str(output_path))
    readback.load(ignore_discard=True, ignore_expires=True)
    names = {c.name for c in readback}
    assert names == {"sessionid", "csrftoken"}


def test_export_netscape_cookies_uses_edge_reader(monkeypatch, tmp_path):
    cookies = [_fake_cookie("sessionid", "edge-session")]
    monkeypatch.setattr(browser_cookie3, "edge", lambda domain_name: cookies)

    count = refresh_cookies.export_netscape_cookies("edge", tmp_path / "cookies.txt")
    assert count == 1


def test_export_netscape_cookies_raises_when_none_found(monkeypatch, tmp_path):
    monkeypatch.setattr(browser_cookie3, "chrome", lambda domain_name: [])

    with pytest.raises(refresh_cookies.RefreshError, match="no instagram.com cookies found"):
        refresh_cookies.export_netscape_cookies("chrome", tmp_path / "cookies.txt")


def test_export_netscape_cookies_unsupported_browser_raises(tmp_path):
    with pytest.raises(refresh_cookies.RefreshError, match="unsupported browser"):
        refresh_cookies.export_netscape_cookies("firefox", tmp_path / "cookies.txt")


def test_browser_reader_selects_chrome_or_edge():
    assert refresh_cookies._browser_reader("chrome") is browser_cookie3.chrome
    assert refresh_cookies._browser_reader("edge") is browser_cookie3.edge


# --- push_to_render --------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code=201):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_push_to_render_sends_correct_request(monkeypatch):
    monkeypatch.setattr(refresh_cookies, "RENDER_API_KEY", "rnd_test_key")
    monkeypatch.setattr(refresh_cookies, "RENDER_SERVICE_ID", "srv-abc123")
    calls = []

    def _fake_put(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _FakeResponse(201)

    monkeypatch.setattr(httpx, "put", _fake_put)

    refresh_cookies.push_to_render("# Netscape HTTP Cookie File\n...")

    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://api.render.com/v1/services/srv-abc123/secret-files/cookies.txt"
    assert call["headers"]["Authorization"] == "Bearer rnd_test_key"
    assert call["json"] == {"content": "# Netscape HTTP Cookie File\n..."}


def test_push_to_render_raises_without_credentials(monkeypatch):
    monkeypatch.setattr(refresh_cookies, "RENDER_API_KEY", "")
    monkeypatch.setattr(refresh_cookies, "RENDER_SERVICE_ID", "srv-abc123")

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("must not call Render's API without credentials")

    monkeypatch.setattr(httpx, "put", _must_not_be_called)

    with pytest.raises(refresh_cookies.RefreshError, match="RENDER_API_KEY"):
        refresh_cookies.push_to_render("content")


def test_push_to_render_propagates_http_errors(monkeypatch):
    monkeypatch.setattr(refresh_cookies, "RENDER_API_KEY", "rnd_test_key")
    monkeypatch.setattr(refresh_cookies, "RENDER_SERVICE_ID", "srv-abc123")
    monkeypatch.setattr(httpx, "put", lambda *a, **kw: _FakeResponse(401))

    with pytest.raises(RuntimeError, match="HTTP 401"):
        refresh_cookies.push_to_render("content")


# --- poll_health_until_cookies_ok -------------------------------------------------

def _fake_get_sequence(responses):
    calls = []

    def _get(url, timeout=None):
        calls.append(url)
        result = responses[len(calls) - 1]
        if isinstance(result, Exception):
            raise result
        return result

    return _get, calls


class _HealthResponse:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def test_poll_health_succeeds_on_first_try(monkeypatch):
    monkeypatch.setattr(refresh_cookies, "REELBRAIN_URL", "https://reelbrain.onrender.com")
    monkeypatch.setattr(refresh_cookies.time, "sleep", lambda s: None)
    get_fn, calls = _fake_get_sequence([_HealthResponse({"cookies_file": True, "cookie_health": "ok"})])
    monkeypatch.setattr(httpx, "get", get_fn)

    assert refresh_cookies.poll_health_until_cookies_ok(attempts=6, delay_seconds=0) is True
    assert len(calls) == 1


def test_poll_health_succeeds_after_a_few_tries(monkeypatch):
    monkeypatch.setattr(refresh_cookies, "REELBRAIN_URL", "https://reelbrain.onrender.com")
    monkeypatch.setattr(refresh_cookies.time, "sleep", lambda s: None)
    get_fn, calls = _fake_get_sequence([
        _HealthResponse({"cookies_file": False}),
        _HealthResponse({"cookies_file": False}),
        _HealthResponse({"cookies_file": True}),
    ])
    monkeypatch.setattr(httpx, "get", get_fn)

    assert refresh_cookies.poll_health_until_cookies_ok(attempts=6, delay_seconds=0) is True
    assert len(calls) == 3


def test_poll_health_gives_up_after_attempts_exhausted(monkeypatch):
    monkeypatch.setattr(refresh_cookies, "REELBRAIN_URL", "https://reelbrain.onrender.com")
    monkeypatch.setattr(refresh_cookies.time, "sleep", lambda s: None)
    get_fn, calls = _fake_get_sequence([_HealthResponse({"cookies_file": False})] * 3)
    monkeypatch.setattr(httpx, "get", get_fn)

    assert refresh_cookies.poll_health_until_cookies_ok(attempts=3, delay_seconds=0) is False
    assert len(calls) == 3


def test_poll_health_survives_a_request_exception_and_keeps_trying(monkeypatch):
    monkeypatch.setattr(refresh_cookies, "REELBRAIN_URL", "https://reelbrain.onrender.com")
    monkeypatch.setattr(refresh_cookies.time, "sleep", lambda s: None)
    get_fn, calls = _fake_get_sequence([
        ConnectionError("still restarting"),
        _HealthResponse({"cookies_file": True}),
    ])
    monkeypatch.setattr(httpx, "get", get_fn)

    assert refresh_cookies.poll_health_until_cookies_ok(attempts=6, delay_seconds=0) is True
    assert len(calls) == 2


def test_poll_health_skips_entirely_without_reelbrain_url(monkeypatch):
    monkeypatch.setattr(refresh_cookies, "REELBRAIN_URL", "")

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("must not poll /health without REELBRAIN_URL configured")

    monkeypatch.setattr(httpx, "get", _must_not_be_called)

    assert refresh_cookies.poll_health_until_cookies_ok() is False
