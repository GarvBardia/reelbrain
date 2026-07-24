import logging
import subprocess

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


# --- run_caption_only_extraction: photo/carousel posts get a real summary -----
#
# Fix: yt-dlp can never fetch photo/carousel posts (video-only), but a caption
# is often recoverable via the OG-tag fallback. Previously that caption was
# just stored raw as a bare placeholder. This runs it through the SAME
# structured Gemini call as a video reel, minus the audio/video upload.

SUBSTANTIAL_CAPTION = (
    "New free guide dropping today! I break down the exact 5-step morning "
    "routine that finally fixed my sleep schedule after years of insomnia. "
    "Comment 'SEND' and I'll DM you the full PDF."
)


def test_caption_only_extraction_produces_structured_output(monkeypatch):
    extraction_out = Extraction(
        main_point="A 5-step morning routine that fixed the creator's sleep schedule.",
        topic_tags=["sleep", "habits"],
        content_type="resource_drop",
        comment_gate=CommentGate(detected=True, keyword="SEND", promised_resource="sleep routine PDF"),
        value_score=4,
    )
    monkeypatch.setattr(
        gemini_pipe, "_call_gemini_text_only", lambda prompt: extraction_out.model_dump_json()
    )

    result = gemini_pipe.run_caption_only_extraction(
        SUBSTANTIAL_CAPTION, creator="sleepcoachjane", note=None, taxonomy=[],
    )

    assert result.main_point == "A 5-step morning routine that fixed the creator's sleep schedule."
    assert result.topic_tags == ["sleep", "habits"]
    assert result.value_score == 4
    assert result.priority == "High"          # via value_score >= 4
    assert result.comment_gate.detected is True
    assert result.comment_gate.keyword == "SEND"
    assert result.transcript == ""
    assert result.has_speech is False


def test_caption_only_extraction_prompt_carries_no_audio_upload(monkeypatch):
    """The whole point: no file upload, just the prompt text — confirms the
    text-only call path is actually being used, not the audio one."""
    calls = []

    def _fake_call(prompt):
        calls.append(prompt)
        return Extraction(main_point="x", value_score=2).model_dump_json()

    monkeypatch.setattr(gemini_pipe, "_call_gemini_text_only", _fake_call)
    monkeypatch.setattr(gemini_pipe, "_call_gemini", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("must not call the audio/video extraction path")
    ))

    gemini_pipe.run_caption_only_extraction(SUBSTANTIAL_CAPTION, "someone", None, [])
    assert len(calls) == 1
    assert SUBSTANTIAL_CAPTION in calls[0]


def test_caption_only_extraction_merges_comment_gate_and_sets_priority(monkeypatch):
    """Same finalization steps as the video path: regex-merge + priority."""
    extraction_out = Extraction(main_point="x", value_score=1, topic_tags=["cooking"])
    monkeypatch.setattr(
        gemini_pipe, "_call_gemini_text_only", lambda prompt: extraction_out.model_dump_json()
    )

    result = gemini_pipe.run_caption_only_extraction(
        "Comment 'LINK' below and I'll send you the resource, promise, this caption is long enough!",
        creator=None, note=None, taxonomy=[],
    )
    assert result.comment_gate.detected is True
    assert result.comment_gate.keyword == "LINK"
    assert result.priority == "Low"  # value_score 1, no Claude-related topic -> Low, computed either way


def test_caption_only_extraction_falls_back_to_degraded_when_thin(monkeypatch):
    """Caption under MIN_CAPTION_WORDS_FOR_EXTRACTION words -> honest placeholder,
    never a Gemini call (no risk of hallucinating content from almost nothing)."""
    def _must_not_be_called(prompt):
        raise AssertionError("must not call Gemini for a too-thin caption")

    monkeypatch.setattr(gemini_pipe, "_call_gemini_text_only", _must_not_be_called)

    result = gemini_pipe.run_caption_only_extraction("just a few words here", None, None, [])
    assert result.main_point == "just a few words here"
    assert result.content_type == "unknown"  # confirms the degraded path, not a real extraction


def test_caption_only_extraction_falls_back_to_degraded_when_caption_is_none(monkeypatch):
    def _must_not_be_called(prompt):
        raise AssertionError("must not call Gemini with no caption at all")

    monkeypatch.setattr(gemini_pipe, "_call_gemini_text_only", _must_not_be_called)

    result = gemini_pipe.run_caption_only_extraction(None, None, None, [])
    assert result.content_type == "unknown"
    assert result.main_point == "No caption or transcript available."


