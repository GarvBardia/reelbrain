"""Integration test: mocked fetcher + Gemini, but a fake Notion *client* (not a
mocked notion_writer) so we actually exercise the property/block-building logic
and can assert on the resulting Notion payload shape, per BUILD_SPEC's Testing section.

The fake mirrors Notion's 2025-09-03+ "data source" API shape that notion_writer.py
actually uses (databases.retrieve -> data_sources, data_sources.query, pages with a
data_source_id parent) — see the NOTE at the top of app/notion_writer.py.
"""
from app import notion_writer, store
from app.main import run_pipeline


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
