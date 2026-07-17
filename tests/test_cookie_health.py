"""Cookie-health failure tracking: the counter, the ok/degraded classification,
and that fetch_reel's cookie-backed retry loop wires into it correctly. All mocked
— no yt-dlp run, no network."""
import pytest

from app import fetcher, store
from app.fetcher import FetchDegraded

PERMALINK = "https://www.instagram.com/reel/COOKIETEST01/"


# --- counter mechanics ---------------------------------------------------------

def test_counter_starts_at_zero():
    assert fetcher.get_consecutive_auth_failures() == 0


def test_record_failure_increments_and_persists():
    assert fetcher.record_cookie_auth_failure() == 1
    assert fetcher.record_cookie_auth_failure() == 2
    assert fetcher.record_cookie_auth_failure() == 3
    assert fetcher.get_consecutive_auth_failures() == 3


def test_record_success_resets_to_zero():
    fetcher.record_cookie_auth_failure()
    fetcher.record_cookie_auth_failure()
    fetcher.record_cookie_auth_success()
    assert fetcher.get_consecutive_auth_failures() == 0


def test_counter_survives_across_store_calls_via_app_state():
    """Not fetcher-module state — genuinely persisted, so it survives a process
    restart (the whole point: a Render restart mid-degradation shouldn't reset
    the signal back to healthy)."""
    fetcher.record_cookie_auth_failure()
    assert store.get_state("consecutive_cookie_auth_failures") == "1"


# --- ok/degraded classification -------------------------------------------------

def test_cookie_health_ok_below_threshold(monkeypatch):
    monkeypatch.setattr(fetcher, "AUTH_FAILURE_THRESHOLD", 3)
    fetcher.record_cookie_auth_failure()
    fetcher.record_cookie_auth_failure()
    assert fetcher.cookie_health_status() == "ok"


def test_cookie_health_degraded_at_threshold(monkeypatch):
    monkeypatch.setattr(fetcher, "AUTH_FAILURE_THRESHOLD", 3)
    fetcher.record_cookie_auth_failure()
    fetcher.record_cookie_auth_failure()
    fetcher.record_cookie_auth_failure()
    assert fetcher.cookie_health_status() == "degraded"


def test_cookie_health_respects_custom_threshold(monkeypatch):
    monkeypatch.setattr(fetcher, "AUTH_FAILURE_THRESHOLD", 1)
    fetcher.record_cookie_auth_failure()
    assert fetcher.cookie_health_status() == "degraded"


# --- marker classification: auth-specific vs. generic challenge ----------------

@pytest.mark.parametrize("message", [
    "Login required to access this content",
    "empty media response ... use --cookies for the authentication",
    "Checkpoint required",
    "Instagram issued a challenge for this request",
])
def test_auth_markers_are_recognized(message):
    assert fetcher._is_cookie_auth_failure(Exception(message)) is True


@pytest.mark.parametrize("message", [
    "ERROR: [Instagram] No video formats found!",  # a normal missing-video, not auth
    "HTTP Error 429: Too Many Requests",             # generic rate limit, not auth
    "The requested content is not available",        # could be deleted content
])
def test_non_auth_challenge_markers_are_not_auth_failures(message):
    # still challenges (cookie retry fires) but NOT the cookie-health signal
    assert fetcher._looks_like_challenge(Exception(message)) is True
    assert fetcher._is_cookie_auth_failure(Exception(message)) is False


def test_hard_error_is_neither():
    exc = Exception("Unsupported URL: example.com")
    assert fetcher._looks_like_challenge(exc) is False
    assert fetcher._is_cookie_auth_failure(exc) is False


# --- wired into fetch_reel's cookie-backed retry loop ---------------------------

def _with_cookies(monkeypatch, tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# netscape cookie file")
    monkeypatch.setattr(fetcher, "BURNER_COOKIES_FILE", str(cookies))
    monkeypatch.setattr(fetcher, "BACKOFF_SECONDS", [0])  # don't actually sleep


def test_successful_cookie_fetch_resets_counter(monkeypatch, tmp_path):
    _with_cookies(monkeypatch, tmp_path)
    fetcher.record_cookie_auth_failure()
    fetcher.record_cookie_auth_failure()

    calls = {"n": 0}

    def _run_ytdlp(url, cookiefile):
        calls["n"] += 1
        if cookiefile is None:
            raise RuntimeError("login required")  # anonymous attempt fails, triggers cookie retry
        return {"id": "COOKIETEST01", "_video_path": "/tmp/x.mp4"}

    monkeypatch.setattr(fetcher, "_run_ytdlp", _run_ytdlp)

    reel = fetcher.fetch_reel("COOKIETEST01", PERMALINK)

    assert reel.video_path == "/tmp/x.mp4"
    assert fetcher.get_consecutive_auth_failures() == 0


def test_cookie_backed_auth_failure_increments_counter(monkeypatch, tmp_path):
    _with_cookies(monkeypatch, tmp_path)
    monkeypatch.setattr(fetcher, "fetch_og_metadata", lambda p: None)  # no OG fallback rescue

    def _run_ytdlp(url, cookiefile):
        raise RuntimeError("login required")  # fails identically anon + cookie-backed

    monkeypatch.setattr(fetcher, "_run_ytdlp", _run_ytdlp)

    with pytest.raises(FetchDegraded):
        fetcher.fetch_reel("COOKIETEST01", PERMALINK)

    # one attempt per BACKOFF_SECONDS entry (we set it to [0] -> exactly one retry)
    assert fetcher.get_consecutive_auth_failures() == 1


def test_non_auth_challenge_does_not_increment_counter(monkeypatch, tmp_path):
    """'No video formats found' still triggers the cookie retry (it's a
    CHALLENGE_MARKER) but must NOT count toward cookie-health, since it doesn't
    mean the cookies are bad."""
    _with_cookies(monkeypatch, tmp_path)
    monkeypatch.setattr(fetcher, "fetch_og_metadata", lambda p: None)

    def _run_ytdlp(url, cookiefile):
        # A CHALLENGE_MARKER that's neither an auth marker nor the photo/carousel
        # marker — this test is specifically about the auth-failure counter, not
        # about photo/carousel classification (see test_fetch_hardening.py for that).
        raise RuntimeError("requested content is not available")

    monkeypatch.setattr(fetcher, "_run_ytdlp", _run_ytdlp)

    with pytest.raises(FetchDegraded):
        fetcher.fetch_reel("COOKIETEST01", PERMALINK)

    assert fetcher.get_consecutive_auth_failures() == 0


def test_anonymous_only_failure_does_not_touch_counter(monkeypatch, tmp_path):
    """A hard failure on the very first (anonymous, no-cookie) attempt never
    reaches the cookie-backed retry loop at all — shouldn't affect the counter."""
    _with_cookies(monkeypatch, tmp_path)
    monkeypatch.setattr(fetcher, "fetch_og_metadata", lambda p: None)

    def _run_ytdlp(url, cookiefile):
        raise RuntimeError("Unsupported URL")  # hard error, not a challenge at all

    monkeypatch.setattr(fetcher, "_run_ytdlp", _run_ytdlp)

    with pytest.raises(FetchDegraded):
        fetcher.fetch_reel("COOKIETEST01", PERMALINK)

    assert fetcher.get_consecutive_auth_failures() == 0
