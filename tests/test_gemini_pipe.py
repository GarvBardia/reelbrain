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
