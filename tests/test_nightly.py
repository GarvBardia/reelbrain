from datetime import timedelta

from app import nightly, notion_writer, store
from app.store import _utc_naive_now
from tests.test_pipeline import FakeClient, save_page_calls


def _backdate(shortcode: str, *, created_minutes_ago: int = 0, updated_minutes_ago: int | None = None) -> None:
    created_at = (_utc_naive_now() - timedelta(minutes=created_minutes_ago)).isoformat()
    with store.get_connection() as conn:
        if updated_minutes_ago is None:
            conn.execute("UPDATE saves SET created_at = ?, updated_at = NULL WHERE shortcode = ?", (created_at, shortcode))
        else:
            updated_at = (_utc_naive_now() - timedelta(minutes=updated_minutes_ago)).isoformat()
            conn.execute(
                "UPDATE saves SET created_at = ?, updated_at = ? WHERE shortcode = ?",
                (created_at, updated_at, shortcode),
            )


def _install_fake_notion(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(notion_writer, "_client", lambda: fake)
    return fake


def test_stuck_processing_over_an_hour_marked_failed_with_existing_page(monkeypatch):
    _install_fake_notion(monkeypatch)
    store.insert_processing("STUCK001", "https://www.instagram.com/reel/STUCK001/")
    store.update_save("STUCK001", notion_page_id="page-STUCK001", notion_page_url="https://notion.so/page-STUCK001")
    _backdate("STUCK001", created_minutes_ago=90, updated_minutes_ago=90)

    updated = nightly.mark_stuck_processing_failed()

    assert updated == ["STUCK001"]
    assert store.get_by_shortcode("STUCK001")["status"] == "failed"


def test_stuck_processing_creates_page_when_none_existed(monkeypatch):
    fake = _install_fake_notion(monkeypatch)
    store.insert_processing("STUCK002", "https://www.instagram.com/reel/STUCK002/", note="orphaned row")
    _backdate("STUCK002", created_minutes_ago=90, updated_minutes_ago=90)

    updated = nightly.mark_stuck_processing_failed()

    assert updated == ["STUCK002"]
    row = store.get_by_shortcode("STUCK002")
    assert row["status"] == "failed"
    assert row["notion_page_id"]  # constraint #3: a page now exists, capture wasn't dropped

    save_calls = save_page_calls(fake)
    assert len(save_calls) == 1
    assert save_calls[0]["properties"]["Status"]["select"]["name"] == "⚠️ Failed — retry"


def test_recently_retried_row_not_marked_stuck(monkeypatch):
    """A /retry resets status to 'processing' and bumps updated_at — the nightly
    job must not immediately re-fail it just because created_at is old."""
    _install_fake_notion(monkeypatch)
    store.insert_processing("RETRIED001", "https://www.instagram.com/reel/RETRIED001/")
    # created long ago (first capture attempt), but just retried a minute ago
    _backdate("RETRIED001", created_minutes_ago=200, updated_minutes_ago=1)

    updated = nightly.mark_stuck_processing_failed()

    assert updated == []
    assert store.get_by_shortcode("RETRIED001")["status"] == "processing"


def test_fresh_processing_row_untouched(monkeypatch):
    _install_fake_notion(monkeypatch)
    store.insert_processing("FRESH001", "https://www.instagram.com/reel/FRESH001/")

    updated = nightly.mark_stuck_processing_failed()

    assert updated == []
    assert store.get_by_shortcode("FRESH001")["status"] == "processing"


def test_gate_older_than_a_week_expires(monkeypatch):
    _install_fake_notion(monkeypatch)
    store.insert_processing("OLDGATE01", "https://www.instagram.com/reel/OLDGATE01/")
    store.update_save(
        "OLDGATE01", status="awaiting_dm",
        notion_page_id="page-OLDGATE01", notion_page_url="https://notion.so/page-OLDGATE01",
    )
    _backdate("OLDGATE01", updated_minutes_ago=8 * 24 * 60)

    updated = nightly.expire_old_gates()

    assert updated == ["OLDGATE01"]
    assert store.get_by_shortcode("OLDGATE01")["status"] == "gate_expired"


def test_recent_gate_not_expired(monkeypatch):
    _install_fake_notion(monkeypatch)
    store.insert_processing("NEWGATE01", "https://www.instagram.com/reel/NEWGATE01/")
    store.update_save(
        "NEWGATE01", status="awaiting_dm",
        notion_page_id="page-NEWGATE01", notion_page_url="https://notion.so/page-NEWGATE01",
    )
    _backdate("NEWGATE01", updated_minutes_ago=60)  # 1 hour, well under 7 days

    updated = nightly.expire_old_gates()

    assert updated == []
    assert store.get_by_shortcode("NEWGATE01")["status"] == "awaiting_dm"


def test_run_reports_both_buckets(monkeypatch):
    _install_fake_notion(monkeypatch)
    store.insert_processing("A1", "https://www.instagram.com/reel/A1/")
    store.update_save("A1", notion_page_id="page-A1", notion_page_url="https://notion.so/page-A1")
    _backdate("A1", created_minutes_ago=90, updated_minutes_ago=90)

    store.insert_processing("A2", "https://www.instagram.com/reel/A2/")
    store.update_save(
        "A2", status="awaiting_dm", notion_page_id="page-A2", notion_page_url="https://notion.so/page-A2"
    )
    _backdate("A2", updated_minutes_ago=8 * 24 * 60)

    result = nightly.run()
    assert result == {"marked_failed": ["A1"], "marked_gate_expired": ["A2"]}
