"""Integration test: mocked fetcher + Gemini, but a fake Notion *client* (not a
mocked notion_writer) so we actually exercise the property/block-building logic
and can assert on the resulting Notion payload shape, per BUILD_SPEC's Testing section.

The fake mirrors Notion's 2025-09-03+ "data source" API shape that notion_writer.py
actually uses (databases.retrieve -> data_sources, data_sources.query, pages with a
data_source_id parent) — see the NOTE at the top of app/notion_writer.py.
"""
from app import fetcher, notion_writer, store
from app.main import run_pipeline
from app.models import CommentGate, Extraction


class FakePages:
    def __init__(self):
        self.created = []
        self.updated = []

    def create(self, **kwargs):
        page_id = f"page-{len(self.created) + 1}"
        self.created.append(kwargs)
        return {"id": page_id, "url": f"https://notion.so/{page_id}"}

    def update(self, **kwargs):
        self.updated.append(kwargs)
        return {"id": kwargs["page_id"], "url": f"https://notion.so/{kwargs['page_id']}"}


class FakeDatabases:
    def retrieve(self, database_id):
        return {"id": database_id, "data_sources": [{"id": f"ds-{database_id or 'default'}"}]}


class FakeDataSources:
    def query(self, **kwargs):
        return {"results": []}  # no existing creator page found -> notion_writer creates one


class FakeBlockChildren:
    def list(self, block_id):
        return {"results": []}

    def append(self, block_id, children):
        return {"results": children}


class FakeBlocks:
    def __init__(self):
        self.children = FakeBlockChildren()
        self.deleted = []

    def delete(self, block_id):
        self.deleted.append(block_id)
        return {}


class FakeClient:
    def __init__(self):
        self.pages = FakePages()
        self.databases = FakeDatabases()
        self.data_sources = FakeDataSources()
        self.blocks = FakeBlocks()


def _install_fake_notion(monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(notion_writer, "_client", lambda: fake_client)
    return fake_client


def _props_of(created_call: dict) -> dict:
    return created_call["properties"]


def save_page_calls(fake) -> list[dict]:
    """The pages.create calls that landed in the Saves DB specifically (as
    opposed to a Creator page, which lands in a different data source)."""
    saves_ds_id = notion_writer._resolve_data_source_id(fake, notion_writer.NOTION_DB_ID)
    return [
        c for c in fake.pages.created
        if c["parent"].get("data_source_id") == saves_ds_id
    ]


def test_pipeline_tutorial_reel_creates_inbox_page(monkeypatch, tutorial_reel, tutorial_extraction):
    fake = _install_fake_notion(monkeypatch)
    monkeypatch.setattr("app.main.fetcher.fetch_reel", lambda shortcode, permalink: tutorial_reel)
    monkeypatch.setattr("app.main.gemini_pipe.run_extraction", lambda reel, note, taxonomy: tutorial_extraction)

    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink)
    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note=None)

    save_calls = save_page_calls(fake)
    assert len(save_calls) == 1
    props = _props_of(save_calls[0])
    assert props["Status"]["select"]["name"] == "📥 Inbox"
    assert props["Content type"]["select"]["name"] == "tutorial"
    assert props["Shortcode"]["rich_text"][0]["text"]["content"] == "TUT001abc"
    assert {"name": "sleep"} in props["Topics"]["multi_select"]
    assert props["Value score"]["select"]["name"] == "4"

    row = store.get_by_shortcode(tutorial_reel.shortcode)
    assert row["status"] == "done"
    assert row["notion_page_id"]


def test_pipeline_gated_reel_sets_awaiting_dm(monkeypatch, gated_reel, gated_extraction):
    fake = _install_fake_notion(monkeypatch)
    monkeypatch.setattr("app.main.fetcher.fetch_reel", lambda shortcode, permalink: gated_reel)
    monkeypatch.setattr("app.main.gemini_pipe.run_extraction", lambda reel, note, taxonomy: gated_extraction)

    store.insert_processing(gated_reel.shortcode, gated_reel.permalink)
    run_pipeline(gated_reel.shortcode, gated_reel.permalink, note=None)

    save_calls = save_page_calls(fake)
    props = _props_of(save_calls[0])
    assert props["Status"]["select"]["name"] == "⏳ Awaiting DM"
    assert props["Comment gate"]["checkbox"] is True
    assert props["Gate keyword"]["rich_text"][0]["text"]["content"] == "SEND"

    row = store.get_by_shortcode(gated_reel.shortcode)
    assert row["status"] == "awaiting_dm"
    assert row["gate_keyword"] == "SEND"


