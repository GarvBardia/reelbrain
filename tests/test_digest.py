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


def test_render_markdown_empty_week():
    md = digest.render_markdown({"saves": [], "by_topic": {}})
    assert "No reels saved this week." in md


def test_render_markdown_with_and_without_ai_summary():
    _seed_save("WK1", "jane", ["sleep"], "Sleep tip")
    data = digest.collect_week()

    without = digest.render_markdown(data, ai_summary=None)
    assert "Sleep tip" in without
    assert "## Low priority" in without  # no priority in extraction_json -> defaults Low
    assert "## Topics this week" in without
    assert "sleep (1)" in without

    with_summary = digest.render_markdown(data, ai_summary="Big week. Much sleep. Wow.")
    assert "Big week. Much sleep. Wow." in with_summary


def test_try_ai_summary_fails_soft_when_gemini_unavailable():
    # conftest blocks genai.Client construction — the guard IS the failure mode here
    _seed_save("WK1", "jane", ["sleep"], "Sleep tip")
    data = digest.collect_week()
    assert digest.try_ai_summary(data) is None


def test_try_ai_daily_summary_fails_soft_when_gemini_unavailable():
    _seed_daily_save("D1", ["sleep"], "Sleep tip")
    data = digest.collect_day()
    assert digest.try_ai_daily_summary(data) is None


def test_render_daily_markdown_includes_ai_summary_when_present():
    saves = [
        {"shortcode": "H1", "title": "x", "main_point": "x", "topics": ["mcp"], "priority": "High", "url": "https://x/1"},
    ]
    md = digest.render_daily_markdown({"saves": saves}, ai_summary="A short reflective note.")
    assert "A short reflective note." in md


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
    assert call["properties"]["title"]["title"][0]["text"]["content"] == digest.WEEKLY_DIGEST_TITLE
    block_types = {b["type"] for b in call["children"]}
    assert "heading_2" in block_types and "bulleted_list_item" in block_types


def test_create_notion_page_skips_without_parent_id(monkeypatch):
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "")
    assert digest.create_notion_page("# whatever") is None


# --- single persistent page: create_notion_page/create_daily_notion_page ------
# always call notion_writer.upsert_named_page with a FIXED title (never a
# per-run dated title) -- the find-or-create/replace mechanism itself is
# tested at the notion_writer layer (tests/test_notion_writer.py).

def test_create_notion_page_uses_persistent_title(monkeypatch):
    calls = []

    def _fake_upsert(parent_id, title, children):
        calls.append((parent_id, title, children))
        return {"page_id": "pg-1", "url": "https://notion.so/pg-1"}

    monkeypatch.setattr(notion_writer, "upsert_named_page", _fake_upsert)
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "parent-page-id")

    result = digest.create_notion_page("# whatever\n\n- a bullet")

    assert result == {"page_id": "pg-1", "url": "https://notion.so/pg-1"}
    assert len(calls) == 1
    parent_id, title, children = calls[0]
    assert parent_id == "parent-page-id"
    assert title == digest.WEEKLY_DIGEST_TITLE
    assert any(b["type"] == "bulleted_list_item" for b in children)


def test_create_daily_notion_page_uses_persistent_title(monkeypatch):
    calls = []

    def _fake_upsert(parent_id, title, children):
        calls.append((parent_id, title, children))
        return {"page_id": "pg-2", "url": "https://notion.so/pg-2"}

    monkeypatch.setattr(notion_writer, "upsert_named_page", _fake_upsert)
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "parent-page-id")

    result = digest.create_daily_notion_page("# whatever")

    assert result == {"page_id": "pg-2", "url": "https://notion.so/pg-2"}
    assert calls[0][1] == digest.DAILY_DIGEST_TITLE


def test_digest_titles_stay_constant_across_repeated_runs(monkeypatch):
    """The whole point of the fix: the title never changes run to run (it's
    the lookup key), regardless of whether anything was saved."""
    titles_seen = []

    def _fake_upsert(parent_id, title, children):
        titles_seen.append(title)
        return {"page_id": "pg-1", "url": "https://notion.so/pg-1"}

    monkeypatch.setattr(notion_writer, "upsert_named_page", _fake_upsert)
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "parent-page-id")

    digest.create_daily_notion_page("# day one, lots saved")
    digest.create_daily_notion_page("# day two, nothing saved")

    assert titles_seen == [digest.DAILY_DIGEST_TITLE, digest.DAILY_DIGEST_TITLE]


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


def test_format_entry_includes_topic_clause_and_link():
    save = {"title": "Great point", "topics": ["ai", "tools"], "url": "https://x/1", "shortcode": "S1"}
    entry = digest._format_entry(save)
    assert entry == "- Great point (filed under ai and tools). [Open the reel](https://x/1)"


def test_format_entry_omits_topic_clause_when_no_topics():
    save = {"title": "Great point", "topics": [], "url": "https://x/1", "shortcode": "S1"}
    entry = digest._format_entry(save)
    assert entry == "- Great point. [Open the reel](https://x/1)"
    assert "no topics" not in entry  # no raw field-dump filler phrase


