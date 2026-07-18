import app.gemini_pipe as gemini_pipe
from app.gemini_pipe import _merge_comment_gate, run_extraction
from app.models import CommentGate, Extraction, ReelData

GATED_CAPTION = "New guide! Comment 'SEND' and I'll DM you the link"


def test_run_extraction_degrades_when_no_video():
    reel = ReelData(
        shortcode="NOVID001",
        permalink="https://www.instagram.com/reel/NOVID001/",
        video_path=None,
        caption="whatever caption we had",
    )
    extraction = run_extraction(reel, note=None, taxonomy=[])
    assert extraction.has_speech is None
    assert extraction.main_point == "whatever caption we had"
    assert extraction.content_type == "unknown"


def test_comment_gate_detected_on_degraded_no_video():
    """Bug A: the gate is a pure caption regex — a missing video must NOT skip it."""
    reel = ReelData(
        shortcode="GATENOVID",
        permalink="https://www.instagram.com/reel/GATENOVID/",
        video_path=None,
        caption=GATED_CAPTION,
    )
    extraction = run_extraction(reel, note=None, taxonomy=[])
    assert extraction.comment_gate.detected is True
    assert extraction.comment_gate.keyword == "SEND"


def test_comment_gate_detected_when_gemini_call_fails_entirely(monkeypatch):
    """Bug A: even when the Gemini call raises on every attempt (the
    `except Exception: break` path), the caption gate regex must still fire."""
    reel = ReelData(
        shortcode="GATEFAIL",
        permalink="https://www.instagram.com/reel/GATEFAIL/",
        video_path="/tmp/GATEFAIL.mp4",
        caption=GATED_CAPTION,
    )
    # skip real ffmpeg; force the model call to blow up on every attempt
    monkeypatch.setattr(gemini_pipe, "_extract_audio", lambda p: "/tmp/GATEFAIL.m4a")

    def _boom(audio_path, prompt):
        raise RuntimeError("gemini 500")

    monkeypatch.setattr(gemini_pipe, "_call_gemini", _boom)

    extraction = run_extraction(reel, note=None, taxonomy=[])
    assert extraction.content_type == "unknown"  # confirms we took the degraded path
    assert extraction.comment_gate.detected is True
    assert extraction.comment_gate.keyword == "SEND"


def test_comment_gate_detected_when_ffmpeg_fails(monkeypatch):
    """Bug A: an ffmpeg failure (CalledProcessError) also degrades — gate still fires."""
    import subprocess

    reel = ReelData(
        shortcode="GATEFFMPEG",
        permalink="https://www.instagram.com/reel/GATEFFMPEG/",
        video_path="/tmp/GATEFFMPEG.mp4",
        caption=GATED_CAPTION,
    )

    def _ffmpeg_boom(video_path):
        raise subprocess.CalledProcessError(1, "ffmpeg")

    monkeypatch.setattr(gemini_pipe, "_extract_audio", _ffmpeg_boom)

    extraction = run_extraction(reel, note=None, taxonomy=[])
    assert extraction.comment_gate.detected is True
    assert extraction.comment_gate.keyword == "SEND"


def test_merge_comment_gate_regex_catches_what_model_missed():
    extraction = Extraction(main_point="x", comment_gate=CommentGate(detected=False))
    _merge_comment_gate(extraction, "Comment 'SEND' below for the link")
    assert extraction.comment_gate.detected is True
    assert extraction.comment_gate.keyword == "SEND"


def test_merge_comment_gate_keeps_models_own_keyword():
    extraction = Extraction(
        main_point="x", comment_gate=CommentGate(detected=True, keyword="LINK")
    )
    _merge_comment_gate(extraction, "no regex match here")
    assert extraction.comment_gate.keyword == "LINK"


def test_merge_comment_gate_noop_when_neither_detects():
    extraction = Extraction(main_point="x", comment_gate=CommentGate(detected=False))
    _merge_comment_gate(extraction, "just a normal caption")
    assert extraction.comment_gate.detected is False
    assert extraction.comment_gate.keyword is None


# --- BUG 2 regression: detected/keyword must never disagree -------------------
#
# Real incident: DajFASZODlj had gate_keyword="International" with
# comment_gate=False in Notion; DaQIJHnP6zn had gate_keyword="CODING" with the
# same mismatch. Root cause: Gemini's own structured output can set a keyword
# while independently leaving detected=False, and when the caption's gate
# phrasing also doesn't match our regex, nothing corrected it.

