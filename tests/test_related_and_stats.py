"""Integration tests for BUILD_SPEC 3.1 (near-dup/related) and 3.3 (low-signal
filter, Core source). Gemini extraction + embedding are mocked; Notion is the
FakeClient from test_pipeline.py; sqlite-vec itself runs for real (local, no
network) so similarity math is exercised honestly.
"""
from app import main, notion_writer, store
from app.main import run_pipeline
from app.models import CommentGate, Extraction
from tests.test_pipeline import FakeClient, save_page_calls


def _vector(primary_index: int, *, blend: float = 0.0, dim: int = 768) -> list[float]:
    v = [0.0] * dim
    v[primary_index] = 1.0
    if blend:
        v[(primary_index + 1) % dim] = blend
    return v


def _install(monkeypatch, reel, extraction, embed_vector=None, embed_error=None):
    fake = FakeClient()
    monkeypatch.setattr(notion_writer, "_client", lambda: fake)
    # Non-empty so _get_or_create_creator actually exercises page creation
    # instead of short-circuiting to None (which it does when unset, same as
    # in production until NOTION_CREATORS_DB_ID is configured).
    monkeypatch.setattr(notion_writer, "NOTION_CREATORS_DB_ID", "test-creators-db")
    monkeypatch.setattr(main.fetcher, "fetch_reel", lambda shortcode, permalink: reel.model_copy(
        update={"shortcode": shortcode, "permalink": permalink}
    ))
    monkeypatch.setattr(main.gemini_pipe, "run_extraction", lambda r, note, taxonomy: extraction)

    if embed_error is not None:
        def _boom(text):
            raise embed_error
        monkeypatch.setattr(main.gemini_pipe, "embed_text", _boom)
    else:
        monkeypatch.setattr(main.gemini_pipe, "embed_text", lambda text: embed_vector or _vector(0))

    return fake


def _save_page(fake) -> dict:
    calls = save_page_calls(fake)
    assert len(calls) == 1
    return calls[0]


def test_no_neighbors_means_no_related_and_no_near_dup_tag(monkeypatch, tutorial_reel, tutorial_extraction):
    fake = _install(monkeypatch, tutorial_reel, tutorial_extraction, embed_vector=_vector(0))
    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink)

    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note=None)

    props = _save_page(fake)["properties"]
    assert "Related" not in props
    assert {"name": "near-duplicate"} not in props["Topics"]["multi_select"]


def test_related_save_links_without_near_dup_tag(monkeypatch, tutorial_reel, tutorial_extraction):
    store.insert_processing("PRIOR001", "https://www.instagram.com/reel/PRIOR001/")
    store.update_save("PRIOR001", notion_page_id="page-PRIOR001", notion_page_url="https://notion.so/page-PRIOR001")
    store.upsert_embedding("PRIOR001", _vector(0, blend=0.75))  # cosine_sim 0.8 vs. the new save's vector

    fake = _install(monkeypatch, tutorial_reel, tutorial_extraction, embed_vector=_vector(0))
    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink)

    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note=None)

    props = _save_page(fake)["properties"]
    assert props["Related"]["relation"] == [{"id": "page-PRIOR001"}]
    assert {"name": "near-duplicate"} not in props["Topics"]["multi_select"]


def test_near_dup_save_links_and_tags_near_duplicate(monkeypatch, tutorial_reel, tutorial_extraction):
    store.insert_processing("DUPSRC001", "https://www.instagram.com/reel/DUPSRC001/")
    store.update_save("DUPSRC001", notion_page_id="page-DUPSRC001", notion_page_url="https://notion.so/page-DUPSRC001")
    store.upsert_embedding("DUPSRC001", _vector(0, blend=0.1))  # cosine_sim ~0.995 -> near-dup

    fake = _install(monkeypatch, tutorial_reel, tutorial_extraction, embed_vector=_vector(0))
    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink)

    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note=None)

    props = _save_page(fake)["properties"]
    assert props["Related"]["relation"] == [{"id": "page-DUPSRC001"}]
    assert {"name": "near-duplicate"} in props["Topics"]["multi_select"]
    assert "near-duplicate" in store.get_taxonomy()  # persisted to the tags table too