def test_pipeline_writes_priority_property_to_notion(monkeypatch, tutorial_reel):
    """Priority system: the computed Priority lands on the Notion page as a
    Select property, written alongside the existing properties on every
    capture/retry — plain text ("High"), no emoji."""
    fake = _install_fake_notion(monkeypatch)
    extraction = Extraction(
        main_point="A high-priority item", topic_tags=["claude-code"], value_score=2,
        comment_gate=CommentGate(detected=False), priority="High",
    )
    monkeypatch.setattr("app.main.fetcher.fetch_reel", lambda shortcode, permalink: tutorial_reel)
    monkeypatch.setattr("app.main.gemini_pipe.run_extraction", lambda reel, note, taxonomy: extraction)

    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink)
    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note=None)

    props = _props_of(save_page_calls(fake)[0])
    assert props["Priority"]["select"]["name"] == "High"


def test_pipeline_writes_research_context_as_body_toggle_not_a_property(monkeypatch, tutorial_reel):
    """research_context lands as a "Research Context" toggle in the page body
    (see PROGRESS.md: Notion's 2000-char rich_text cap means several topics
    concatenated into one property value could overflow it), one paragraph
    block per entry -- never a Notion property."""
    from app.models import ResearchContextItem

    fake = _install_fake_notion(monkeypatch)
    extraction = Extraction(
        main_point="x", topic_tags=["sleep"], value_score=3,
        comment_gate=CommentGate(detected=False),
        named_entities=["Cleanlist.ai"],
        research_context=[ResearchContextItem(topic="Cleanlist.ai", context="A LinkedIn scraping tool.")],
    )
    monkeypatch.setattr("app.main.fetcher.fetch_reel", lambda shortcode, permalink: tutorial_reel)
    monkeypatch.setattr("app.main.gemini_pipe.run_extraction", lambda reel, note, taxonomy: extraction)

    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink)
    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note=None)

    call = save_page_calls(fake)[0]
    assert "Research Context" not in call["properties"]  # never a property
    toggles = [b for b in call["children"] if b["type"] == "toggle"]
    research_toggle = next(t for t in toggles if t["toggle"]["rich_text"][0]["text"]["content"] == "Research Context")
    child_texts = [c["paragraph"]["rich_text"][0]["text"]["content"] for c in research_toggle["toggle"]["children"]]
    assert child_texts == ["Cleanlist.ai [search-grounding]: A LinkedIn scraping tool."]


def test_pipeline_no_research_context_toggle_when_empty(monkeypatch, tutorial_reel, tutorial_extraction):
    """tutorial_extraction has no research_context set (defaults to []) --
    confirms the toggle is only emitted when there's actually something to show."""
    fake = _install_fake_notion(monkeypatch)
    monkeypatch.setattr("app.main.fetcher.fetch_reel", lambda shortcode, permalink: tutorial_reel)
    monkeypatch.setattr("app.main.gemini_pipe.run_extraction", lambda reel, note, taxonomy: tutorial_extraction)

    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink)
    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note=None)

    call = save_page_calls(fake)[0]
    toggle_titles = [b["toggle"]["rich_text"][0]["text"]["content"] for b in call["children"] if b["type"] == "toggle"]
    assert "Research Context" not in toggle_titles


def test_pipeline_music_only_reel_is_honest_about_no_speech(monkeypatch, music_only_reel, music_only_extraction):
    fake = _install_fake_notion(monkeypatch)
    monkeypatch.setattr("app.main.fetcher.fetch_reel", lambda shortcode, permalink: music_only_reel)
    monkeypatch.setattr("app.main.gemini_pipe.run_extraction", lambda reel, note, taxonomy: music_only_extraction)

    store.insert_processing(music_only_reel.shortcode, music_only_reel.permalink)
    run_pipeline(music_only_reel.shortcode, music_only_reel.permalink, note=None)

    save_calls = save_page_calls(fake)
    props = _props_of(save_calls[0])
    assert props["Value score"]["select"]["name"] == "1"
    # value_score 1 <= LOW_SIGNAL_VALUE_SCORE -> this is Low signal, not Inbox
    assert props["Status"]["select"]["name"] == "🗑 Low signal"

    transcript_toggle = next(
        b for b in save_calls[0]["children"]
        if b["type"] == "toggle" and b["toggle"]["rich_text"][0]["text"]["content"] == "Transcript"
    )
    body_text = transcript_toggle["toggle"]["children"][0]["paragraph"]["rich_text"][0]["text"]["content"]
    assert body_text == "(no speech detected)"

    row = store.get_by_shortcode(music_only_reel.shortcode)
    assert row["status"] == "low_signal"


