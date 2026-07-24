"""scripts/scout.py — gathering/cap logic + queue-pick parsing + the digest's
Scout-pick line. All mocked, no vault or network."""
from pathlib import Path

from scripts import scout


def _make_vault(tmp_path: Path) -> Path:
    (tmp_path / "reels").mkdir()
    (tmp_path / "resources").mkdir()
    return tmp_path


def _reel(vault: Path, name: str, priority: str, body: str = "content") -> None:
    (vault / "reels" / name).write_text(
        f"---\nshortcode: X\npriority: {priority}\n---\n\n{body}\n", encoding="utf-8"
    )


# --- gathering ------------------------------------------------------------------

def test_gathers_only_high_priority_reels_plus_all_resources(tmp_path):
    vault = _make_vault(tmp_path)
    _reel(vault, "2026-07-20-AAA.md", "High")
    _reel(vault, "2026-07-21-BBB.md", "Low")
    _reel(vault, "2026-07-22-CCC.md", "High")
    (vault / "resources" / "AAA-tool.md").write_text("resource body", encoding="utf-8")

    sections = scout.gather_sections(vault)
    headings = [h for h, _ in sections]
    assert headings[0] == "INSTALLED.md"                       # always first
    assert "reels/2026-07-22-CCC.md" in headings               # newest High first
    assert headings.index("reels/2026-07-22-CCC.md") < headings.index("reels/2026-07-20-AAA.md")
    assert "reels/2026-07-21-BBB.md" not in headings           # Low excluded
    assert "resources/AAA-tool.md" in headings


def test_missing_installed_md_treated_as_empty(tmp_path):
    vault = _make_vault(tmp_path)
    sections = scout.gather_sections(vault)
    assert sections[0][0] == "INSTALLED.md"
    assert "does not exist yet" in sections[0][1]


def test_installed_md_content_included_when_present(tmp_path):
    vault = _make_vault(tmp_path)
    (vault / "INSTALLED.md").write_text("## MCP servers\n- notion-mcp", encoding="utf-8")
    sections = scout.gather_sections(vault)
    assert "notion-mcp" in sections[0][1]


# --- cap ------------------------------------------------------------------------

def test_cap_drops_tail_sections_and_says_so():
    sections = [("INSTALLED.md", "small")] + [
        (f"reels/r{i}.md", "x" * 400) for i in range(10)
    ]
    text = scout.build_input(sections, max_chars=1500)
    assert "INSTALLED.md" in text
    assert "reels/r0.md" in text                # earliest (highest-priority) kept
    assert "reels/r9.md" not in text            # tail dropped
    assert "omitted to stay under" in text      # truncation is explicit
    assert len(text) < 1500 + 200               # cap held (plus the note)


def test_no_truncation_note_when_everything_fits():
    text = scout.build_input([("INSTALLED.md", "small"), ("reels/a.md", "tiny")])
    assert "omitted" not in text


# --- queue pick parsing ----------------------------------------------------------

QUEUE = """# Implementation Queue
last updated: 2026-07-24

## 1. Scroll World landing-page generator
- **Source**: [[reels/2026-07-20-Da8ey0fscUF]]

## 2. Something else
"""


def test_first_queue_pick_finds_number_one():
    assert scout.first_queue_pick(QUEUE) == "Scroll World landing-page generator"


def test_first_queue_pick_none_on_empty():
    assert scout.first_queue_pick("# Implementation Queue\n\nnothing yet") is None


# --- digest Scout-pick line ------------------------------------------------------

def test_daily_digest_includes_scout_pick_line(monkeypatch):
    from app import digest, notion_writer

    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "parent-1")
    monkeypatch.setattr(notion_writer, "read_named_page_text",
                        lambda parent, title: "Build the Scroll World generator")
    md = digest.render_daily_markdown({"saves": []})
    assert "🔭 Scout pick of the day: Build the Scroll World generator" in md


def test_daily_digest_survives_missing_scout_pick(monkeypatch):
    from app import digest, notion_writer

    monkeypatch.setattr(digest, "NOTION_PARENT_PAGE_ID", "parent-1")
    def _boom(parent, title):
        raise RuntimeError("notion down")
    monkeypatch.setattr(notion_writer, "read_named_page_text", _boom)
    md = digest.render_daily_markdown({"saves": []})
    assert "Scout pick" not in md
    assert "Nothing saved today." in md