def test_caption_only_extraction_falls_back_when_gemini_call_fails(monkeypatch):
    def _boom(prompt):
        raise RuntimeError("gemini 500")

    monkeypatch.setattr(gemini_pipe, "_call_gemini_text_only", _boom)

    result = gemini_pipe.run_caption_only_extraction(SUBSTANTIAL_CAPTION, None, None, [])
    assert result.content_type == "unknown"  # degraded path
    # gate regex still fires even on the degraded path (mirrors run_extraction's guarantee)
    assert result.comment_gate.detected is True
    assert result.comment_gate.keyword == "SEND"


# --- run_resource_extraction (scripts/ingest_resources.py's Gemini call) -------

SUBSTANTIAL_RESOURCE_TEXT = (
    "This guide walks through a five-step workflow for building a scroll-based "
    "animation site. Step one: install the library. Step two: configure the "
    "scroll trigger. Step three: wire up the timeline. Step four: add easing. "
    "Step five: ship it. Plenty of real content here for a proper summary."
)


def test_resource_extraction_produces_structured_output(monkeypatch):
    from app.models import ResourceExtraction

    extraction_out = ResourceExtraction(
        summary="A five-step workflow for building scroll-based animation sites.",
        key_takeaways=["Install the library first", "Configure the scroll trigger"],
        topic_tags=["web-design", "developer-tools"],
        resource_kind="github_repo",
    )
    monkeypatch.setattr(gemini_pipe, "_call_gemini_resource", lambda prompt: extraction_out.model_dump_json())

    result = gemini_pipe.run_resource_extraction(
        SUBSTANTIAL_RESOURCE_TEXT, "github_repo", "Build scroll animations", taxonomy=["web-design"],
    )

    assert result is not None
    assert result.summary == "A five-step workflow for building scroll-based animation sites."
    assert result.topic_tags == ["web-design", "developer-tools"]
    assert result.resource_kind == "github_repo"


def test_resource_extraction_returns_none_when_content_too_thin(monkeypatch):
    def _must_not_be_called(prompt):
        raise AssertionError("must not call Gemini for too-thin content")

    monkeypatch.setattr(gemini_pipe, "_call_gemini_resource", _must_not_be_called)

    result = gemini_pipe.run_resource_extraction("just a few words", "web_article", "Title", [])
    assert result is None


def test_resource_extraction_returns_none_when_content_is_empty(monkeypatch):
    def _must_not_be_called(prompt):
        raise AssertionError("must not call Gemini with no content at all")

    monkeypatch.setattr(gemini_pipe, "_call_gemini_resource", _must_not_be_called)

    result = gemini_pipe.run_resource_extraction("", "web_article", "Title", [])
    assert result is None


def test_resource_extraction_returns_none_when_gemini_call_fails(monkeypatch):
    def _boom(prompt):
        raise RuntimeError("gemini 500")

    monkeypatch.setattr(gemini_pipe, "_call_gemini_resource", _boom)

    result = gemini_pipe.run_resource_extraction(SUBSTANTIAL_RESOURCE_TEXT, "web_article", "Title", [])
    assert result is None  # never a placeholder -- caller must skip writing entirely


def test_resource_extraction_retries_once_on_validation_failure_then_succeeds(monkeypatch):
    from app.models import ResourceExtraction

    good = ResourceExtraction(summary="Real summary.", resource_kind="pdf")
    calls = []

    def _flaky(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return "{not valid json"
        return good.model_dump_json()

    monkeypatch.setattr(gemini_pipe, "_call_gemini_resource", _flaky)

    result = gemini_pipe.run_resource_extraction(SUBSTANTIAL_RESOURCE_TEXT, "pdf", "Title", [])
    assert len(calls) == 2
    assert result is not None
    assert result.summary == "Real summary."


def test_video_extraction_path_completely_unaffected_by_caption_only_addition(monkeypatch):
    """Regression guard: a normal video reel must still go through _call_gemini
    (audio path), never _call_gemini_text_only — this is purely an addition
    for the photo/carousel fallback, not a change to normal reel processing."""
    reel = ReelData(
        shortcode="STILLVID", permalink="https://www.instagram.com/reel/STILLVID/",
        video_path="/tmp/STILLVID.mp4", caption=SUBSTANTIAL_CAPTION,
    )
    monkeypatch.setattr(gemini_pipe, "_extract_audio", lambda p: "/tmp/STILLVID.m4a")

    def _fake_audio_call(audio_path, prompt):
        return Extraction(main_point="video-derived summary", value_score=4).model_dump_json()

    monkeypatch.setattr(gemini_pipe, "_call_gemini", _fake_audio_call)
    monkeypatch.setattr(gemini_pipe, "_call_gemini_text_only", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("must not call the caption-only path for a normal video reel")
    ))

    result = run_extraction(reel, note=None, taxonomy=[])
    assert result.main_point == "video-derived summary"


