"""app/carousel.py + gemini_pipe.run_carousel_extraction — all mocked.

The embed-HTML fixtures below use the REAL double-escaped shape captured from
a live Instagram embed page, not an invented one — that escaping is the whole
reason a naive regex found only 1 of 10 slides during development.
"""
import pytest

from app import carousel, gemini_pipe


# Real shape: \"display_url\":\"https:\\/\\/scontent-...\.webp?x=1%3D\",
def _embed_html(urls, include_avatar=True):
    parts = []
    if include_avatar:
        parts.append(
            r'\"profile_pic\",\"display_url\":\"https:\\/\\/scontent-maa3-2.cdninstagram.com'
            r'\/v\/t51.2885-19\/366492500_avatar_n.jpg\",'
        )
    for i, u in enumerate(urls):
        escaped = u.replace("/", r"\\/")
        parts.append(rf'\"id\":\"slide{i}\",\"is_video\":false,\"display_url\":\"{escaped}\",')
    return "<html>" + "".join(parts) + "</html>"


SLIDES = [
    f"https://scontent-maa3-1.cdninstagram.com/v/t51.82787-15/slide{i}_n.webp?_nc_cat=1"
    for i in range(3)
]


# --- slide URL extraction ---------------------------------------------------------

def test_extracts_every_slide_in_order():
    urls = carousel.extract_slide_urls(_embed_html(SLIDES))
    assert urls == SLIDES  # order preserved — carousels are sequential


def test_avatar_is_never_mistaken_for_a_slide():
    urls = carousel.extract_slide_urls(_embed_html(SLIDES, include_avatar=True))
    assert all("/t51.2885-19/" not in u for u in urls)
    assert len(urls) == 3


def test_duplicate_display_urls_are_collapsed():
    html = _embed_html([SLIDES[0], SLIDES[0], SLIDES[1]], include_avatar=False)
    assert carousel.extract_slide_urls(html) == [SLIDES[0], SLIDES[1]]


def test_no_display_urls_yields_empty():
    assert carousel.extract_slide_urls("<html>nothing here</html>") == []


def test_unescape_handles_double_escaped_url():
    assert carousel._unescape_url(r"https:\\/\\/x.com\\/a") == "https://x.com/a"
    assert carousel._unescape_url(r"https://x.com/a?b%3Dc") == "https://x.com/a?b=c"


# --- fetch_slide_urls -------------------------------------------------------------

class _Resp:
    def __init__(self, text="", status=200, content=b""):
        self.text, self.status_code, self.content = text, status, content

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_fetch_slide_urls_success(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(text=_embed_html(SLIDES)))
    urls, error = carousel.fetch_slide_urls("ABC")
    assert urls == SLIDES and error is None


def test_fetch_slide_urls_reports_http_error(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(status=404))
    urls, error = carousel.fetch_slide_urls("ABC")
    assert urls == [] and "404" in error


def test_fetch_slide_urls_reports_no_entries(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(text="<html>empty</html>"))
    urls, error = carousel.fetch_slide_urls("ABC")
    assert urls == [] and "no display_url" in error


def test_fetch_slide_urls_never_raises(monkeypatch):
    import httpx

    def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(httpx, "get", _boom)
    urls, error = carousel.fetch_slide_urls("ABC")
    assert urls == [] and "embed request failed" in error


def test_slide_cap_applied(monkeypatch):
    import httpx
    many = [f"https://scontent-x.cdninstagram.com/v/t51.82787-15/s{i}_n.webp" for i in range(30)]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(text=_embed_html(many, include_avatar=False)))
    urls, _ = carousel.fetch_slide_urls("ABC")
    assert len(urls) == carousel.MAX_SLIDES


# --- download_slides --------------------------------------------------------------

def test_download_skips_tiny_images_but_keeps_real_ones(monkeypatch):
    import httpx
    big = b"x" * (carousel.MIN_IMAGE_BYTES + 10)
    responses = {"a": _Resp(content=big), "b": _Resp(content=b"tiny"), "c": _Resp(content=big)}
    monkeypatch.setattr(httpx, "get", lambda url, **k: responses[url])
    images, error = carousel.download_slides(["a", "b", "c"])
    assert len(images) == 2 and error is None


