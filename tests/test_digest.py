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


# --- Daily reflection digest ---------------------------------------------------

def _seed_daily_save(shortcode: str, tags: list[str], main_point: str,
                      priority: str = "Low", value_score: int = 3, hours_ago: float = 0) -> None:
    store.insert_processing(shortcode, f"https://www.instagram.com/reel/{shortcode}/")
    store.update_save(
        shortcode,
        status="done",
        extraction_json=json.dumps({
            "main_point": main_point, "topic_tags": tags,
            "priority": priority, "value_score": value_score,
        }),
        notion_page_url=f"https://notion.so/page-{shortcode}",
    )
    store.set_tags(shortcode, tags)
    if hours_ago:
        backdated = (_utc_naive_now() - timedelta(hours=hours_ago)).isoformat()
        with store.get_connection() as conn:
            conn.execute(
                "UPDATE saves SET created_at = ?, updated_at = ? WHERE shortcode = ?",
                (backdated, backdated, shortcode),
            )


def test_collect_day_excludes_rows_older_than_24_hours():
    _seed_daily_save("D_IN1", ["sleep"], "Recent tip", hours_ago=1)
    _seed_daily_save("D_IN2", ["sleep"], "Just under the wire", hours_ago=23)
    _seed_daily_save("D_OUT1", ["sleep"], "Yesterday's tip", hours_ago=25)

    data = digest.collect_day()

    assert {s["shortcode"] for s in data["saves"]} == {"D_IN1", "D_IN2"}


def test_collect_day_parses_priority_and_topics():
    _seed_daily_save("D1", ["mcp", "ai"], "MCP servers explained", priority="High", value_score=5)

    data = digest.collect_day()

    save = data["saves"][0]
    assert save["priority"] == "High"
    assert save["topics"] == ["mcp", "ai"]
    assert save["main_point"] == "MCP servers explained"
    assert save["url"] == "https://notion.so/page-D1"


def test_collect_day_row_without_extraction_json_defaults_to_low_priority():
    store.insert_processing("D_NOEX", "https://www.instagram.com/reel/D_NOEX/", note=None)
    store.update_save("D_NOEX", status="processing", caption="a raw caption")

    data = digest.collect_day()

    save = data["saves"][0]
    assert save["priority"] == "Low"
    assert save["topics"] == []
    assert save["main_point"] == "a raw caption"


def test_clean_title_collapses_whitespace_and_truncates():
    messy = "Line one\n\nLine two   with   extra   spaces\n#hashtag #spam #more" * 3
    title = digest._clean_title(messy)
    assert "\n" not in title
    assert len(title) <= digest.DAILY_TITLE_MAX_LEN + 1  # +1 for the ellipsis char
    assert title.endswith("…")


def test_clean_title_short_text_untouched():
    assert digest._clean_title("A short main point.") == "A short main point."


def test_render_daily_markdown_empty_day():
    md = digest.render_daily_markdown({"saves": []})
    assert "Nothing saved today." in md


def test_render_daily_markdown_groups_by_priority_high_first():
    saves = [
        {"shortcode": "L1", "title": "Low one", "main_point": "Low one", "topics": ["misc"], "priority": "Low", "url": "https://x/l1"},
        {"shortcode": "H1", "title": "High one", "main_point": "High one", "topics": ["claude-code"], "priority": "High", "url": "https://x/h1"},
        {"shortcode": "M1", "title": "Medium one", "main_point": "Medium one", "topics": ["fitness"], "priority": "Medium", "url": "https://x/m1"},
    ]
    md = digest.render_daily_markdown({"saves": saves})

    assert md.index("## High priority") < md.index("## Medium priority") < md.index("## Low priority")
    assert "High one" in md and "Medium one" in md and "Low one" in md


def test_render_daily_markdown_synthesis_line_content():
    saves = [
        {"shortcode": "H1", "title": "x", "main_point": "x", "topics": ["mcp", "ai"], "priority": "High", "url": "https://x/1"},
        {"shortcode": "H2", "title": "y", "main_point": "y", "topics": ["mcp"], "priority": "High", "url": "https://x/2"},
        {"shortcode": "L1", "title": "z", "main_point": "z", "topics": ["fitness"], "priority": "Low", "url": "https://x/3"},
    ]
    md = digest.render_daily_markdown({"saves": saves})

    assert "3 reels saved today" in md
    assert "2 flagged High priority" in md
    assert "mcp" in md  # most-common topic surfaces in the synthesis line