# --- research_context: Gemini call 2, per-named-entity, search-grounded ---------

class _FakeChunk:
    pass


class _FakeGroundingMetadata:
    def __init__(self, chunks):
        self.grounding_chunks = chunks


class _FakeCandidate:
    def __init__(self, chunks):
        self.grounding_metadata = _FakeGroundingMetadata(chunks)


class _FakeGeminiResponse:
    def __init__(self, text, chunks):
        self.text = text
        self.candidates = [_FakeCandidate(chunks)]


def test_grounding_found_results_true_when_chunks_present():
    response = _FakeGeminiResponse("some text", chunks=[_FakeChunk()])
    assert gemini_pipe._grounding_found_results(response) is True


def test_grounding_found_results_false_when_chunks_empty():
    response = _FakeGeminiResponse("some text", chunks=[])
    assert gemini_pipe._grounding_found_results(response) is False


def test_grounding_found_results_false_when_chunks_none():
    response = _FakeGeminiResponse("some text", chunks=None)
    assert gemini_pipe._grounding_found_results(response) is False


def test_grounding_found_results_false_on_malformed_response():
    class _Weird:
        pass

    assert gemini_pipe._grounding_found_results(_Weird()) is False
    assert gemini_pipe._grounding_found_results(None) is False


def test_run_research_context_uses_real_text_when_grounded(monkeypatch):
    def _fake_call(entity):
        assert entity == "Cleanlist.ai"
        return _FakeGeminiResponse(
            "Cleanlist.ai is a Chrome extension for scraping LinkedIn contact data.",
            chunks=[_FakeChunk()],
        )

    monkeypatch.setattr(gemini_pipe, "_call_gemini_research", _fake_call)

    result = gemini_pipe.run_research_context(["Cleanlist.ai"])
    assert len(result) == 1
    assert result[0].topic == "Cleanlist.ai"
    assert "Chrome extension for scraping LinkedIn" in result[0].context


def test_run_research_context_writes_honest_marker_when_grounding_empty(monkeypatch):
    """CRITICAL (point 2): even though Gemini's own response.text here contains
    a plausible-sounding answer, grounding_chunks is empty -- meaning Google
    Search found nothing. The unlabeled training-data guess must NEVER be used;
    the literal not-found marker must be written instead."""
    def _fake_call(entity):
        return _FakeGeminiResponse(
            "This is a plausible-sounding answer from Gemini's own training data.",
            chunks=[],  # search grounding found NOTHING
        )

    monkeypatch.setattr(gemini_pipe, "_call_gemini_research", _fake_call)

    result = gemini_pipe.run_research_context(["SomeObscureTool"])
    assert len(result) == 1
    assert result[0].topic == "SomeObscureTool"
    assert result[0].context == "not found via search"
    assert "plausible-sounding answer" not in result[0].context


def test_run_research_context_empty_entities_returns_empty_without_calling_gemini(monkeypatch):
    def _must_not_be_called(entity):
        raise AssertionError("must not call Gemini with no named entities")

    monkeypatch.setattr(gemini_pipe, "_call_gemini_research", _must_not_be_called)

    assert gemini_pipe.run_research_context([]) == []
    assert gemini_pipe.run_research_context(None) == []


def test_run_research_context_one_entity_failure_does_not_block_others(monkeypatch):
    calls = []

    def _fake_call(entity):
        calls.append(entity)
        if entity == "BrokenTool":
            raise RuntimeError("gemini 500")
        return _FakeGeminiResponse(f"real info about {entity}", chunks=[_FakeChunk()])

    monkeypatch.setattr(gemini_pipe, "_call_gemini_research", _fake_call)
    # Phase 3: a failed grounded call now routes to the web-fetch fallback
    # instead of silently skipping; nothing fetchable -> honest not-found.
    from app import web_research
    monkeypatch.setattr(web_research, "fetch_context_material", lambda e: (None, None))

    result = gemini_pipe.run_research_context(["GoodTool", "BrokenTool", "AnotherGoodTool"])
    assert calls == ["GoodTool", "BrokenTool", "AnotherGoodTool"]  # all three attempted
    by_topic = {item.topic: item for item in result}
    assert set(by_topic) == {"GoodTool", "BrokenTool", "AnotherGoodTool"}
    assert by_topic["BrokenTool"].source == "web-fetch"
    assert by_topic["BrokenTool"].context == gemini_pipe.NOT_FOUND_VIA_SEARCH
    assert by_topic["GoodTool"].source == "search-grounding"


