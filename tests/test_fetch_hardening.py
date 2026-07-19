"""Cookie-path resolution, fail-fast-without-cookies, challenge detection, and the
OG-tag fallback. All mocked — no yt-dlp run, no network."""
import pytest

from app import fetcher
from app.fetcher import FetchDegraded

PERMALINK = "https://www.instagram.com/reel/OGTEST01/"

# Trimmed to the tags we read, but shaped like the real thing: escaped entities,
# both attribute orders, and IG's "Name (@handle) on Instagram" title format.
IG_HTML = """
<html><head>
<meta property="og:title" content="Jane Doe (@sleepcoachjane) on Instagram" />
<meta content="3 steps to fix your sleep &amp; energy. Comment &#39;SEND&#39; for the guide" property="og:description" />
<meta property="og:image" content="https://scontent.cdninstagram.com/v/thumb.jpg" />
</head><body></body></html>
"""


# --- cookie resolution -------------------------------------------------------

def test_resolves_configured_cookie_path_first(monkeypatch, tmp_path):
    configured = tmp_path / "cookies.txt"
    configured.write_text("# netscape cookie file")
    secrets = tmp_path / "secret_cookies.txt"
    secrets.write_text("# other")
    monkeypatch.setattr(fetcher, "BURNER_COOKIES_FILE", str(configured))
    monkeypatch.setattr(fetcher, "RENDER_SECRETS_COOKIES_FILE", str(secrets))

    assert fetcher.resolve_cookies_file() == str(configured)
    assert fetcher.cookies_file_available() is True


def test_falls_back_to_render_secrets_mount(monkeypatch, tmp_path):
    """The Render case: BURNER_COOKIES_FILE default (./cookies.txt) doesn't exist
    in the container, but the Secret File is mounted at /etc/secrets/cookies.txt."""
    secrets = tmp_path / "secret_cookies.txt"
    secrets.write_text("# netscape cookie file")
    monkeypatch.setattr(fetcher, "BURNER_COOKIES_FILE", str(tmp_path / "nope.txt"))
    monkeypatch.setattr(fetcher, "RENDER_SECRETS_COOKIES_FILE", str(secrets))

    assert fetcher.resolve_cookies_file() == str(secrets)


def test_returns_none_when_no_cookie_file_anywhere(monkeypatch, tmp_path):
    monkeypatch.setattr(fetcher, "BURNER_COOKIES_FILE", str(tmp_path / "nope.txt"))
    monkeypatch.setattr(fetcher, "RENDER_SECRETS_COOKIES_FILE", str(tmp_path / "also-nope.txt"))

    assert fetcher.resolve_cookies_file() is None
    assert fetcher.cookies_file_available() is False


def test_cookie_candidates_deduped(monkeypatch):
    monkeypatch.setattr(fetcher, "BURNER_COOKIES_FILE", "/etc/secrets/cookies.txt")
    monkeypatch.setattr(fetcher, "RENDER_SECRETS_COOKIES_FILE", "/etc/secrets/cookies.txt")
    assert fetcher._cookie_candidates() == ["/etc/secrets/cookies.txt"]


# --- fail fast without cookies ----------------------------------------------

def test_fetch_fails_fast_with_actionable_reason_when_no_cookies(monkeypatch, tmp_path):
    monkeypatch.setattr(fetcher, "BURNER_COOKIES_FILE", str(tmp_path / "nope.txt"))
    monkeypatch.setattr(fetcher, "RENDER_SECRETS_COOKIES_FILE", str(tmp_path / "also-nope.txt"))

    def _ytdlp_must_not_run(url, cookiefile):
        raise AssertionError("must not attempt an anonymous fetch without cookies")

    monkeypatch.setattr(fetcher, "_run_ytdlp", _ytdlp_must_not_run)

    with pytest.raises(FetchDegraded) as exc_info:
        fetcher.fetch_reel("OGTEST01", PERMALINK)

    message = str(exc_info.value)
    assert "cookies file not found at" in message
    assert "nope.txt" in message and "also-nope.txt" in message  # both paths named
    # fail-fast means it never burned a fetch against the daily cap
    from app import store
    assert store.get_daily_fetch_count() == 0


