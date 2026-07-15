import pytest

from app.fetcher import detect_comment_gate, normalize_url

URL_SHAPES = [
    ("https://www.instagram.com/reel/C9xAbC12345/", "C9xAbC12345"),
    ("https://instagram.com/reel/C9xAbC12345/?utm_source=ig_web_copy_link", "C9xAbC12345"),
    ("https://www.instagram.com/p/C9xAbC12345/", "C9xAbC12345"),
    ("https://www.instagram.com/reels/C9xAbC12345/", "C9xAbC12345"),
    ("http://instagram.com/reel/C9xAbC12345", "C9xAbC12345"),
    ("Check this out! https://www.instagram.com/reel/C9xAbC12345/?igsh=xyz123 so good", "C9xAbC12345"),
]


@pytest.mark.parametrize("url,expected_shortcode", URL_SHAPES)
def test_normalize_url_shapes(url, expected_shortcode):
    assert normalize_url(url) == expected_shortcode


def test_normalize_url_raises_on_non_instagram_url():
    with pytest.raises(ValueError):
        normalize_url("https://example.com/not-a-reel")


def test_detect_comment_gate_positive():
    assert detect_comment_gate("Comment 'SEND' below and I'll DM you the link") == "SEND"


def test_detect_comment_gate_negative_no_gate():
    assert detect_comment_gate("just a normal caption about my day") is None


def test_detect_comment_gate_requires_uppercase_keyword():
    # Matches CLAUDE.md's literal regex intent: only fires on an actually-capitalized
    # keyword, the typical gate style ("comment SEND"), not on every use of the word.
    assert detect_comment_gate("comment your thoughts below") is None


def test_detect_comment_gate_none_caption():
    assert detect_comment_gate(None) is None