def test_run_research_context_caps_at_max_research_entities(monkeypatch):
    calls = []

    def _fake_call(entity):
        calls.append(entity)
        return _FakeGeminiResponse("x", chunks=[_FakeChunk()])

    monkeypatch.setattr(gemini_pipe, "_call_gemini_research", _fake_call)

    entities = [f"Tool{i}" for i in range(10)]
    gemini_pipe.run_research_context(entities)
    assert len(calls) == gemini_pipe.MAX_RESEARCH_ENTITIES


def test_extraction_success_path_populates_research_context(monkeypatch):
    """Wiring: run_caption_only_extraction's success path calls
    run_research_context with the extraction's named_entities and stores the
    result back onto extraction.research_context."""
    extraction_out = Extraction(
        main_point="x", value_score=3, named_entities=["Exply"],
    )
    monkeypatch.setattr(gemini_pipe, "_call_gemini_text_only", lambda prompt: extraction_out.model_dump_json())

    from app.models import ResearchContextItem
    fake_context = [ResearchContextItem(topic="Exply", context="Exply is an AI outreach tool.")]
    monkeypatch.setattr(gemini_pipe, "run_research_context", lambda entities: fake_context)

    result = gemini_pipe.run_caption_only_extraction(SUBSTANTIAL_CAPTION, None, None, [])
    assert result.research_context == fake_context


def test_degraded_extraction_never_calls_research_context(monkeypatch):
    """A too-thin caption degrades WITHOUT ever reaching Gemini call 1, so
    call 2 (research) must never fire either -- there's no real
    named_entities to research, and no reason to spend the extra Gemini calls."""
    def _must_not_be_called(entities):
        raise AssertionError("must not run research on a degraded extraction")

    monkeypatch.setattr(gemini_pipe, "run_research_context", _must_not_be_called)

    result = gemini_pipe.run_caption_only_extraction("just a few words here", None, None, [])
    assert result.content_type == "unknown"  # confirms the degraded path
    assert result.research_context == []


# --- Gemini-specific call spacing -- distinct from fetcher.MIN_FETCH_SPACING ---

def test_gemini_call_spacing_is_separate_state_from_fetch_spacing(monkeypatch):
    """Point 3: MIN_FETCH_SPACING_SECONDS/store.get_last_fetch_at() governs only
    yt-dlp fetch pacing -- this must use its own independent app_state key, not
    piggyback on the fetch-log table at all."""
    from app import store

    monkeypatch.setattr(gemini_pipe, "MIN_GEMINI_CALL_SPACING_SECONDS", 5.0)
    fetch_calls = []
    monkeypatch.setattr(store, "get_last_fetch_at", lambda: fetch_calls.append("called") or None)
    monkeypatch.setattr(store, "record_fetch", lambda: fetch_calls.append("called"))

    gemini_pipe._enforce_gemini_call_spacing()

    assert fetch_calls == []  # never touched the fetch-spacing machinery
    assert store.get_state(gemini_pipe._LAST_GEMINI_CALL_STATE_KEY) is not None


def test_gemini_call_spacing_sleeps_when_called_again_too_soon(monkeypatch):
    from app import store

    monkeypatch.setattr(gemini_pipe, "MIN_GEMINI_CALL_SPACING_SECONDS", 5.0)
    sleeps = []
    monkeypatch.setattr(gemini_pipe.time, "sleep", lambda s: sleeps.append(s))

    fake_now = [1000.0]
    monkeypatch.setattr(gemini_pipe.time, "time", lambda: fake_now[0])

    gemini_pipe._enforce_gemini_call_spacing()  # first call: nothing recorded yet, no sleep
    assert sleeps == []

    fake_now[0] += 1.0  # only 1s elapsed, well under the 5s spacing
    gemini_pipe._enforce_gemini_call_spacing()
    assert sleeps == [4.0]