def test_pipeline_fetch_degraded_still_writes_failed_page(monkeypatch, tutorial_reel):
    from app.fetcher import FetchDegraded

    fake = _install_fake_notion(monkeypatch)

    def fake_fetch(shortcode, permalink):
        raise FetchDegraded("refresh burner cookies", partial=tutorial_reel.model_copy(update={"video_path": None}))

    monkeypatch.setattr("app.main.fetcher.fetch_reel", fake_fetch)

    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink)
    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note=None)

    save_calls = save_page_calls(fake)
    assert len(save_calls) == 1
    assert _props_of(save_calls[0])["Status"]["select"]["name"] == "⚠️ Failed — retry"

    row = store.get_by_shortcode(tutorial_reel.shortcode)
    assert row["status"] == "failed"
    assert row["notion_page_id"]  # constraint #3: never silently drop a capture


def test_photo_carousel_post_still_captured_not_dropped(monkeypatch, tutorial_reel):
    """The actual reported bug: yt-dlp says 'no video formats found' (a
    photo/carousel post) on every attempt, and the OG-tag scrape ALSO comes back
    empty (Instagram login-walling the anonymous scrape from Render's IP). This
    must never silently drop the capture — it must still land as a real Notion
    row carrying the permalink, distinctly marked so the user knows to open it
    manually rather than expecting an auto-summary.

    Runs the REAL app.fetcher.fetch_reel (only its yt-dlp/OG internals mocked),
    not a stand-in, so this proves the actual integration, not just the unit.
    """
    fake = _install_fake_notion(monkeypatch)
    monkeypatch.setattr(fetcher, "resolve_cookies_file", lambda: "cookies.txt")
    monkeypatch.setattr(fetcher, "BACKOFF_SECONDS", [0])
    monkeypatch.setattr(fetcher, "fetch_og_metadata", lambda p: None)  # login-walled

    def _no_video(url, cookiefile):
        raise RuntimeError("No video formats found")

    monkeypatch.setattr(fetcher, "_run_ytdlp", _no_video)

    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink)
    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note="my original note")

    save_calls = save_page_calls(fake)
    assert len(save_calls) == 1  # a real Notion row was created — nothing dropped
    props = _props_of(save_calls[0])
    assert props["Status"]["select"]["name"] == "📷 Photo — manual"
    assert props["Reel URL"]["url"] == tutorial_reel.permalink
    note_text = props["My note"]["rich_text"][0]["text"]["content"]
    assert "my original note" in note_text  # user's own note preserved
    assert "photo/carousel post — no auto-transcript, open the reel URL to view" in note_text

    row = store.get_by_shortcode(tutorial_reel.shortcode)
    assert row["status"] == "photo_manual"
    assert row["notion_page_id"]  # constraint #3: never silently drop a capture
    assert row["permalink"] == tutorial_reel.permalink


def test_photo_carousel_with_recovered_caption_gets_real_summary_not_placeholder(monkeypatch):
    """The fix: when yt-dlp fails with 'no video formats found' (photo/carousel)
    AND the OG-tag scrape DOES recover a caption, the row must get a real Main
    Point/Topics/Priority/Value score from Gemini -- not the bare-caption
    placeholder -- plus a clear note distinguishing it from a video-based save.

    Runs the REAL app.fetcher.fetch_reel (only yt-dlp/OG internals mocked) so
    this proves the actual fetcher->main.py wiring, not just the unit.
    """
    fake = _install_fake_notion(monkeypatch)
    monkeypatch.setattr(fetcher, "resolve_cookies_file", lambda: "cookies.txt")
    monkeypatch.setattr(fetcher, "BACKOFF_SECONDS", [0])

    def _no_video(url, cookiefile):
        raise RuntimeError("No video formats found")

    monkeypatch.setattr(fetcher, "_run_ytdlp", _no_video)
    monkeypatch.setattr(
        fetcher, "fetch_og_metadata",
        lambda p: {
            "og:title": "Jane Doe (@sleepcoachjane) on Instagram",
            "og:description": "3 steps to fix your sleep schedule for good, save this!",
        },
    )

    caption_extraction = Extraction(
        main_point="Three steps to fix a broken sleep schedule.",
        topic_tags=["sleep", "habits"],
        content_type="tutorial",
        comment_gate=CommentGate(detected=False),
        value_score=4,
        priority="High",
    )
    monkeypatch.setattr(
        "app.main.gemini_pipe.run_caption_only_extraction",
        lambda caption, creator, note, taxonomy: caption_extraction,
    )
    monkeypatch.setattr(
        "app.main.gemini_pipe.run_extraction",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not call the video path")),
    )

    shortcode = "PHOTOCAP1"
    permalink = f"https://www.instagram.com/reel/{shortcode}/"
    store.insert_processing(shortcode, permalink)
    run_pipeline(shortcode, permalink, note=None)

    save_calls = save_page_calls(fake)
    assert len(save_calls) == 1
    props = _props_of(save_calls[0])
    # real extraction, not a placeholder
    assert props["Title"]["title"][0]["text"]["content"] == "Three steps to fix a broken sleep schedule."
    assert {"name": "sleep"} in props["Topics"]["multi_select"]
    assert props["Value score"]["select"]["name"] == "4"
    assert props["Priority"]["select"]["name"] == "High"
    # still distinctly marked as photo/carousel, not indistinguishable from a video save
    assert props["Status"]["select"]["name"] == "📷 Photo — manual"
    note_text = props["My note"]["rich_text"][0]["text"]["content"]
    assert "summarized from caption only, no video transcript available" in note_text

    row = store.get_by_shortcode(shortcode)
    assert row["status"] == "photo_manual"