def test_merge_comment_gate_forces_detected_true_when_model_sets_keyword_without_detected():
    extraction = Extraction(
        main_point="x", comment_gate=CommentGate(detected=False, keyword="International")
    )
    _merge_comment_gate(extraction, "just a normal caption with no gate phrasing our regex covers")
    assert extraction.comment_gate.detected is True
    assert extraction.comment_gate.keyword == "International"


def test_run_extraction_sets_priority_on_the_success_path(monkeypatch):
    """Priority is computed and attached at the same finalization point as
    comment_gate — never left at the model default regardless of path taken."""
    reel = ReelData(
        shortcode="PRIOK1", permalink="https://www.instagram.com/reel/PRIOK1/",
        video_path="/tmp/PRIOK1.mp4", caption="no gate here",
    )
    extraction = Extraction(main_point="x", topic_tags=["fitness"], value_score=5)
    monkeypatch.setattr(gemini_pipe, "_extract_audio", lambda video_path: "/tmp/a.m4a")
    monkeypatch.setattr(gemini_pipe, "_call_gemini", lambda audio_path, prompt: extraction.model_dump_json())

    result = run_extraction(reel, note=None, taxonomy=[])
    assert result.priority == "High"  # value_score 5 >= 4


def test_merge_comment_gate_invariant_holds_across_all_input_combinations():
    """Exhaustive: whatever detected/keyword the model hands in, and whatever
    the regex does or doesn't find, the merged result must never end up with a
    keyword set and detected False."""
    for detected in (True, False):
        for keyword in (None, "SOMEWORD"):
            for caption in (None, "no gate phrasing here", "comment 'SEND' for the guide"):
                extraction = Extraction(
                    main_point="x", comment_gate=CommentGate(detected=detected, keyword=keyword)
                )
                _merge_comment_gate(extraction, caption)
                assert extraction.comment_gate.detected or not extraction.comment_gate.keyword


# --- compute_priority: computed field replacing decorative-only value_score ---

def test_compute_priority_high_from_value_score():
    assert gemini_pipe.compute_priority(["fitness"], 4) == "High"
    assert gemini_pipe.compute_priority(["fitness"], 5) == "High"


def test_compute_priority_medium_at_value_score_three():
    assert gemini_pipe.compute_priority(["fitness"], 3) == "Medium"


def test_compute_priority_low_otherwise():
    assert gemini_pipe.compute_priority(["fitness"], 1) == "Low"
    assert gemini_pipe.compute_priority(["fitness"], 2) == "Low"


def test_compute_priority_high_from_claude_keyword_even_at_low_value_score():
    """A Claude/Anthropic-related topic forces High regardless of value_score —
    the whole point of the OR condition."""
    assert gemini_pipe.compute_priority(["claude-code"], 1) == "High"
    assert gemini_pipe.compute_priority(["anthropic"], 2) == "High"


def test_compute_priority_claude_keyword_matching_is_case_insensitive():
    assert gemini_pipe.compute_priority(["Claude-Code"], 1) == "High"
    assert gemini_pipe.compute_priority(["ANTHROPIC"], 1) == "High"
    assert gemini_pipe.compute_priority(["MCP-Servers"], 1) == "High"


def test_compute_priority_claude_keyword_substring_match_within_topic():
    """Each keyword in CLAUDE_KEYWORDS matches as a substring anywhere in a
    topic tag, not just an exact-equal tag."""
    assert gemini_pipe.compute_priority(["my-claude-skills-list"], 1) == "High"
    assert gemini_pipe.compute_priority(["claude-ai-tools"], 1) == "High"


def test_compute_priority_no_claude_keyword_falls_through_to_value_score():
    assert gemini_pipe.compute_priority(["fitness", "cooking"], 5) == "High"   # via value_score
    assert gemini_pipe.compute_priority(["fitness", "cooking"], 3) == "Medium"
    assert gemini_pipe.compute_priority(["fitness", "cooking"], 1) == "Low"


def test_compute_priority_no_topics_at_all():
    assert gemini_pipe.compute_priority([], 4) == "High"
    assert gemini_pipe.compute_priority([], 3) == "Medium"
    assert gemini_pipe.compute_priority([], 1) == "Low"