def test_gemini_call_spacing_no_sleep_once_enough_time_has_passed(monkeypatch):
    from app import store

    monkeypatch.setattr(gemini_pipe, "MIN_GEMINI_CALL_SPACING_SECONDS", 5.0)
    sleeps = []
    monkeypatch.setattr(gemini_pipe.time, "sleep", lambda s: sleeps.append(s))

    fake_now = [1000.0]
    monkeypatch.setattr(gemini_pipe.time, "time", lambda: fake_now[0])

    gemini_pipe._enforce_gemini_call_spacing()
    fake_now[0] += 10.0  # well past the 5s spacing
    gemini_pipe._enforce_gemini_call_spacing()
    assert sleeps == []


def test_call_gemini_text_only_enforces_gemini_spacing(monkeypatch):
    """The spacing guard actually fires around the real Gemini call sites, not
    just as a standalone function nobody calls."""
    calls = []
    monkeypatch.setattr(gemini_pipe, "_enforce_gemini_call_spacing", lambda: calls.append("spaced"))

    class _FakeModels:
        def generate_content(self, **kwargs):
            return _FakeGeminiResponse(Extraction(main_point="x", value_score=2).model_dump_json(), chunks=[])

    class _FakeClient:
        def __init__(self, api_key):
            self.models = _FakeModels()

    import google.genai as genai_module
    monkeypatch.setattr(genai_module, "Client", _FakeClient)

    gemini_pipe._call_gemini_text_only("some prompt")
    assert calls == ["spaced"]


# --- silent-degradation incident: every degrade point must log the real error -
#
# Real incident: reel captures with a successfully-downloaded video were landing
# as degraded/caption-only saves (no Topics, flat value_score=3, caption-as-
# title) with ZERO error logged anywhere explaining why — subprocess.
# CalledProcessError and Gemini call/parse exceptions were all being swallowed
# silently. Confirmed via git diff that _extract_audio and run_extraction's
# try/except skeleton were byte-for-byte unchanged by the photo/carousel,
# priority, and comment-gate commits from the same night — so this silent
# swallowing pre-dates those changes, it just went unnoticed until now. Every
# branch below must now log the actual exception before degrading.

def test_ffmpeg_failure_logs_the_actual_error(monkeypatch, caplog):
    """The exact reported regression: a video that downloaded successfully
    (video_path is set) but whose ffmpeg audio-extraction step fails must log
    ffmpeg's real stderr — not degrade in total silence."""
    reel = ReelData(
        shortcode="FFMPEGLOG1", permalink="https://www.instagram.com/reel/FFMPEGLOG1/",
        video_path="/tmp/FFMPEGLOG1.mp4", caption="a caption",
    )

    def _ffmpeg_boom(video_path):
        raise subprocess.CalledProcessError(
            1, "ffmpeg", stderr=b"Unknown encoder 'aac' -- codec not found in this build"
        )

    monkeypatch.setattr(gemini_pipe, "_extract_audio", _ffmpeg_boom)

    with caplog.at_level(logging.WARNING, logger="reelbrain.gemini"):
        result = run_extraction(reel, note=None, taxonomy=[])

    assert result.content_type == "unknown"  # confirms the degraded path was taken
    assert "FFMPEGLOG1" in caplog.text
    assert "Unknown encoder 'aac' -- codec not found in this build" in caplog.text


def test_no_video_path_logs_a_warning(monkeypatch, caplog):
    reel = ReelData(
        shortcode="NOVIDLOG1", permalink="https://www.instagram.com/reel/NOVIDLOG1/",
        video_path=None, caption="a caption",
    )
    with caplog.at_level(logging.WARNING, logger="reelbrain.gemini"):
        run_extraction(reel, note=None, taxonomy=[])
    assert "NOVIDLOG1" in caplog.text
    assert "no video_path" in caplog.text


def test_gemini_call_exception_logs_the_actual_error(monkeypatch, caplog):
    reel = ReelData(
        shortcode="GEMLOG1", permalink="https://www.instagram.com/reel/GEMLOG1/",
        video_path="/tmp/GEMLOG1.mp4", caption="a caption",
    )
    monkeypatch.setattr(gemini_pipe, "_extract_audio", lambda p: "/tmp/GEMLOG1.m4a")

    def _boom(audio_path, prompt):
        raise RuntimeError("503 Service Unavailable from Gemini")

    monkeypatch.setattr(gemini_pipe, "_call_gemini", _boom)

    with caplog.at_level(logging.WARNING, logger="reelbrain.gemini"):
        result = run_extraction(reel, note=None, taxonomy=[])

    assert result.content_type == "unknown"
    assert "GEMLOG1" in caplog.text
    assert "503 Service Unavailable from Gemini" in caplog.text