# --- challenge markers -------------------------------------------------------

@pytest.mark.parametrize("message", [
    "ERROR: [Instagram] No video formats found!",
    "empty media response ... use --cookies for the authentication",
    "The requested content is not available, rate-limited",
    "HTTP Error 429: Too Many Requests",
    "Login required to access this content",
])
def test_soft_block_messages_are_treated_as_challenges(message):
    """These are how a datacenter-IP soft-block actually presents. If they aren't
    recognised as challenges, the cookie-backed retry never fires at all."""
    assert fetcher._looks_like_challenge(Exception(message)) is True


def test_genuine_hard_error_is_not_a_challenge():
    assert fetcher._looks_like_challenge(Exception("Unsupported URL: example.com")) is False


# --- OG tag parsing ----------------------------------------------------------

def test_parse_og_tags_handles_both_attribute_orders_and_entities():
    tags = fetcher._parse_og_tags(IG_HTML)
    assert tags["og:title"] == "Jane Doe (@sleepcoachjane) on Instagram"
    # content-before-property order parsed, &amp; and &#39; unescaped
    assert tags["og:description"] == "3 steps to fix your sleep & energy. Comment 'SEND' for the guide"
    assert tags["og:image"] == "https://scontent.cdninstagram.com/v/thumb.jpg"


def test_parse_og_tags_returns_empty_on_junk_html():
    assert fetcher._parse_og_tags("<html><body>nothing here</body></html>") == {}


def test_og_reel_data_extracts_caption_username_thumbnail():
    reel = fetcher._og_reel_data("OGTEST01", PERMALINK, fetcher._parse_og_tags(IG_HTML))
    assert reel is not None
    assert reel.creator_username == "sleepcoachjane"
    assert reel.thumbnail_url == "https://scontent.cdninstagram.com/v/thumb.jpg"
    assert reel.video_path is None  # no media — downstream must degrade honestly
    assert "Comment 'SEND'" in reel.caption


def test_og_reel_data_none_without_caption():
    assert fetcher._og_reel_data("X", PERMALINK, {"og:image": "https://x/y.jpg"}) is None


def test_fetch_og_metadata_is_anonymous_one_shot_with_timeout(monkeypatch):
    calls = []

    class _Resp:
        text = IG_HTML

        def raise_for_status(self):
            return None

    def _fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "get", _fake_get)

    tags = fetcher.fetch_og_metadata(PERMALINK)
    assert tags["og:image"] == "https://scontent.cdninstagram.com/v/thumb.jpg"

    assert len(calls) == 1  # exactly one attempt
    url, kwargs = calls[0]
    assert url == PERMALINK
    assert kwargs["timeout"] == fetcher.OG_FETCH_TIMEOUT_SECONDS
    assert "Mozilla/5.0" in kwargs["headers"]["User-Agent"]
    # must never authenticate this request
    assert "cookies" not in kwargs
    assert "cookiefile" not in kwargs


def test_fetch_og_metadata_returns_none_on_http_error(monkeypatch):
    import httpx

    def _boom(url, **kwargs):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(httpx, "get", _boom)
    assert fetcher.fetch_og_metadata(PERMALINK) is None


# --- OG fallback inside fetch_reel -------------------------------------------