def test_create_daily_notion_page_skips_without_parent_id(monkeypatch):
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "")
    assert digest.create_daily_notion_page("# whatever") is None


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
    # title is fixed regardless of whether anything was saved -- the "nothing
    # saved" fact lives in the body text, not the (persistent, lookup-key) title
    assert call["properties"]["title"]["title"][0]["text"]["content"] == digest.DAILY_DIGEST_TITLE


# --- FIX 2: digests are Notion-primary (ephemeral SQLite wipes made them lie) ---

def _digest_page(shortcode, title, topics=(), priority="Low", value="3", status="📥 Inbox"):
    return {
        "id": f"pg-{shortcode}",
        "url": f"https://notion.so/pg-{shortcode}",
        "properties": {
            "Shortcode": {"rich_text": [{"plain_text": shortcode}]},
            "Title": {"title": [{"plain_text": title}]},
            "Topics": {"multi_select": [{"name": t} for t in topics]},
            "Priority": {"select": {"name": priority}},
            "Value score": {"select": {"name": value}},
            "Status": {"select": {"name": status}},
            "Reel URL": {"url": f"https://www.instagram.com/reel/{shortcode}/"},
        },
    }


class _DigestDS:
    def __init__(self, pages):
        self.pages = pages
        self.filters = []

    def query(self, **kwargs):
        self.filters.append(kwargs.get("filter"))
        return {"results": self.pages, "has_more": False}


def test_collect_day_prefers_notion_over_empty_local_sqlite(monkeypatch):
    """The exact incident: SQLite wiped by a redeploy, Notion still has the
    day's saves — the digest must show them, not 'nothing saved'."""
    client = FakeClient()
    client.data_sources = _DigestDS([
        _digest_page("NT1", "A real Notion-sourced point", topics=("mcp",), priority="High"),
    ])
    monkeypatch.setattr(notion_writer, "_client", lambda: client)
    # local SQLite deliberately empty (fresh tmp_db) — the wiped-disk scenario

    data = digest.collect_day()

    assert len(data["saves"]) == 1
    save = data["saves"][0]
    assert save["shortcode"] == "NT1"
    assert save["main_point"] == "A real Notion-sourced point"
    assert save["topics"] == ["mcp"]
    assert save["priority"] == "High"
    assert save["url"] == "https://notion.so/pg-NT1"
    # and the query was a created_time window filter
    assert client.data_sources.filters[0]["timestamp"] == "created_time"
    assert "on_or_after" in client.data_sources.filters[0]["created_time"]


def test_collect_week_prefers_notion_and_groups_by_topic(monkeypatch):
    client = FakeClient()
    client.data_sources = _DigestDS([
        _digest_page("WKN1", "Point one", topics=("sleep", "health")),
        _digest_page("WKN2", "Point two", topics=("sleep",)),
    ])
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    data = digest.collect_week()

    assert {s["shortcode"] for s in data["saves"]} == {"WKN1", "WKN2"}
    assert len(data["by_topic"]["sleep"]) == 2
    assert data["saves"][0]["creator"] == "(unknown)"  # relation not resolvable post-wipe


def test_collect_day_falls_back_to_local_when_notion_errors(monkeypatch):
    def _boom():
        raise RuntimeError("notion down")
    monkeypatch.setattr(notion_writer, "_client", _boom)
    _seed_daily_save("FB1", ["sleep"], "Local fallback point", priority="High")

    data = digest.collect_day()
    assert [s["shortcode"] for s in data["saves"]] == ["FB1"]


def test_collect_day_notion_empty_and_local_empty_is_genuinely_zero(monkeypatch):
    client = FakeClient()  # empty query results
    monkeypatch.setattr(notion_writer, "_client", lambda: client)
    assert digest.collect_day() == {"saves": []}


def test_find_saves_pages_since_paginates(monkeypatch):
    calls = []

    class _PagedDS:
        def query(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return {"results": [_digest_page("P1", "one")], "has_more": True, "next_cursor": "c2"}
            return {"results": [_digest_page("P2", "two")], "has_more": False}

    client = FakeClient()
    client.data_sources = _PagedDS()
    monkeypatch.setattr(notion_writer, "_client", lambda: client)

    pages = notion_writer.find_saves_pages_since("2026-07-18T00:00:00")
    assert [notion_writer.extract_digest_fields(p)["shortcode"] for p in pages] == ["P1", "P2"]
    assert calls[1]["start_cursor"] == "c2"


def test_run_daily_survives_notion_failure(monkeypatch):
    def _boom():
        raise RuntimeError("notion down")
    monkeypatch.setattr(notion_writer, "_client", _boom)
    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "parent-page-id")
    _seed_daily_save("RD2", ["sleep"], "Sleep tip")

    result = digest.run_daily()
    assert result["notion_page"] is None
    assert "Sleep tip" in result["markdown"]