def test_schema_validation_failure_logs_the_validation_error(monkeypatch, caplog):
    reel = ReelData(
        shortcode="VALIDLOG1", permalink="https://www.instagram.com/reel/VALIDLOG1/",
        video_path="/tmp/VALIDLOG1.mp4", caption="a caption",
    )
    monkeypatch.setattr(gemini_pipe, "_extract_audio", lambda p: "/tmp/VALIDLOG1.m4a")
    monkeypatch.setattr(gemini_pipe, "_call_gemini", lambda a, p: "not valid json at all")

    with caplog.at_level(logging.WARNING, logger="reelbrain.gemini"):
        result = run_extraction(reel, note=None, taxonomy=[])

    assert result.content_type == "unknown"
    assert "VALIDLOG1" in caplog.text
    # two attempts (one retry) -> two validation-failure log lines
    assert caplog.text.count("failed schema validation") == 2


def test_caption_only_gemini_failure_also_logs(monkeypatch, caplog):
    def _boom(prompt):
        raise RuntimeError("gemini 500")

    monkeypatch.setattr(gemini_pipe, "_call_gemini_text_only", _boom)

    with caplog.at_level(logging.WARNING, logger="reelbrain.gemini"):
        result = gemini_pipe.run_caption_only_extraction(SUBSTANTIAL_CAPTION, None, None, [])

    assert result.content_type == "unknown"
    assert "gemini 500" in caplog.text


# --- timing-bug fix: truncated/still-being-written file caught before ffmpeg ----
#
# Real incident: ffmpeg's CalledProcessError fired while a download's progress
# was still at 63.9% in the Render log, continuing to 100% afterward. Root
# cause turned out to be a missing lock letting two fetch_reel calls run
# concurrently (fixed in app/fetcher.py) rather than _run_ytdlp itself
# returning early -- but this defensive check stays regardless, so ANY future
# truncated-file cause (disk issues, a container restart mid-download, etc.)
# is caught explicitly instead of surfacing as an opaque ffmpeg error.

def test_check_video_file_size_flags_a_truncated_file(tmp_path):
    video = tmp_path / "truncated.mp4"
    video.write_bytes(b"x" * 1000)  # far smaller than "expected"

    issue = gemini_pipe._check_video_file_size(str(video), expected_size=5_000_000)

    assert issue is not None
    assert "truncated" in issue
    assert "1000" in issue and "5000000" in issue


def test_check_video_file_size_passes_when_size_matches(tmp_path):
    video = tmp_path / "complete.mp4"
    video.write_bytes(b"x" * 5000)

    assert gemini_pipe._check_video_file_size(str(video), expected_size=5000) is None
    assert gemini_pipe._check_video_file_size(str(video), expected_size=4000) is None  # bigger than expected is fine


def test_check_video_file_size_skipped_without_an_expected_size(tmp_path):
    video = tmp_path / "unknown_size.mp4"
    video.write_bytes(b"x" * 10)
    assert gemini_pipe._check_video_file_size(str(video), expected_size=None) is None
    assert gemini_pipe._check_video_file_size(str(video), expected_size=0) is None


def test_check_video_file_size_flags_a_missing_file():
    issue = gemini_pipe._check_video_file_size("/tmp/does-not-exist-at-all.mp4", expected_size=1000)
    assert issue is not None
    assert "missing or unreadable" in issue


def test_run_extraction_catches_truncated_file_before_calling_ffmpeg(monkeypatch, tmp_path, caplog):
    """The exact requirement: a video file that exists but is smaller than
    yt-dlp reported must be caught explicitly -- and ffmpeg must never even
    be invoked for it."""
    video = tmp_path / "TRUNC1.mp4"
    video.write_bytes(b"x" * 100)  # tiny -- nowhere near expected_video_size

    reel = ReelData(
        shortcode="TRUNC1", permalink="https://www.instagram.com/reel/TRUNC1/",
        video_path=str(video), caption="a caption", expected_video_size=10_000_000,
    )

    def _must_not_be_called(video_path):
        raise AssertionError("ffmpeg must not be invoked on a known-truncated file")

    monkeypatch.setattr(gemini_pipe, "_extract_audio", _must_not_be_called)

    with caplog.at_level(logging.WARNING, logger="reelbrain.gemini"):
        result = run_extraction(reel, note=None, taxonomy=[])

    assert result.content_type == "unknown"  # degraded path
    assert "TRUNC1" in caplog.text
    assert "truncated" in caplog.text


