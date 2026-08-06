"""scripts/awaiting_dm_report.py — the Awaiting DM worklist. Pure/mocked:
collect_rows/render_markdown never touch Notion or the filesystem directly."""
from scripts import awaiting_dm_report as adr


def _page(shortcode, title, gate_keyword, status, posted=None, created="2026-01-01T00:00:00.000Z"):
    props = {
        "Shortcode": {"rich_text": [{"plain_text": shortcode}]},
        "Title": {"title": [{"plain_text": title}]},
        "Gate keyword": {"rich_text": [{"plain_text": gate_keyword}] if gate_keyword else []},
        "Status": {"select": {"name": status}},
        "Reel URL": {"url": f"https://www.instagram.com/reel/{shortcode}/"},
    }
    if posted:
        props["Posted at"] = {"date": {"start": posted}}
    return {"id": f"pg-{shortcode}", "created_time": created, "properties": props}


# --- collection: only Awaiting DM rows, oldest first ----------------------------------

def test_collects_only_awaiting_dm_rows():
    pages = [
        _page("A", "gated one", "SEND", "⏳ Awaiting DM"),
        _page("B", "inbox one", None, "📥 Inbox"),
    ]
    rows = adr.collect_rows(pages)
    assert [r["shortcode"] for r in rows] == ["A"]


def test_sorts_oldest_first_by_posted_date():
    pages = [
        _page("NEW", "newer", "X", "⏳ Awaiting DM", posted="2026-06-01"),
        _page("OLD", "older", "Y", "⏳ Awaiting DM", posted="2026-01-01"),
    ]
    rows = adr.collect_rows(pages)
    assert [r["shortcode"] for r in rows] == ["OLD", "NEW"]


def test_falls_back_to_created_time_when_posted_at_is_missing():
    pages = [_page("A", "t", "K", "⏳ Awaiting DM", posted=None, created="2026-03-15T00:00:00.000Z")]
    rows = adr.collect_rows(pages)
    assert rows[0]["date"] == "2026-03-15T00:00:00.000Z"


def test_row_with_no_shortcode_is_skipped():
    page = _page("", "t", "K", "⏳ Awaiting DM")
    assert adr.collect_rows([page]) == []


def test_permalink_falls_back_to_a_constructed_url_when_missing():
    page = _page("XYZ", "t", "K", "⏳ Awaiting DM")
    page["properties"]["Reel URL"] = {"url": None}
    rows = adr.collect_rows([page])
    assert rows[0]["permalink"] == "https://www.instagram.com/reel/XYZ/"


# --- rendering --------------------------------------------------------------------------

def test_render_shows_row_count_and_table():
    rows = [{"shortcode": "A", "title": "Some title", "gate_keyword": "SEND",
             "date": "2026-01-01", "permalink": "https://www.instagram.com/reel/A/"}]
    md = adr.render_markdown(rows)
    assert "**1 row(s) waiting.**" in md
    assert "SEND" in md and "2026-01-01" in md and "[A]" in md


def test_render_handles_zero_rows_gracefully():
    md = adr.render_markdown([])
    assert "**0 row(s) waiting.**" in md
    assert "Nothing waiting" in md


def test_render_escapes_pipe_characters_in_title_and_keyword():
    rows = [{"shortcode": "A", "title": "a | b", "gate_keyword": "X|Y",
             "date": "2026-01-01", "permalink": "https://x/A/"}]
    md = adr.render_markdown(rows)
    assert r"a \| b" in md and r"X\|Y" in md


def test_render_shows_question_mark_for_missing_keyword():
    rows = [{"shortcode": "A", "title": "t", "gate_keyword": "",
             "date": "2026-01-01", "permalink": "https://x/A/"}]
    md = adr.render_markdown(rows)
    assert "`?`" in md
