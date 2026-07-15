from app.gemini_pipe import _merge_comment_gate, run_extraction
from app.models import CommentGate, Extraction, ReelData


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
