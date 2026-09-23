"""public_api._display_title: what a visitor sees for a row whose extraction
degraded and whose stored Title is therefore the raw caption."""
from app.public_api import PENDING_TITLE, _display_title


def test_real_extracted_title_is_never_rewritten():
    t = "Connects Claude to other apps so it can run multi-step tasks #not-a-caption"
    assert _display_title(t, "tutorial") == t


def test_hashtags_and_mentions_are_stripped():
    assert _display_title("Build on YouTube #selfimprovement #aitools @someone", "unknown") == "Build on YouTube"


def test_first_real_sentence_is_used():
    t = "These effects look complicated. Until you find this site.\n\nMore text"
    assert _display_title(t, "unknown") == "These effects look complicated."


def test_comment_gate_line_is_skipped_so_the_keyword_never_shows():
    t = 'comment "REPOS" & i\'ll send you the link.\n\nAgent Reach gives your agent eyes on the internet.'
    out = _display_title(t, "unknown")
    assert "REPOS" not in out and "comment" not in out.lower()
    assert out.startswith("Agent Reach")


def test_keyword_dump_and_empty_caption_fall_back_to_pending():
    assert _display_title("[ claude, claudecode, ai ]\n#ai #tools", "unknown") == PENDING_TITLE
    assert _display_title("Comment 'GOOGLE' and I'll send it over.", "") == PENDING_TITLE


def test_long_sentence_is_cut_on_a_word_boundary():
    out = _display_title("word " * 60, "unknown")
    assert len(out) <= 121 and out.endswith("…") and not out.endswith(" …")
