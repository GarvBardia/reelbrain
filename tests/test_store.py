from app import store


def test_insert_and_get_by_shortcode():
    store.insert_processing("ABC123", "https://www.instagram.com/reel/ABC123/", note="hi")
    row = store.get_by_shortcode("ABC123")
    assert row is not None
    assert row["status"] == "processing"
    assert row["note"] == "hi"


def test_get_by_shortcode_missing_returns_none():
    assert store.get_by_shortcode("does-not-exist") is None


def test_dedupe_flow_marks_existing_notion_page():
    store.insert_processing("DUP001", "https://www.instagram.com/reel/DUP001/")
    store.update_save("DUP001", status="done", notion_page_id="page-1", notion_page_url="https://notion.so/page-1")

    row = store.get_by_shortcode("DUP001")
    assert row["notion_page_id"] == "page-1"
    # This is exactly what /capture checks to decide "duplicate, don't re-process"
    assert bool(row["notion_page_id"]) is True


def test_update_save_is_partial():
    store.insert_processing("PART001", "https://www.instagram.com/reel/PART001/")
    store.update_save("PART001", status="failed")
    row = store.get_by_shortcode("PART001")
    assert row["status"] == "failed"
    assert row["permalink"] == "https://www.instagram.com/reel/PART001/"


def test_taxonomy_orders_by_frequency():
    store.insert_processing("T1", "https://www.instagram.com/reel/T1/")
    store.insert_processing("T2", "https://www.instagram.com/reel/T2/")
    store.insert_processing("T3", "https://www.instagram.com/reel/T3/")
    store.set_tags("T1", ["ai-workflows", "productivity"])
    store.set_tags("T2", ["ai-workflows"])
    store.set_tags("T3", ["fitness"])

    taxonomy = store.get_taxonomy(limit=40)
    assert taxonomy[0] == "ai-workflows"
    assert "fitness" in taxonomy


def test_daily_fetch_count_and_record_fetch():
    assert store.get_daily_fetch_count() == 0
    store.record_fetch()
    store.record_fetch()
    assert store.get_daily_fetch_count() == 2
    assert store.get_last_fetch_at() is not None


def test_get_most_recent_awaiting_dm():
    store.insert_processing("G1", "https://www.instagram.com/reel/G1/")
    store.update_save("G1", status="awaiting_dm")
    row = store.get_most_recent_awaiting_dm()
    assert row["shortcode"] == "G1"
