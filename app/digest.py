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
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_BASE_URL = "https://ntfy.sh"
NTFY_TIMEOUT_SECONDS = 10.0

DIGEST_DAYS = 7
DAILY_DIGEST_HOURS = 24
DAILY_DIGEST_TOP_TOPICS = 3
DAILY_TITLE_MAX_LEN = 80
DAILY_PRIORITY_ORDER = ["High", "Medium", "Low"]


def _notion_pages_since(hours: Optional[int] = None, days: Optional[int] = None) -> Optional[list[dict]]:
    """Saves pages in the window, straight from Notion (the durable source —
    FIX 2, see PROGRESS.md: local SQLite is wiped by every Render redeploy,
    which made digests report false 'nothing saved' days). Returns None on any
    Notion failure so callers can fall back to the local rows instead of
    producing an empty digest over a transient API error."""
    from datetime import timedelta

    if hours is not None:
        cutoff = _utc_naive_now() - timedelta(hours=hours)
    else:
        cutoff = _utc_naive_now() - timedelta(days=days or DIGEST_DAYS)
    try:
        return notion_writer.find_saves_pages_since(cutoff.isoformat())
    except Exception:  # noqa: BLE001 - fall back to local rather than an empty digest
        logger.warning("Notion digest query failed — falling back to local SQLite", exc_info=True)
        return None


def _collect_week_local() -> dict:
    """The original SQLite-based weekly collection — now the fallback path."""
    rows = store.get_saves_since(days=DIGEST_DAYS)
    shortcodes = [row["shortcode"] for row in rows]
    tags_by_shortcode = store.get_tags_for_shortcodes(shortcodes)

    saves = []
    for row in rows:
        main_point = None
        if row["extraction_json"]:
            try:
                main_point = json.loads(row["extraction_json"]).get("main_point")
            except (json.JSONDecodeError, AttributeError):
                pass
        saves.append({
            "shortcode": row["shortcode"],
            "main_point": main_point or row["caption"] or row["permalink"],
            "creator": row["creator"] or "(unknown)",
            "status": row["status"],
            "notion_page_url": row["notion_page_url"],
            "tags": tags_by_shortcode.get(row["shortcode"], []),
        })
    return _group_week(saves)


def _group_week(saves: list[dict]) -> dict:
    by_topic: dict[str, list[dict]] = {}
    by_creator: dict[str, list[dict]] = {}
    for save in saves:
        for tag in save["tags"]:
            by_topic.setdefault(tag, []).append(save)
        by_creator.setdefault(save["creator"], []).append(save)
    return {"saves": saves, "by_topic": by_topic, "by_creator": by_creator}


def collect_week() -> dict:
    """Past-7-days saves grouped by topic and creator. Notion-primary (durable);
    falls back to local SQLite when Notion errors or comes back empty while
    local rows exist (a Notion misconfiguration must not blank the digest)."""
    pages = _notion_pages_since(days=DIGEST_DAYS)
    if pages:
        saves = []
        for page in pages:
            fields = notion_writer.extract_digest_fields(page)
            if not fields["shortcode"]:
                continue
            # Creator is a Notion relation (not cheaply resolvable) — recover it
            # from the local row when one survives; "(unknown)" after a wipe.
            row = store.get_by_shortcode(fields["shortcode"])
            creator = row["creator"] if row and row["creator"] else "(unknown)"
            saves.append({
                "shortcode": fields["shortcode"],
                "main_point": fields["title"] or fields["permalink"],
                "creator": creator,
                "status": fields["status_label"],
                "notion_page_url": fields["page_url"],
                "tags": fields["topics"],
            })
        return _group_week(saves)
    local = _collect_week_local()
    if pages is not None and not local["saves"]:
        return _group_week([])  # genuinely nothing saved this week
    return local


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


# --- Daily reflection digest ---------------------------------------------------
#
# Same delivery mechanism as the weekly digest (Notion page under
# NOTION_PARENT_PAGE_ID), plus an ntfy.sh push if NTFY_TOPIC is configured
# (weekly digest itself doesn't use ntfy today — only the cookie-health alert
# in app/alerts.py does; see PROGRESS.md). A second, independent scheduled
# job — see SCHEDULING.md — not a replacement for the weekly one.


def _clean_title(main_point: str, max_len: int = DAILY_TITLE_MAX_LEN) -> str:
    """Collapses whitespace/newlines (raw Instagram captions are often full of
    them) and truncates to a short heading. Never the raw caption dump — for a
    degraded/placeholder row, main_point IS the caption, so this is the one
    place that keeps even that case readable as a digest entry."""
    text = " ".join((main_point or "").split())
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


def _collect_day_local() -> dict:
    """The original SQLite-based daily collection — now the fallback path."""
    rows = store.get_saves_since_hours(hours=DAILY_DIGEST_HOURS)
    saves = []
    for row in rows:
        extraction = {}
        if row["extraction_json"]:
            try:
                extraction = json.loads(row["extraction_json"])
            except (json.JSONDecodeError, AttributeError):
                extraction = {}
        main_point = extraction.get("main_point") or row["caption"] or row["permalink"]
        saves.append({
            "shortcode": row["shortcode"],
            "title": _clean_title(main_point),
            "main_point": main_point,
            "topics": extraction.get("topic_tags") or [],
            "priority": extraction.get("priority") or "Low",
            "url": row["notion_page_url"] or row["permalink"],
        })
    return {"saves": saves}


