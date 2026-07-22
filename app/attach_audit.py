"""Durable audit trail for every /attach and /attach/confirm attempt.

The Da8IIonEhGR/DbFDY3yTwlI incidents (see PROGRESS.md) were only
diagnosable at all via forensic reconstruction — comparing Notion's
created_time/last_edited_time, cross-checking Render's access logs (which
don't capture request bodies), and grepping for the resource URL across
every row. Nothing about a resolution attempt was ever actually recorded.
This writes one line per attempt to a single persistent Notion page (an
APPEND, not a replace — history must survive, unlike a digest that's meant
to reflect only "now") so a future mismatch can be looked up directly
instead of reconstructed after the fact.

Best-effort only: a logging failure must never break the actual attach flow
it's observing.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("reelbrain.attach_audit")

NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID", "").strip()
AUDIT_LOG_TITLE = "🔍 Attach Audit Log"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def format_entry(
    shortcode_or_note: Optional[str],
    resource_url: str,
    outcome: str,
    shortcode: Optional[str] = None,
    candidates: Optional[list[str]] = None,
    detail: Optional[str] = None,
) -> str:
    parts = [f"[{_now()}]", f"outcome={outcome}", f"input_shortcode_or_note={shortcode_or_note!r}",
             f"resource_url={resource_url}"]
    if shortcode:
        parts.append(f"shortcode={shortcode}")
    if candidates:
        parts.append(f"candidates={candidates}")
    if detail:
        parts.append(f"detail={detail}")
    return " | ".join(parts)


def record(
    shortcode_or_note: Optional[str],
    resource_url: str,
    outcome: str,
    shortcode: Optional[str] = None,
    candidates: Optional[list[str]] = None,
    detail: Optional[str] = None,
) -> None:
    """Never raises. Always logs locally (visible in Render's log stream
    immediately); additionally appends to the durable Notion audit page when
    NOTION_PARENT_PAGE_ID is configured."""
    line = format_entry(shortcode_or_note, resource_url, outcome, shortcode, candidates, detail)
    logger.info("attach audit: %s", line)
    if not NOTION_PARENT_PAGE_ID:
        return
    try:
        from app import notion_writer

        notion_writer.append_to_named_page(
            NOTION_PARENT_PAGE_ID, AUDIT_LOG_TITLE,
            [{"object": "block", "type": "paragraph",
              "paragraph": {"rich_text": notion_writer._rich_text(line)}}],
        )
    except Exception:  # noqa: BLE001 - audit logging must never break /attach itself
        logger.warning("attach audit: Notion append failed", exc_info=True)
