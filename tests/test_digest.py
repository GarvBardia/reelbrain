"""Weekly digest tests — Notion faked, Gemini blocked at the client level by the
conftest guard (so the 'AI summary fails' path is exercised for real)."""
import json
from datetime import timedelta

from app import digest, notion_writer, store
from app.store import _utc_naive_now
from tests.test_pipeline import FakeClient


def _seed_save(shortcode: str, creator: str, tags: list[str], main_point: str,
               days_ago: int = 0) -> None:
    store.insert_processing(shortcode, f"https://www.instagram.com/reel/{shortcode}/")
    store.update_save(
        shortcode,
        creator=creator,
        status="done",
        extraction_json=json.dumps({"main_point": main_point}),
        notion_page_url=f"https://notion.so/page-{shortcode}",
    )
    store.set_tags(shortcode, tags)
    if days_ago:
        backdated = (_utc_naive_now() - timedelta(days=days_ago)).isoformat()
        with store.get_connection() as conn:
            conn.execute(
                "UPDATE saves SET created_at = ?, updated_at = ? WHERE shortcode = ?",
                (backdated, backdated, shortcode),
            )


def test_collect_week_groups_and_excludes_old_rows():
    _seed_save("WK1", "jane", ["sleep", "health"], "Sleep tip")
    _seed_save("WK2", "jane", ["sleep"], "Another sleep tip")
    _seed_save("WK3", "bob", ["finance"], "Money tip")
    _seed_save("OLD1", "jane", ["sleep"], "Ancient tip", days_ago=10)

    data = digest.collect_week()

    assert {s["shortcode"] for s in data["saves"]} == {"WK1", "WK2", "WK3"}
    assert len(data["by_topic"]["sleep"]) == 2
    assert len(data["by_creator"]["jane"]) == 2
    assert len(data["by_creator"]["bob"]) == 1


def test_render_markdown_empty_week():
    md = digest.render_markdown({"saves": [], "by_topic": {}, "by_creator": {}})
    assert "No reels saved this week." in md


def test_render_markdown_with_and_without_ai_summary():
    _seed_save("WK1", "jane", ["sleep"], "Sleep tip")
    data = digest.collect_week()

    without = digest.render_markdown(data, ai_summary=None)
    assert "Week in three sentences" not in without
    assert "Sleep tip" in without
    assert "### sleep (1)" in without
    assert "**jane** — 1 save(s)" in without

    with_summary = digest.render_markdown(data, ai_summary="Big week. Much sleep. Wow.")
    assert "Week in three sentences" in with_summary
    assert "Big week. Much sleep. Wow." in with_summary


def test_try_ai_summary_fails_soft_when_gemini_unavailable():
    # conftest blocks genai.Client construction — the guard IS the failure mode here
    _seed_save("WK1", "jane", ["sleep"], "Sleep tip")
    data = digest.collect_week()
    assert digest.try_ai_summary(data) is None


def test_run_produces_digest_and_notion_page_despite_gemini_failure(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(notion_writer, "_client", lambda: fake)
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "parent-page-id")
    _seed_save("WK1", "jane", ["sleep"], "Sleep tip")

    result = digest.run()

    assert result["save_count"] == 1
    assert result["ai_summary_included"] is False  # Gemini blocked -> failed soft
    assert result["notion_page"] is not None
    assert "Sleep tip" in result["markdown"]

    assert len(fake.pages.created) == 1
    call = fake.pages.created[0]
    assert call["parent"] == {"type": "page_id", "page_id": "parent-page-id"}
    assert "Weekly digest" in call["properties"]["title"]["title"][0]["text"]["content"]
    block_types = {b["type"] for b in call["children"]}
    assert "heading_2" in block_types and "bulleted_list_item" in block_types


def test_create_notion_page_skips_without_parent_id(monkeypatch):
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "")
    assert digest.create_notion_page("# whatever") is None


def test_run_survives_notion_failure(monkeypatch):
    def _boom():
        raise RuntimeError("notion down")
    monkeypatch.setattr(notion_writer, "_client", _boom)
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "parent-page-id")
    _seed_save("WK1", "jane", ["sleep"], "Sleep tip")

    result = digest.run()
    assert result["notion_page"] is None
    assert "Sleep tip" in result["markdown"]  # markdown still produced


def test_markdown_to_blocks_caps_at_100():
    md = "\n".join(f"- point {i}" for i in range(150))
    assert len(digest._markdown_to_blocks(md)) == 100