def collect_day() -> dict:
    """Past-24-hours saves for the daily digest. Notion-primary (durable —
    FIX 2: a redeploy wipes local SQLite mid-day, which made evening digests
    falsely report 'nothing saved'); falls back to local SQLite when Notion
    errors or comes back empty while local rows exist."""
    pages = _notion_pages_since(hours=DAILY_DIGEST_HOURS)
    if pages:
        saves = []
        for page in pages:
            fields = notion_writer.extract_digest_fields(page)
            if not fields["shortcode"]:
                continue
            main_point = fields["title"] or fields["permalink"]
            saves.append({
                "shortcode": fields["shortcode"],
                "title": _clean_title(main_point),
                "main_point": main_point,
                "topics": fields["topics"],
                "priority": fields["priority"] or "Low",
                "url": fields["page_url"] or fields["permalink"],
            })
        return {"saves": saves}
    local = _collect_day_local()
    if pages is not None and not local["saves"]:
        return {"saves": []}  # genuinely nothing saved today
    return local


def _daily_synthesis_line(saves: list[dict]) -> str:
    total = len(saves)
    high_count = sum(1 for s in saves if s["priority"] == "High")

    topic_counts: dict[str, int] = {}
    for save in saves:
        for topic in save["topics"]:
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
    top_topics = [
        topic for topic, _count in
        sorted(topic_counts.items(), key=lambda kv: -kv[1])[:DAILY_DIGEST_TOP_TOPICS]
    ]

    line = f"{total} {'reel' if total == 1 else 'reels'} saved today"
    if high_count:
        line += f", {high_count} flagged High priority"
    if top_topics:
        line += f" — common themes: {', '.join(top_topics)}"
    return line + "."


def _format_daily_entry(save: dict) -> str:
    topics_part = ", ".join(save["topics"]) if save["topics"] else "no topics"
    line = f"- **{save['title']}**"
    if save["main_point"] and save["main_point"] != save["title"]:
        line += f" — {save['main_point']}"
    line += f" — _{topics_part}_ — [Open reel]({save['url']})"
    return line


def render_daily_markdown(data: dict) -> str:
    """Reads like something worth reading, not a raw dump: a one-line
    synthesis up top, then entries grouped by Priority (High first). Zero
    saves today -> a short honest note, not an empty or broken digest (see
    PROGRESS.md for why: a "nothing happened" day is itself informative,
    and skipping delivery silently would look identical to the job failing)."""
    day_of = _utc_naive_now().date().isoformat()
    lines = [f"# Daily reflection — {day_of}", ""]

    saves = data["saves"]
    if not saves:
        lines.append("Nothing saved today.")
        return "\n".join(lines) + "\n"

    lines.append(_daily_synthesis_line(saves))
    lines.append("")

    by_priority: dict[str, list[dict]] = {p: [] for p in DAILY_PRIORITY_ORDER}
    for save in saves:
        by_priority.setdefault(save["priority"], []).append(save)

    for priority in DAILY_PRIORITY_ORDER:
        entries = by_priority.get(priority) or []
        if not entries:
            continue
        lines.append(f"## {priority} priority")
        lines.append("")
        for save in entries:
            lines.append(_format_daily_entry(save))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def create_daily_notion_page(markdown: str, has_saves: bool) -> Optional[dict]:
    """Same mechanism as the weekly digest's create_notion_page: a page
    directly under NOTION_PARENT_PAGE_ID, not in a database."""
    if not NOTION_PARENT_PAGE_ID:
        logger.warning("NOTION_PARENT_PAGE_ID not set — daily digest not written to Notion")
        return None
    day_of = _utc_naive_now().date().isoformat()
    title_prefix = "🌙 Daily reflection" if has_saves else "🌙 Daily reflection (nothing saved)"
    try:
        client = notion_writer._client()
        page = client.pages.create(
            parent={"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
            properties={"title": {"title": notion_writer._rich_text(f"{title_prefix} — {day_of}")}},
            children=_markdown_to_blocks(markdown),
        )
        return {"page_id": page["id"], "url": page["url"]}
    except Exception:  # noqa: BLE001 - fail soft; digest markdown still exists
        logger.exception("daily digest Notion write failed")
        return None


def send_daily_ntfy(save_count: int, synthesis_line: Optional[str]) -> bool:
    """POST to ntfy.sh/{NTFY_TOPIC} — same topic as the cookie-health alert
    (app/alerts.py), since that's the only ntfy topic this app configures.
    Best-effort: returns False (never raises) if NTFY_TOPIC isn't set or the
    request fails."""
    if not NTFY_TOPIC:
        return False
    try:
        import httpx

        if save_count == 0:
            title = "ReelBrain: nothing saved today"
            body = "No reels saved in the last 24 hours."
        else:
            title = f"ReelBrain: {save_count} saved today"
            body = synthesis_line or f"{save_count} reels saved today."
        response = httpx.post(
            f"{NTFY_BASE_URL}/{NTFY_TOPIC}",
            content=body.encode("utf-8"),
            headers={"Title": title, "Tags": "clipboard,memo"},
            timeout=NTFY_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return True
    except Exception:  # noqa: BLE001 - best-effort notification, never the critical path
        logger.warning("daily digest ntfy.sh push failed", exc_info=True)
        return False


def run_daily() -> dict:
    data = collect_day()
    saves = data["saves"]
    markdown = render_daily_markdown(data)
    page = create_daily_notion_page(markdown, has_saves=bool(saves))
    high_count = sum(1 for s in saves if s["priority"] == "High")
    synthesis_line = _daily_synthesis_line(saves) if saves else None
    ntfy_sent = send_daily_ntfy(save_count=len(saves), synthesis_line=synthesis_line)
    return {
        "markdown": markdown,
        "save_count": len(saves),
        "high_priority_count": high_count,
        "notion_page": page,
        "ntfy_sent": ntfy_sent,
    }