def test_related_skips_neighbor_with_no_notion_page(monkeypatch, tutorial_reel, tutorial_extraction):
    # A neighbor that never got a Notion page (e.g. a total pipeline failure)
    # must not produce a broken relation reference.
    store.insert_processing("NOPAGE001", "https://www.instagram.com/reel/NOPAGE001/")
    store.upsert_embedding("NOPAGE001", _vector(0, blend=0.1))

    fake = _install(monkeypatch, tutorial_reel, tutorial_extraction, embed_vector=_vector(0))
    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink)

    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note=None)

    props = _save_page(fake)["properties"]
    assert "Related" not in props


def test_embedding_failure_does_not_break_pipeline(monkeypatch, tutorial_reel, tutorial_extraction):
    fake = _install(monkeypatch, tutorial_reel, tutorial_extraction, embed_error=RuntimeError("quota exceeded"))
    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink)

    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note=None)

    props = _save_page(fake)["properties"]
    assert "Related" not in props
    row = store.get_by_shortcode(tutorial_reel.shortcode)
    assert row["status"] == "done"
    assert row["notion_page_id"]


def _low_value_extraction(**overrides) -> Extraction:
    base = dict(
        main_point="A pretty low-effort reel",
        supporting_points=[],
        content_type="entertainment",
        comment_gate=CommentGate(detected=False),
        value_score=1,
    )
    base.update(overrides)
    return Extraction(**base)


def test_low_value_score_sets_low_signal_status(monkeypatch, tutorial_reel):
    extraction = _low_value_extraction()
    fake = _install(monkeypatch, tutorial_reel, extraction, embed_vector=_vector(1))
    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink)

    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note=None)

    props = _save_page(fake)["properties"]
    assert props["Status"]["select"]["name"] == "🗑 Low signal"
    assert store.get_by_shortcode(tutorial_reel.shortcode)["status"] == "low_signal"


def test_gated_and_low_value_prioritizes_awaiting_dm(monkeypatch, tutorial_reel):
    extraction = _low_value_extraction(comment_gate=CommentGate(detected=True, keyword="SEND"))
    fake = _install(monkeypatch, tutorial_reel, extraction, embed_vector=_vector(2))
    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink)

    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note=None)

    props = _save_page(fake)["properties"]
    assert props["Status"]["select"]["name"] == "⏳ Awaiting DM"
    assert store.get_by_shortcode(tutorial_reel.shortcode)["status"] == "awaiting_dm"


def test_creator_flagged_core_source_at_five_saves(monkeypatch, tutorial_reel, tutorial_extraction):
    for i in range(4):
        shortcode = f"PRIORCREATOR{i}"
        store.insert_processing(shortcode, f"https://www.instagram.com/reel/{shortcode}/")
        store.update_save(shortcode, creator=tutorial_reel.creator_username, status="done")

    fake = _install(monkeypatch, tutorial_reel, tutorial_extraction, embed_vector=_vector(3))
    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink)

    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note=None)

    core_source_calls = [
        c for c in fake.pages.updated
        if c["properties"].get("Core source") == {"checkbox": True}
    ]
    assert len(core_source_calls) == 1


def test_creator_not_flagged_before_five_saves(monkeypatch, tutorial_reel, tutorial_extraction):
    for i in range(3):
        shortcode = f"PRIORCREATOR{i}"
        store.insert_processing(shortcode, f"https://www.instagram.com/reel/{shortcode}/")
        store.update_save(shortcode, creator=tutorial_reel.creator_username, status="done")

    fake = _install(monkeypatch, tutorial_reel, tutorial_extraction, embed_vector=_vector(4))
    store.insert_processing(tutorial_reel.shortcode, tutorial_reel.permalink)

    run_pipeline(tutorial_reel.shortcode, tutorial_reel.permalink, note=None)

    core_source_calls = [c for c in fake.pages.updated if "Core source" in c["properties"]]
    assert core_source_calls == []
