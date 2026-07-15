"""Weekly digest (BUILD_SPEC Phase 4, brought forward): summarize the past 7 days
of saves into a markdown digest and a Notion page under the parent page.

The Gemini "week in 3 sentences" call is strictly optional — any failure there
(quota, network, no API key) produces the digest without it, never an error.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from app import notion_writer, store
from app.store import _utc_naive_now

logger = logging.getLogger("reelbrain.digest")

NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID", "").strip()

DIGEST_DAYS = 7


def collect_week() -> dict:
    """Past-7-days saves grouped by topic and creator."""
    rows = store.get_saves_since(days=DIGEST_DAYS)
    shortcodes = [row["shortcode"] for row in rows]
    tags_by_shortcode = store.get_tags_for_shortcodes(shortcodes)

    saves = []
    by_topic: dict[str, list[dict]] = {}
    by_creator: dict[str, list[dict]] = {}
    for row in rows:
        main_point = None
        if row["extraction_json"]:
            try:
                main_point = json.loads(row["extraction_json"]).get("main_point")
            except (json.JSONDecodeError, AttributeError):
                pass
        save = {
            "shortcode": row["shortcode"],
            "main_point": main_point or row["caption"] or row["permalink"],
            "creator": row["creator"] or "(unknown)",
            "status": row["status"],
            "notion_page_url": row["notion_page_url"],
            "tags": tags_by_shortcode.get(row["shortcode"], []),
        }
        saves.append(save)
        for tag in save["tags"]:
            by_topic.setdefault(tag, []).append(save)
        by_creator.setdefault(save["creator"], []).append(save)

    return {"saves": saves, "by_topic": by_topic, "by_creator": by_creator}


def try_ai_summary(data: dict) -> Optional[str]:
    """3-sentence week summary via Gemini. OPTIONAL — returns None on ANY failure."""
    if not data["saves"]:
        return None
    try:
        from google import genai

        from app.gemini_pipe import GEMINI_API_KEY, GEMINI_MODEL

        points = "\n".join(f"- {s['main_point']}" for s in data["saves"][:50])
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=(
                "Summarize this week's saved Instagram reel takeaways in exactly 3 "
                "sentences. Plain prose, no bullets, no preamble:\n" + points
            ),
        )
        text = (response.text or "").strip()
        return text or None
    except Exception:  # noqa: BLE001 - enhancement only, never block the digest
        logger.warning("AI week-summary skipped", exc_info=True)
        return None


def render_markdown(data: dict, ai_summary: Optional[str] = None) -> str:
    week_of = _utc_naive_now().date().isoformat()
    lines = [f"# Weekly digest — week ending {week_of}", ""]
    if not data["saves"]:
        lines.append("No reels saved this week.")
        return "\n".join(lines)

    lines.append(f"{len(data['saves'])} reels saved this week.")
    lines.append("")
    if ai_summary:
        lines += ["## Week in three sentences", "", ai_summary, ""]

    lines.append("## By topic")
    lines.append("")
    for topic, saves in sorted(data["by_topic"].items(), key=lambda kv: -len(kv[1])):
        lines.append(f"### {topic} ({len(saves)})")
        for save in saves:
            lines.append(f"- {save['main_point']}")
        lines.append("")

    lines.append("## By creator")
    lines.append("")
    for creator, saves in sorted(data["by_creator"].items(), key=lambda kv: -len(kv[1])):
        lines.append(f"- **{creator}** — {len(saves)} save(s)")
    return "\n".join(lines)


def _markdown_to_blocks(markdown: str) -> list[dict]:
    """Minimal line-based markdown -> Notion blocks (headings, bullets, paragraphs)."""
    blocks = []
    for line in markdown.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("### "):
            blocks.append({"object": "block", "type": "heading_3",
                           "heading_3": {"rich_text": notion_writer._rich_text(line[4:])}})
        elif line.startswith("## "):
            blocks.append({"object": "block", "type": "heading_2",
                           "heading_2": {"rich_text": notion_writer._rich_text(line[3:])}})
        elif line.startswith("# "):
            continue  # page title carries the h1
        elif line.startswith("- "):
            blocks.append({"object": "block", "type": "bulleted_list_item",
                           "bulleted_list_item": {"rich_text": notion_writer._rich_text(line[2:])}})
        else:
            blocks.append({"object": "block", "type": "paragraph",
                           "paragraph": {"rich_text": notion_writer._rich_text(line)}})
    return blocks[:100]  # Notion caps children at 100 blocks per request


def create_notion_page(markdown: str) -> Optional[dict]:
    """Creates the digest page directly under the parent page (not in a database).
    Returns None (logged) if the parent page ID isn't configured or the write fails —
    the markdown digest is still the caller's to keep."""
    if not NOTION_PARENT_PAGE_ID:
        logger.warning("NOTION_PARENT_PAGE_ID not set — digest not written to Notion")
        return None
    week_of = _utc_naive_now().date().isoformat()
    try:
        client = notion_writer._client()
        page = client.pages.create(
            parent={"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
            properties={"title": {"title": notion_writer._rich_text(f"📬 Weekly digest — {week_of}")}},
            children=_markdown_to_blocks(markdown),
        )
        return {"page_id": page["id"], "url": page["url"]}
    except Exception:  # noqa: BLE001 - fail soft; digest markdown still exists
        logger.exception("digest Notion write failed")
        return None


def run() -> dict:
    data = collect_week()
    ai_summary = try_ai_summary(data)
    markdown = render_markdown(data, ai_summary)
    page = create_notion_page(markdown)
    return {
        "markdown": markdown,
        "save_count": len(data["saves"]),
        "ai_summary_included": ai_summary is not None,
        "notion_page": page,
    }