def test_format_daily_entry_omits_redundant_main_point_when_same_as_title():
    save = {"title": "Short point", "main_point": "Short point", "topics": ["x"], "url": "https://x/1"}
    entry = digest._format_daily_entry(save)
    assert entry.count("Short point") == 1  # not duplicated


def test_create_daily_notion_page_title_reflects_saves_vs_empty(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(notion_writer, "_client", lambda: fake)
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "parent-page-id")

    digest.create_daily_notion_page("# whatever", has_saves=True)
    digest.create_daily_notion_page("# whatever", has_saves=False)

    titles = [c["properties"]["title"]["title"][0]["text"]["content"] for c in fake.pages.created]
    assert "Daily reflection —" in titles[0] and "nothing saved" not in titles[0]
    assert "nothing saved" in titles[1]


def test_create_daily_notion_page_skips_without_parent_id(monkeypatch):
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "")
    assert digest.create_daily_notion_page("# whatever", has_saves=True) is None


def test_send_daily_ntfy_skipped_without_topic(monkeypatch):
    monkeypatch.setattr(digest, "NTFY_TOPIC", "")
    assert digest.send_daily_ntfy(save_count=3, synthesis_line="3 reels saved today.") is False


def test_send_daily_ntfy_posts_when_configured(monkeypatch):
    monkeypatch.setattr(digest, "NTFY_TOPIC", "test-topic")
    calls = []

    class _Resp:
        def raise_for_status(self):
            return None

    def _fake_post(url, content=None, headers=None, timeout=None):
        calls.append({"url": url, "content": content, "headers": headers})
        return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "post", _fake_post)

    sent = digest.send_daily_ntfy(save_count=2, synthesis_line="2 reels saved today.")

    assert sent is True
    assert len(calls) == 1
    assert calls[0]["url"] == "https://ntfy.sh/test-topic"
    assert b"2 reels saved today." in calls[0]["content"]


def test_send_daily_ntfy_zero_saves_message(monkeypatch):
    monkeypatch.setattr(digest, "NTFY_TOPIC", "test-topic")
    calls = []

    class _Resp:
        def raise_for_status(self):
            return None

    def _fake_post(url, content=None, headers=None, timeout=None):
        calls.append({"headers": headers, "content": content})
        return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "post", _fake_post)

    digest.send_daily_ntfy(save_count=0, synthesis_line=None)
    assert calls[0]["headers"]["Title"] == "ReelBrain: nothing saved today"


def test_run_daily_end_to_end_with_saves(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(notion_writer, "_client", lambda: fake)
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "parent-page-id")
    monkeypatch.setattr(digest, "NTFY_TOPIC", "")  # not configured -> ntfy_sent False
    _seed_daily_save("RD1", ["mcp"], "MCP thing", priority="High", value_score=5)

    result = digest.run_daily()

    assert result["save_count"] == 1
    assert result["high_priority_count"] == 1
    assert result["notion_page"] is not None
    assert result["ntfy_sent"] is False
    assert "MCP thing" in result["markdown"]
    assert "## High priority" in result["markdown"]


def test_run_daily_zero_saves_still_writes_a_notion_note(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(notion_writer, "_client", lambda: fake)
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "parent-page-id")

    result = digest.run_daily()

    assert result["save_count"] == 0
    assert result["notion_page"] is not None
    assert "Nothing saved today." in result["markdown"]
    call = fake.pages.created[0]
    assert "nothing saved" in call["properties"]["title"]["title"][0]["text"]["content"]


def test_run_daily_survives_notion_failure(monkeypatch):
    def _boom():
        raise RuntimeError("notion down")
    monkeypatch.setattr(notion_writer, "_client", _boom)
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "parent-page-id")
    _seed_daily_save("RD2", ["sleep"], "Sleep tip")

    result = digest.run_daily()
    assert result["notion_page"] is None
    assert "Sleep tip" in result["markdown"]
