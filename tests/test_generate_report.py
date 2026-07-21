"""scripts/generate_report.py — mocked (pure formatting + resource-state logic)."""
from scripts import generate_report as gr


def test_oneline_collapses_and_truncates():
    assert gr._oneline("comment  AGENTS\n\nmost people use") == "comment AGENTS most people use"
    assert gr._oneline("x" * 200).endswith("…")
    assert len(gr._oneline("x" * 200)) <= 111


def test_resource_state():
    assert gr._resource_state({"gate_resource": "https://x/1", "status_label": "📥 Inbox", "gate_keyword": ""}) == "attached"
    assert gr._resource_state({"gate_resource": "", "status_label": "⏳ Awaiting DM", "gate_keyword": "SEND"}) == "pending"
    assert gr._resource_state({"gate_resource": "", "status_label": "📥 Inbox", "gate_keyword": ""}) == "n/a"


def test_build_markdown_groups_by_topic_and_lists_pending():
    rows = [
        {"shortcode": "A1", "title": "Claude MCP servers guide", "topics": ["claude-ai", "mcp-servers"],
         "priority": "High", "value_score": "5", "status_label": "⏳ Awaiting DM",
         "gate_resource": "", "gate_keyword": "STACK"},
        {"shortcode": "B2", "title": "A vibe reel", "topics": ["entertainment"],
         "priority": "Low", "value_score": "1", "status_label": "📥 Inbox",
         "gate_resource": "", "gate_keyword": ""},
    ]
    md = gr.build_markdown(rows)
    assert "**Total rows:** 2" in md
    assert "### claude-ai (1)" in md and "### mcp-servers (1)" in md
    assert "[P] (5) Claude MCP servers guide" in md
    assert "comment **STACK**" in md          # pending list
    assert "A vibe reel" in md