def test_run_extraction_proceeds_normally_when_file_size_matches(monkeypatch, tmp_path):
    """Regression guard: a genuinely complete file (or one with no reported
    expected size) must still reach ffmpeg as before -- this check must not
    block normal, healthy extraction."""
    video = tmp_path / "OK1.mp4"
    video.write_bytes(b"x" * 5000)

    reel = ReelData(
        shortcode="OK1", permalink="https://www.instagram.com/reel/OK1/",
        video_path=str(video), caption="a caption", expected_video_size=5000,
    )
    monkeypatch.setattr(gemini_pipe, "_extract_audio", lambda p: "/tmp/OK1.m4a")
    monkeypatch.setattr(
        gemini_pipe, "_call_gemini",
        lambda a, p: Extraction(main_point="fine", value_score=4).model_dump_json(),
    )

    monkeypatch.setattr(gemini_pipe, "_has_audio_stream", lambda p: True)
    result = run_extraction(reel, note=None, taxonomy=[])
    assert result.main_point == "fine"


# --- FIX 1: no-video path routes through the caption-only extraction ------------
#
# The main complaint: OG-recovered reels (video_path=None, caption present) were
# storing the raw caption as the title with no Topics — the no-video branch went
# straight to _degraded() instead of run_caption_only_extraction, which already
# existed and works. Now it routes exactly like the no-audio branch.

def test_no_video_with_caption_routes_to_caption_only_extraction(monkeypatch):
    reel = ReelData(
        shortcode="NOVIDCAP1", permalink="https://www.instagram.com/reel/NOVIDCAP1/",
        video_path=None, caption=SUBSTANTIAL_CAPTION,
    )
    monkeypatch.setattr(
        gemini_pipe, "_call_gemini_text_only",
        lambda prompt: Extraction(
            main_point="Synthesized title from caption", topic_tags=["sleep", "habits"],
            value_score=4,
        ).model_dump_json(),
    )
    monkeypatch.setattr(gemini_pipe, "_call_gemini", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("audio path must not run without a video")
    ))

    result = run_extraction(reel, note=None, taxonomy=[])

    assert result.main_point == "Synthesized title from caption"  # NOT the raw caption
    assert result.topic_tags == ["sleep", "habits"]
    assert result.value_score == 4
    assert result.priority == "High"


def test_no_video_and_no_caption_still_falls_back_to_placeholder(monkeypatch):
    reel = ReelData(
        shortcode="NOVIDNOCAP1", permalink="https://www.instagram.com/reel/NOVIDNOCAP1/",
        video_path=None, caption=None,
    )

    def _must_not_call(prompt):
        raise AssertionError("no caption -> Gemini must not be called at all")

    monkeypatch.setattr(gemini_pipe, "_call_gemini_text_only", _must_not_call)

    result = run_extraction(reel, note=None, taxonomy=[])
    assert result.main_point == "No caption or transcript available."
    assert result.content_type == "unknown"


def test_no_video_thin_caption_still_falls_back_to_placeholder(monkeypatch):
    reel = ReelData(
        shortcode="NOVIDTHIN1", permalink="https://www.instagram.com/reel/NOVIDTHIN1/",
        video_path=None, caption="too few words here",
    )
    monkeypatch.setattr(gemini_pipe, "_call_gemini_text_only", lambda p: (_ for _ in ()).throw(
        AssertionError("thin caption -> Gemini must not be called")
    ))

    result = run_extraction(reel, note=None, taxonomy=[])
    assert result.main_point == "too few words here"  # honest placeholder, unchanged
    assert result.content_type == "unknown"


# --- no-audio-stream handling: video-only IG downloads (the actual root cause) --
#
# Real incident: ffmpeg exit 1 on a fully-downloaded, existing file. Root cause
# was yt-dlp selecting a VIDEO-ONLY format for some IG posts (fixed at the
# source in fetcher.YTDLP_FORMAT). This is the downstream safety net: even if a
# genuinely audio-less video ever arrives, ffprobe catches it and we route to
# caption-only with a specific note instead of crashing ffmpeg's -vn extraction.


class _ProbeResult:
    def __init__(self, stdout: str):
        self.stdout = stdout


def test_has_audio_stream_true_when_ffprobe_lists_a_stream(monkeypatch):
    monkeypatch.setattr(
        gemini_pipe.subprocess, "run", lambda *a, **kw: _ProbeResult("0\n")
    )
    assert gemini_pipe._has_audio_stream("/tmp/x.mp4") is True


