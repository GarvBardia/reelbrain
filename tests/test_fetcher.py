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


def test_detect_comment_gate_requires_uppercase_when_unquoted():
    # Unquoted keywords still need ALL-CAPS to separate them from ordinary prose.
    assert detect_comment_gate("comment your thoughts below") is None


def test_detect_comment_gate_quoted_mixed_case():
    """The DajFASZODlj miss: quoted keyword in ordinary Title case. Quoting is the
    creator's own signal, so case must not matter inside quotes."""
    assert detect_comment_gate('Comment "International" for free Guide') == "International"


def test_detect_comment_gate_quoted_lowercase_and_curly_quotes():
    assert detect_comment_gate("Comment “growth” and I'll DM you") == "growth"
    assert detect_comment_gate("comment 'links' for the resource") == "links"


def test_detect_comment_gate_unquoted_allcaps_still_works():
    assert detect_comment_gate("comment SEND below for the guide") == "SEND"


def test_detect_comment_gate_none_caption():
    assert detect_comment_gate(None) is None


# --- BUG 2: emoji-drop-for-DM gate style --------------------------------------
#
# Real miss: Dap3IoNo4Kt ("Drop your 🔥 emoji to grab all in ur dms") wasn't
# detected — no literal "comment", so COMMENT_GATE_RE never fires.

def test_detect_comment_gate_emoji_drop_style():
    assert detect_comment_gate("Drop your 🔥 emoji to grab all in ur dms") == "🔥"


def test_detect_comment_gate_emoji_drop_style_variants():
    assert detect_comment_gate("drop a 🎯 emoji and I'll dm you the link") == "🎯"
    assert detect_comment_gate("Drop the 💌 emoji below, sliding into your dm") == "💌"


def test_detect_comment_gate_emoji_drop_requires_dm_nearby():
    """Ordinary reaction-bait — 'drop a fire emoji' with no DM mention at all —
    must NOT match. This is deliberately common, unrelated caption phrasing."""
    assert detect_comment_gate("Drop your 🔥 emoji if you loved this one") is None


def test_detect_comment_gate_emoji_drop_requires_an_actual_emoji_token():
    """'Drop your comment' has no non-alphanumeric token before 'emoji' (there's
    no 'emoji' at all here) — must not false-positive just because 'drop' and
    'dm' both appear somewhere in the caption."""
    assert detect_comment_gate("Drop your comment below, I'll dm you the guide") is None


def test_detect_comment_gate_emoji_drop_requires_the_word_emoji():
    """'Drop a 🔥 and dm me' has the verb, an emoji-shaped token, and 'dm' — but
    never the literal word 'emoji' — so it must not match either; this keeps
    the pattern scoped to the specific reported phrasing rather than any
    drop+symbol+dm combination."""
    assert detect_comment_gate("Drop a 🔥 and dm me for the list") is None
