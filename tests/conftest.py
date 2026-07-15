import pytest

from app import gemini_pipe, main, notion_writer, store
from app.models import CommentGate, Extraction, ReelData


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Every test gets its own throwaway SQLite file."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(store, "DB_PATH", db_path)
    store.init_db()
    yield db_path


@pytest.fixture(autouse=True)
def _never_call_real_gemini_embeddings(monkeypatch):
    """Safety net: no test should ever reach the real Gemini embedding API, even
    if it forgets to mock gemini_pipe.embed_text itself (a test that mocks
    run_extraction but not embed_text would otherwise fall through to a live
    network call from inside run_pipeline's near-dup/related-saves step).
    Tests that care about specific similarity results override this themselves."""
    monkeypatch.setattr(gemini_pipe, "embed_text", lambda text: [0.0] * 768)


@pytest.fixture(autouse=True)
def _never_construct_real_genai_client(monkeypatch):
    """Defense-in-depth: block real Gemini client construction in every test.
    Anything that slips past a forgotten mock raises here instead of making a
    live network call. Tests wanting a 'successful' Gemini result patch the
    relevant app-level function (embed_text, run_extraction, try_ai_summary)."""
    import google.genai as genai_module

    class _BlockedClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("live Gemini client construction blocked in tests")

    monkeypatch.setattr(genai_module, "Client", _BlockedClient)


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    """Every TestClient request comes from the same 'testclient' IP — clear the
    per-IP rate-limit bucket between tests so unrelated tests can't trip it."""
    main._rate_buckets.clear()
    yield
    main._rate_buckets.clear()


@pytest.fixture(autouse=True)
def _reset_notion_data_source_cache():
    """notion_writer caches resolved data_source_ids at module level — clear it
    between tests so a fresh FakeClient always gets its own resolution call."""
    notion_writer._data_source_id_cache.clear()
    yield
    notion_writer._data_source_id_cache.clear()


@pytest.fixture
def tutorial_reel() -> ReelData:
    return ReelData(
        shortcode="TUT001abc",
        permalink="https://www.instagram.com/reel/TUT001abc/",
        video_path="/tmp/TUT001abc.mp4",
        caption="3 steps to fix your sleep schedule tonight",
        creator_username="sleepcoachjane",
        creator_fullname="Jane Doe",
        taken_at="2026-06-01T12:00:00+00:00",
        like_count=1200,
    )


@pytest.fixture
def tutorial_extraction() -> Extraction:
    return Extraction(
        transcript="Step one, get sunlight in the morning. Step two, no caffeine after noon. Step three, keep a consistent wake time.",
        has_speech=True,
        main_point="Three concrete steps to fix a broken sleep schedule.",
        supporting_points=["Morning sunlight resets your circadian rhythm", "Caffeine after noon disrupts deep sleep"],
        steps_or_framework=["Get sunlight in the morning", "No caffeine after noon", "Keep a consistent wake time"],
        quotable_lines=["Step one, get sunlight in the morning."],
        topic_tags=["sleep", "health", "habits"],
        content_type="tutorial",
        comment_gate=CommentGate(detected=False),
        value_score=4,
        language="en",
    )


@pytest.fixture
def gated_reel() -> ReelData:
    return ReelData(
        shortcode="GATE002xy",
        permalink="https://www.instagram.com/reel/GATE002xy/",
        video_path="/tmp/GATE002xy.mp4",
        caption="Want my full AI workflow doc? Comment 'SEND' and I'll DM it to you",
        creator_username="aiworkflows",
        creator_fullname="AI Workflows",
        taken_at="2026-06-10T09:00:00+00:00",
        like_count=5400,
    )


@pytest.fixture
def gated_extraction() -> Extraction:
    return Extraction(
        transcript="If you want my full AI workflow doc, comment send and I'll DM it to you.",
        has_speech=True,
        main_point="Creator offers a free AI workflow doc via DM in exchange for a comment.",
        supporting_points=[],
        steps_or_framework=[],
        quotable_lines=[],
        topic_tags=["ai-workflows", "automation"],
        content_type="resource_drop",
        comment_gate=CommentGate(detected=True, keyword="SEND", promised_resource="AI workflow doc"),
        value_score=3,
        language="en",
    )


@pytest.fixture
def music_only_reel() -> ReelData:
    return ReelData(
        shortcode="MUS003qq",
        permalink="https://www.instagram.com/reel/MUS003qq/",
        video_path="/tmp/MUS003qq.mp4",
        caption="vibes only 🎶",
        creator_username="aestheticclips",
        creator_fullname="Aesthetic Clips",
        taken_at="2026-06-12T18:00:00+00:00",
        like_count=800,
    )


@pytest.fixture
def music_only_extraction() -> Extraction:
    return Extraction(
        transcript="",
        has_speech=False,
        main_point="vibes only 🎶",
        supporting_points=[],
        steps_or_framework=[],
        quotable_lines=[],
        topic_tags=["music", "aesthetic"],
        content_type="entertainment",
        comment_gate=CommentGate(detected=False),
        value_score=1,
        language="en",
    )