def test_bug1_reported_shortcode_danilwobzdja_never_vanishes(monkeypatch):
    """BUG 1 verification: confirms the fix from test_photo_carousel_post_still_
    captured_not_dropped against the literal shortcode reported as vanishing
    (DaNiWoBzdja, 'No video formats found') rather than a generic fixture, tied
    to a fresh /capture-style insert with no pre-existing note."""
    fake = _install_fake_notion(monkeypatch)
    monkeypatch.setattr(fetcher, "resolve_cookies_file", lambda: "cookies.txt")
    monkeypatch.setattr(fetcher, "BACKOFF_SECONDS", [0, 0, 0])
    monkeypatch.setattr(fetcher, "fetch_og_metadata", lambda p: None)  # login-walled

    def _no_video(url, cookiefile):
        raise RuntimeError("ERROR: [Instagram] DaNiWoBzdja: No video formats found!")

    monkeypatch.setattr(fetcher, "_run_ytdlp", _no_video)

    shortcode = "DaNiWoBzdja"
    permalink = f"https://www.instagram.com/reel/{shortcode}/"
    store.insert_processing(shortcode, permalink)
    run_pipeline(shortcode, permalink, note=None)

    save_calls = save_page_calls(fake)
    assert len(save_calls) == 1  # never silently dropped
    assert _props_of(save_calls[0])["Status"]["select"]["name"] == "📷 Photo — manual"

    row = store.get_by_shortcode(shortcode)
    assert row["status"] == "photo_manual"
    assert row["notion_page_id"]


def test_failed_row_writes_reason_into_my_note(monkeypatch, tutorial_reel):
    """A Failed row should say WHY on the Notion page — no log access needed."""
    from app.fetcher import FetchDegraded

    fake = _install_fake_notion(monkeypatch)

    def fake_fetch(shortcode, permalink):
        raise FetchDegraded(
            "cookies file not found at ./cookies.txt or /etc/secrets/cookies.txt",
            partial=tutorial_reel.model_copy(update={"video_path": None}),
        )

    monkeypatch.setattr("app.main.fetcher.fetch_reel", fake_fetch)

    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink)
    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note=None)

    note = _props_of(save_page_calls(fake)[0])["My note"]["rich_text"][0]["text"]["content"]
    assert "cookies file not found" in note


def test_failure_reason_appends_to_user_note_without_overwriting(monkeypatch, tutorial_reel):
    from app.fetcher import FetchDegraded

    fake = _install_fake_notion(monkeypatch)

    def fake_fetch(shortcode, permalink):
        raise FetchDegraded("fetch failed: boom", partial=tutorial_reel.model_copy(update={"video_path": None}))

    monkeypatch.setattr("app.main.fetcher.fetch_reel", fake_fetch)

    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink, note="my original note")
    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note="my original note")

    note = _props_of(save_page_calls(fake)[0])["My note"]["rich_text"][0]["text"]["content"]
    assert note.startswith("my original note")  # user's note preserved, first
    assert "fetch failed: boom" in note


def test_successful_row_note_has_no_failure_stamp(monkeypatch, tutorial_reel, tutorial_extraction):
    fake = _install_fake_notion(monkeypatch)
    monkeypatch.setattr("app.main.fetcher.fetch_reel", lambda s, p: tutorial_reel)
    monkeypatch.setattr("app.main.gemini_pipe.run_extraction", lambda r, n, t: tutorial_extraction)

    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink, note="clean note")
    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note="clean note")

    note = _props_of(save_page_calls(fake)[0])["My note"]["rich_text"][0]["text"]["content"]
    assert note == "clean note"
    assert "⚠️" not in note


def test_note_with_failure_reason_truncates():
    from app.main import FAILURE_REASON_MAX_CHARS, _note_with_failure_reason

    out = _note_with_failure_reason(None, "x" * 5000)
    assert out.count("x") == FAILURE_REASON_MAX_CHARS  # reason truncated, prefix aside
    assert out.startswith("⚠️ ")


def test_note_with_failure_reason_passthrough_when_no_reason():
    from app.main import _note_with_failure_reason

    assert _note_with_failure_reason("keep me", None) == "keep me"
    assert _note_with_failure_reason(None, None) is None