def _with_cookies(monkeypatch, tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# netscape cookie file")
    monkeypatch.setattr(fetcher, "BURNER_COOKIES_FILE", str(cookies))


def test_ytdlp_hard_failure_falls_back_to_og_caption(monkeypatch, tmp_path):
    _with_cookies(monkeypatch, tmp_path)

    def _ytdlp_fails(url, cookiefile):
        raise RuntimeError("Unsupported URL")  # not a challenge -> terminal

    monkeypatch.setattr(fetcher, "_run_ytdlp", _ytdlp_fails)
    monkeypatch.setattr(fetcher, "fetch_og_metadata", lambda p: fetcher._parse_og_tags(IG_HTML))

    reel = fetcher.fetch_reel("OGTEST01", PERMALINK)

    # row is NOT failed — pipeline continues caption-only
    assert reel.video_path is None
    assert reel.creator_username == "sleepcoachjane"
    assert "Comment 'SEND'" in reel.caption


def test_exhausted_challenges_fall_back_to_og(monkeypatch, tmp_path):
    _with_cookies(monkeypatch, tmp_path)
    monkeypatch.setattr(fetcher, "BACKOFF_SECONDS", [])  # don't actually sleep

    def _soft_blocked(url, cookiefile):
        raise RuntimeError("No video formats found")

    monkeypatch.setattr(fetcher, "_run_ytdlp", _soft_blocked)
    monkeypatch.setattr(fetcher, "fetch_og_metadata", lambda p: fetcher._parse_og_tags(IG_HTML))

    reel = fetcher.fetch_reel("OGTEST01", PERMALINK)
    assert "Comment 'SEND'" in reel.caption


def test_exhausted_challenges_with_og_success_tags_photo_or_carousel(monkeypatch, tmp_path):
    """Caption-only extraction fix: when yt-dlp's failure is the photo/carousel
    signature AND the OG-tag scrape DOES recover a caption, the reel must still
    be tagged is_photo_or_carousel (previously only the OG-also-fails branch
    tagged it) so main.py routes it to the caption-only Gemini extraction
    instead of silently treating it as an ordinary video-equivalent capture."""
    _with_cookies(monkeypatch, tmp_path)
    # at least one retry so last_exc (and thus "no video formats found") is
    # actually captured into the final reason string -- BACKOFF_SECONDS=[]
    # would skip the retry loop entirely and lose the classifying signature.
    monkeypatch.setattr(fetcher, "BACKOFF_SECONDS", [0])
    monkeypatch.setattr(fetcher, "_run_ytdlp", lambda url, cookiefile: (_ for _ in ()).throw(
        RuntimeError("No video formats found")
    ))
    monkeypatch.setattr(fetcher, "fetch_og_metadata", lambda p: fetcher._parse_og_tags(IG_HTML))

    reel = fetcher.fetch_reel("OGTEST01", PERMALINK)
    assert reel.is_photo_or_carousel is True
    assert reel.caption is not None
    assert "no video transcript available" in reel.fetch_note


def test_non_photo_failure_with_og_success_is_not_tagged(monkeypatch, tmp_path):
    """Regression guard: a genuine soft-block (not photo/carousel) whose OG
    scrape happens to succeed must NOT be misclassified as photo/carousel."""
    _with_cookies(monkeypatch, tmp_path)
    monkeypatch.setattr(fetcher, "BACKOFF_SECONDS", [])
    monkeypatch.setattr(fetcher, "_run_ytdlp", lambda url, cookiefile: (_ for _ in ()).throw(
        RuntimeError("checkpoint required")
    ))
    monkeypatch.setattr(fetcher, "fetch_og_metadata", lambda p: fetcher._parse_og_tags(IG_HTML))

    reel = fetcher.fetch_reel("OGTEST01", PERMALINK)
    assert reel.is_photo_or_carousel is False
    assert reel.fetch_note is None


def test_og_fallback_failure_still_degrades_with_reason(monkeypatch, tmp_path):
    _with_cookies(monkeypatch, tmp_path)

    def _ytdlp_fails(url, cookiefile):
        raise RuntimeError("Unsupported URL")

    monkeypatch.setattr(fetcher, "_run_ytdlp", _ytdlp_fails)
    monkeypatch.setattr(fetcher, "fetch_og_metadata", lambda p: None)

    with pytest.raises(FetchDegraded) as exc_info:
        fetcher.fetch_reel("OGTEST01", PERMALINK)
    assert "Unsupported URL" in str(exc_info.value)
    assert exc_info.value.partial.shortcode == "OGTEST01"


# --- photo/carousel posts: never dropped, even when OG also fails -----------

@pytest.mark.parametrize("reason,expected", [
    ("ERROR: [Instagram] No video formats found!", True),
    ("no video formats found for this post", True),
    ("Unsupported URL: example.com", False),
    ("HTTP Error 429: Too Many Requests", False),
])
def test_is_photo_or_carousel_marker(reason, expected):
    assert fetcher._is_photo_or_carousel(reason) is expected


def test_photo_carousel_captured_url_only_when_og_also_fails(monkeypatch, tmp_path):
    """The exact bug: yt-dlp says 'no video formats found' (a photo/carousel
    post) and the OG-tag scrape also comes back empty (login-walled from
    Render's IP) — this must NOT raise FetchDegraded. It must still produce a
    usable ReelData so the capture lands, not vanishes."""
    _with_cookies(monkeypatch, tmp_path)
    monkeypatch.setattr(fetcher, "fetch_og_metadata", lambda p: None)  # login-walled scrape

    reel = fetcher._og_fallback_or_degrade(
        "PHOTO01", PERMALINK, "ERROR: [Instagram] No video formats found!", RuntimeError("boom"),
    )

    assert reel.shortcode == "PHOTO01"
    assert reel.permalink == PERMALINK
    assert reel.video_path is None
    assert reel.caption is None
    assert reel.is_photo_or_carousel is True
    assert reel.fetch_note == "photo/carousel post — no auto-transcript, open the reel URL to view"


def test_non_photo_failure_with_failed_og_still_raises_as_before(monkeypatch, tmp_path):
    """Regression guard: an unrelated hard failure must NOT be reclassified as a
    photo/carousel post just because OG also failed — it should still raise
    FetchDegraded (Failed — retry), since that one might genuinely be worth
    a human retry."""
    _with_cookies(monkeypatch, tmp_path)
    monkeypatch.setattr(fetcher, "fetch_og_metadata", lambda p: None)

    with pytest.raises(FetchDegraded) as exc_info:
        fetcher._og_fallback_or_degrade("HARD01", PERMALINK, "Unsupported URL: example.com", RuntimeError("x"))
    assert exc_info.value.partial.shortcode == "HARD01"


def test_fetch_reel_end_to_end_photo_carousel_never_raises(monkeypatch, tmp_path):
    """fetch_reel itself (not just the inner helper) must return, not raise, for
    the full photo/carousel + failed-OG-scrape scenario."""
    _with_cookies(monkeypatch, tmp_path)
    monkeypatch.setattr(fetcher, "BACKOFF_SECONDS", [0])
    monkeypatch.setattr(fetcher, "fetch_og_metadata", lambda p: None)

    def _no_video(url, cookiefile):
        raise RuntimeError("No video formats found")

    monkeypatch.setattr(fetcher, "_run_ytdlp", _no_video)

    reel = fetcher.fetch_reel("PHOTO02", PERMALINK)  # must not raise
    assert reel.is_photo_or_carousel is True
    assert reel.permalink == PERMALINK


def test_cookie_retry_uses_resolved_path(monkeypatch, tmp_path):
    """The cookie-backed retry must use the resolved file, not the raw env value."""
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# netscape cookie file")
    monkeypatch.setattr(fetcher, "BURNER_COOKIES_FILE", str(tmp_path / "missing.txt"))
    monkeypatch.setattr(fetcher, "RENDER_SECRETS_COOKIES_FILE", str(cookies))
    monkeypatch.setattr(fetcher, "BACKOFF_SECONDS", [0])

    seen: list = []

    def _record(url, cookiefile):
        seen.append(cookiefile)
        # A genuine soft-block signature, deliberately NOT "no video formats
        # found" — this test is about which cookie path gets used, not about
        # photo/carousel classification (see test_photo_carousel_* for that).
        raise RuntimeError("checkpoint required")

    monkeypatch.setattr(fetcher, "_run_ytdlp", _record)
    monkeypatch.setattr(fetcher, "fetch_og_metadata", lambda p: None)

    with pytest.raises(FetchDegraded):
        fetcher.fetch_reel("OGTEST01", PERMALINK)

    assert seen == [None, str(cookies)]  # anonymous first, then the RESOLVED path


# --- timing-bug investigation: expected download size + concurrent-fetch lock ----
#
# Real incident: ffmpeg's CalledProcessError fired at the exact Render-log moment
# a download showed 63.9% progress, with that download's progress continuing to
# 100% afterward. Confirmed by reading _run_ytdlp: it's fully synchronous, no
# progress_hooks, no threading -- extract_info(download=True) cannot return before
# the file is completely written. The real bug: fetch_reel had no lock, so two
# background-task threads (FastAPI's BackgroundTasks run sync functions in a
# threadpool) could run yt-dlp/ffmpeg concurrently, interleaving their Render log
# lines -- making an unrelated reel's ffmpeg failure look like it belonged to
# THIS reel's still-downloading file. See PROGRESS.md for the full writeup.

def test_expected_download_size_from_requested_downloads():
    info = {"requested_downloads": [{"filesize": 5_000_000}], "filesize": 999}
    assert fetcher._expected_download_size(info) == 5_000_000


def test_expected_download_size_falls_back_to_filesize_approx():
    info = {"requested_downloads": [{"filesize_approx": 4_200_000}]}
    assert fetcher._expected_download_size(info) == 4_200_000


def test_expected_download_size_falls_back_to_top_level_info():
    info = {"requested_downloads": [], "filesize": 3_100_000}
    assert fetcher._expected_download_size(info) == 3_100_000


def test_expected_download_size_none_when_nothing_reported():
    assert fetcher._expected_download_size({}) is None
    assert fetcher._expected_download_size({"requested_downloads": [{}]}) is None


def test_info_to_reel_data_carries_expected_video_size():
    info = {
        "_video_path": "/tmp/SIZE01.mp4",
        "requested_downloads": [{"filesize": 12_345}],
    }
    reel = fetcher._info_to_reel_data("SIZE01", PERMALINK, info)
    assert reel.expected_video_size == 12_345


def test_fetch_reel_serializes_concurrent_calls(monkeypatch, tmp_path):
    """The actual fix: two fetch_reel calls from different threads must never
    run their yt-dlp bodies concurrently -- proving the lock genuinely
    serializes, not just that it exists."""
    import threading
    import time as time_module

    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# netscape cookie file")
    monkeypatch.setattr(fetcher, "BURNER_COOKIES_FILE", str(cookies))
    monkeypatch.setattr(fetcher, "_enforce_rate_discipline", lambda s, p: None)

    intervals: list[tuple[float, float]] = []
    lock_for_list = threading.Lock()

    def _slow_ytdlp(url, cookiefile):
        start = time_module.monotonic()
        time_module.sleep(0.05)
        end = time_module.monotonic()
        with lock_for_list:
            intervals.append((start, end))
        return {"_video_path": "/tmp/x.mp4"}

    monkeypatch.setattr(fetcher, "_run_ytdlp", _slow_ytdlp)

    threads = [
        threading.Thread(target=fetcher.fetch_reel, args=(f"CONC{i}", PERMALINK))
        for i in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(intervals) == 3
    intervals.sort()
    for (_s1, e1), (s2, _e2) in zip(intervals, intervals[1:]):
        assert e1 <= s2  # each interval fully finishes before the next starts


def test_run_ytdlp_registers_no_hooks_or_threading():
    """Structural confirmation (not just a claim in a comment): _run_ytdlp's
    opts dict registers no progress_hooks/postprocessors keys, and the
    function body itself spawns no threads and awaits nothing -- nothing that
    could hand control back before the file is fully written. Checks the
    function body only (not the docstring, which discusses these terms)."""
    import inspect

    source = inspect.getsource(fetcher._run_ytdlp)
    body = source.split('"""', 2)[-1]  # strip the docstring, keep only the code
    assert '"progress_hooks"' not in body
    assert '"postprocessors"' not in body
    assert "Thread" not in body and "await " not in body and "async " not in body