def test_has_audio_stream_false_when_ffprobe_lists_nothing(monkeypatch):
    monkeypatch.setattr(
        gemini_pipe.subprocess, "run", lambda *a, **kw: _ProbeResult("   \n")
    )
    assert gemini_pipe._has_audio_stream("/tmp/x.mp4") is False


def test_has_audio_stream_none_when_ffprobe_cannot_run(monkeypatch):
    def _boom(*a, **kw):
        raise FileNotFoundError("ffprobe not installed")
    monkeypatch.setattr(gemini_pipe.subprocess, "run", _boom)
    assert gemini_pipe._has_audio_stream("/tmp/x.mp4") is None


def test_has_audio_stream_none_when_ffprobe_errors_on_file(monkeypatch):
    def _boom(*a, **kw):
        raise subprocess.CalledProcessError(1, "ffprobe")
    monkeypatch.setattr(gemini_pipe.subprocess, "run", _boom)
    assert gemini_pipe._has_audio_stream("/tmp/x.mp4") is None


def test_no_audio_video_routes_to_caption_only_with_specific_note(monkeypatch, tmp_path, caplog):
    """The exact requirement: a video file that EXISTS and is complete but has
    no audio stream must be detected via ffprobe and routed to caption-only,
    with a distinct 'no audio track' note — ffmpeg must never be invoked."""
    video = tmp_path / "NOAUDIO1.mp4"
    video.write_bytes(b"x" * 5000)

    reel = ReelData(
        shortcode="NOAUDIO1", permalink="https://www.instagram.com/reel/NOAUDIO1/",
        video_path=str(video),
        caption="A substantial caption with plenty of words to summarize from here today.",
    )

    monkeypatch.setattr(gemini_pipe, "_has_audio_stream", lambda p: False)
    monkeypatch.setattr(gemini_pipe, "_extract_audio", lambda p: (_ for _ in ()).throw(
        AssertionError("ffmpeg must not run when there's no audio stream")
    ))
    # caption-only path's Gemini call -> a real structured extraction
    monkeypatch.setattr(
        gemini_pipe, "_call_gemini_text_only",
        lambda prompt: Extraction(main_point="caption-derived point", topic_tags=["sleep"], value_score=4).model_dump_json(),
    )

    with caplog.at_level(logging.WARNING, logger="reelbrain.gemini"):
        result = run_extraction(reel, note=None, taxonomy=[])

    # real caption-only extraction, not the generic degrade
    assert result.main_point == "caption-derived point"
    assert result.topic_tags == ["sleep"]
    # the distinct note is set on the reel so main.py surfaces it on the Notion row
    assert reel.fetch_note == "no audio track in source video — summarized from caption only, no transcript"
    assert "no audio stream in NOAUDIO1" in caplog.text


def test_ffmpeg_failure_log_includes_ffprobe_stream_layout(monkeypatch, tmp_path, caplog):
    """Diagnostic #3: when ffmpeg DOES fail (for some reason other than no
    audio), the log must include ffprobe's actual stream layout so the failure
    is immediately diagnosable."""
    video = tmp_path / "FFPROBELOG.mp4"
    video.write_bytes(b"x" * 5000)

    reel = ReelData(
        shortcode="FFPROBELOG", permalink="https://www.instagram.com/reel/FFPROBELOG/",
        video_path=str(video), caption="a caption",
    )
    monkeypatch.setattr(gemini_pipe, "_has_audio_stream", lambda p: True)  # probe says audio present
    monkeypatch.setattr(gemini_pipe, "_ffprobe_streams", lambda p: "0,video,h264|1,audio,aac")

    def _ffmpeg_boom(video_path):
        raise subprocess.CalledProcessError(1, "ffmpeg", stderr=b"some ffmpeg error")

    monkeypatch.setattr(gemini_pipe, "_extract_audio", _ffmpeg_boom)

    with caplog.at_level(logging.WARNING, logger="reelbrain.gemini"):
        result = run_extraction(reel, note=None, taxonomy=[])

    assert result.content_type == "unknown"  # degraded
    assert "ffprobe streams: 0,video,h264|1,audio,aac" in caplog.text


def test_ffprobe_streams_never_raises_when_ffprobe_missing(monkeypatch):
    def _boom(*a, **kw):
        raise FileNotFoundError("no ffprobe")
    monkeypatch.setattr(gemini_pipe.subprocess, "run", _boom)
    out = gemini_pipe._ffprobe_streams("/tmp/x.mp4")
    assert "ffprobe unavailable" in out