def test_partial_download_is_still_success(monkeypatch):
    """6-of-7 slides read still beats caption-only, so a partial set is kept."""
    import httpx
    big = b"x" * (carousel.MIN_IMAGE_BYTES + 10)

    def _get(url, **k):
        if url == "bad":
            raise RuntimeError("timeout")
        return _Resp(content=big)

    monkeypatch.setattr(httpx, "get", _get)
    images, error = carousel.download_slides(["ok", "bad", "ok2"])
    assert len(images) == 2 and error is None


def test_all_downloads_failing_is_an_error(monkeypatch):
    import httpx

    def _boom(*a, **k):
        raise RuntimeError("nope")

    monkeypatch.setattr(httpx, "get", _boom)
    images, error = carousel.download_slides(["a", "b"])
    assert images == [] and "every slide image failed" in error


# --- fetch_carousel_images: slide_count vs images distinction ---------------------

def test_slide_count_reported_even_when_downloads_fail(monkeypatch):
    """The explicit requirement: "no images available" must stay
    distinguishable from "images available but unread"."""
    monkeypatch.setattr(carousel, "fetch_slide_urls", lambda sc: (SLIDES, None))
    monkeypatch.setattr(carousel, "download_slides", lambda urls: ([], "every slide image failed to download"))
    images, slide_count, error = carousel.fetch_carousel_images("ABC")
    assert images == []
    assert slide_count == 3          # we KNEW about 3 slides
    assert "failed to download" in error


def test_no_slides_at_all_reports_zero_count(monkeypatch):
    monkeypatch.setattr(carousel, "fetch_slide_urls", lambda sc: ([], "embed returned HTTP 404"))
    images, slide_count, error = carousel.fetch_carousel_images("ABC")
    assert (images, slide_count) == ([], 0)
    assert "404" in error


# --- run_carousel_extraction ------------------------------------------------------

def test_carousel_extraction_returns_none_without_images():
    assert gemini_pipe.run_carousel_extraction([], "cap", "creator", None, []) is None


def test_carousel_extraction_parses_and_computes_priority(monkeypatch):
    raw = (
        '{"main_point": "Install the Firecrawl MCP server to scrape docs into Claude.",'
        '"supporting_points": ["slide 2 detail"], "topic_tags": ["claude-ai"],'
        '"value_score": 5, "content_type": "tutorial"}'
    )
    monkeypatch.setattr(gemini_pipe, "_call_gemini_carousel", lambda prompt, images: raw)
    extraction = gemini_pipe.run_carousel_extraction([b"img"], "cap", "creator", None, [])
    assert extraction is not None
    assert "Firecrawl" in extraction.main_point
    assert extraction.priority == "High"   # claude-ai topic + value 5


def test_carousel_extraction_merges_comment_gate_from_caption(monkeypatch):
    raw = '{"main_point": "x", "topic_tags": [], "value_score": 3, "content_type": "tutorial"}'
    monkeypatch.setattr(gemini_pipe, "_call_gemini_carousel", lambda prompt, images: raw)
    extraction = gemini_pipe.run_carousel_extraction(
        [b"img"], 'Comment "MCP" and I will send you the link!', "creator", None, [])
    assert extraction.comment_gate.detected is True
    assert extraction.comment_gate.keyword == "MCP"


def test_carousel_extraction_returns_none_on_api_failure(monkeypatch):
    def _boom(prompt, images):
        raise RuntimeError("gemini 500")

    monkeypatch.setattr(gemini_pipe, "_call_gemini_carousel", _boom)
    assert gemini_pipe.run_carousel_extraction([b"img"], "cap", "c", None, []) is None


def test_carousel_extraction_retries_once_on_bad_json(monkeypatch):
    calls = []
    good = '{"main_point": "ok", "topic_tags": [], "value_score": 3, "content_type": "tutorial"}'

    def _flaky(prompt, images):
        calls.append(prompt)
        return "not json" if len(calls) == 1 else good

    monkeypatch.setattr(gemini_pipe, "_call_gemini_carousel", _flaky)
    extraction = gemini_pipe.run_carousel_extraction([b"img"], "cap", "c", None, [])
    assert extraction is not None and len(calls) == 2


def test_carousel_prompt_carries_slide_count_and_sequence_instruction():
    prompt = gemini_pipe._build_carousel_prompt("cap", "creator", None, ["claude-ai"], 7)
    assert "7" in prompt
    assert "in order" in prompt.lower()
    assert "plain_summary" in prompt
