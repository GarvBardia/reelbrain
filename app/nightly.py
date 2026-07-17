"""Nightly cleanup job. BUILD_SPEC.md §2.3.

Rows stuck in 'processing' for too long (the server died mid-pipeline, or a
background task silently swallowed something) get marked failed. Awaiting-DM
rows nobody ever attached a resource to expire after a week. Both paths still
write a Notion status update — constraint #3 (never silently drop a capture)
applies just as much to a stale row as to a fresh capture.
"""
from __future__ import annotations

import logging

from app import alerts, notion_writer, store
from app.models import ReelData

logger = logging.getLogger("reelbrain.nightly")

STUCK_PROCESSING_MINUTES = 60
GATE_EXPIRY_DAYS = 7
ARCHIVE_AFTER_DAYS = 30
ARCHIVE_MAX_VALUE_SCORE = 2


def _row_to_reel_data(row) -> ReelData:
    return ReelData(
        shortcode=row["shortcode"],
        permalink=row["permalink"],
        caption=row["caption"],
        creator_username=row["creator"],
        creator_fullname=row["creator_fullname"],
        taken_at=row["taken_at"],
    )


def _ensure_notion_status(row, status: str) -> None:
    if row["notion_page_id"]:
        notion_writer.set_status(row["notion_page_id"], status)
        return
    # No page ever got created for this row (e.g. the process died before the
    # pipeline reached notion_writer) — create one now rather than letting the
    # capture vanish with no trace anywhere.
    result = notion_writer.create_page(_row_to_reel_data(row), None, status, note=row["note"])
    store.update_save(row["shortcode"], notion_page_id=result["page_id"], notion_page_url=result["url"])


def mark_stuck_processing_failed() -> list[str]:
    updated = []
    for row in store.get_stuck_processing(older_than_minutes=STUCK_PROCESSING_MINUTES):
        store.update_save(row["shortcode"], status="failed")
        try:
            _ensure_notion_status(row, "failed")
        except Exception:  # noqa: BLE001 - SQLite status is already correct either way
            logger.exception("failed to sync Notion status for stuck row %s", row["shortcode"])
        updated.append(row["shortcode"])
    return updated


def expire_old_gates() -> list[str]:
    updated = []
    for row in store.get_expired_gates(older_than_days=GATE_EXPIRY_DAYS):
        store.update_save(row["shortcode"], status="gate_expired")
        try:
            _ensure_notion_status(row, "gate_expired")
        except Exception:  # noqa: BLE001
            logger.exception("failed to sync Notion status for expired-gate row %s", row["shortcode"])
        updated.append(row["shortcode"])
    return updated


def archive_stale_low_value() -> list[str]:
    """Auto-archive: value_score <= 2 rows untouched for 30+ days flip to
    🗄 Archived so the Inbox/Low-signal views stay uncluttered. Notion status
    sync is best-effort like the other nightly passes."""
    updated = []
    for row in store.get_archivable(
        older_than_days=ARCHIVE_AFTER_DAYS, max_value_score=ARCHIVE_MAX_VALUE_SCORE
    ):
        store.update_save(row["shortcode"], status="archived")
        try:
            _ensure_notion_status(row, "archived")
        except Exception:  # noqa: BLE001
            logger.exception("failed to sync Notion status for archived row %s", row["shortcode"])
        updated.append(row["shortcode"])
    return updated


def run() -> dict:
    return {
        "marked_failed": mark_stuck_processing_failed(),
        "marked_gate_expired": expire_old_gates(),
        "marked_archived": archive_stale_low_value(),
        "cookie_alert": alerts.check_and_alert(),
    }
