"""scripts/flag_processed.py — mocked predicate + run logic."""
from scripts import flag_processed as fp


def test_processed_worthy_predicate():
    # real extraction: synthesized title + real topics
    assert fp.is_processed_worthy("Five essential Claude MCP servers", ["claude-ai", "mcp"]) is True
    # placeholder title -> not worthy
    assert fp.is_processed_worthy(fp.PLACEHOLDER_TITLE, ["claude-ai"]) is False
    # raw-caption title but NO real topics -> not worthy (degraded)
    assert fp.is_processed_worthy("comment AGENTS for the guide", []) is False
    # only near-duplicate tag -> not real topics -> not worthy
    assert fp.is_processed_worthy("comment GUIDE for the playbook", ["near-duplicate"]) is False
    # empty title -> not worthy
    assert fp.is_processed_worthy("", ["claude-ai"]) is False


def _page(shortcode, title, topics, processed=False):
    return {
        "id": f"pg-{shortcode}",
        "properties": {
            "Shortcode": {"rich_text": [{"plain_text": shortcode}]},
            "Title": {"title": [{"plain_text": title}]},
            "Topics": {"multi_select": [{"name": t} for t in topics]},
            "Priority": {"select": {"name": "High"}},
            "Value score": {"select": {"name": "5"}},
            "Status": {"select": {"name": "📥 Inbox"}},
            "Reel URL": {"url": f"https://www.instagram.com/reel/{shortcode}/"},
            "Processed": {"checkbox": processed},
        },
    }


def test_run_dry_run_counts_worthy_vs_unworthy(monkeypatch):
    from app import notion_writer
    pages = [
        _page("REAL1", "A real summary", ["claude-ai"]),
        _page("REAL2", "Another real one", ["ai-tools"], processed=True),   # already flagged
        _page("PLACE1", fp.PLACEHOLDER_TITLE, []),
        _page("RAW1", "comment AGENTS for guide", []),
    ]
    monkeypatch.setattr(notion_writer, "find_saves_pages_since", lambda iso: pages)

    summary = fp.run(dry_run=True, print_fn=lambda m: None)
    assert summary["worthy"] == 2       # REAL1 + REAL2
    assert summary["to_set"] == 1       # only REAL1 (REAL2 already flagged)
    assert summary["unworthy"] == 2     # PLACE1 + RAW1
    assert summary["flagged"] == 0      # dry-run writes nothing
