"""Cookie-health alerting. BUILD_SPEC-adjacent ops concern, not a spec section:
fires when consecutive cookie-backed auth failures cross fetcher.AUTH_FAILURE_THRESHOLD,
meaning the burner cookies have very likely expired.

Detection + notification ONLY — nothing here ever attempts to log in, refresh a
session, or touch the burner account. That's a human's job, on purpose (auto-refresh
risks the account); see COOKIES.md for the 2-minute manual fix.

Two channels, both best-effort (never raise, never block the nightly job):
  - Notion: a distinctly-titled page under NOTION_PARENT_PAGE_ID (a lightweight
    "System Alerts" area — no new database needed).
  - ntfy.sh: a real push notification to your phone, zero config beyond a topic
    name. Recommended as the simpler channel (see COOKIES.md) but Notion is wired
    up regardless since the token's already configured.

At most one alert per calendar day while degraded, so a nightly job running (or
being manually re-triggered) multiple times in one day doesn't spam either channel.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from app import fetcher, notion_writer, store

logger = logging.getLogger("reelbrain.alerts")

NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID", "").strip()
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_BASE_URL = "https://ntfy.sh"
NTFY_TIMEOUT_SECONDS = 10.0

_LAST_ALERT_DATE_KEY = "cookie_alert_last_sent_date"


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _already_alerted_today() -> bool:
    return store.get_state(_LAST_ALERT_DATE_KEY) == _today()


def _mark_alerted_today() -> None:
    store.set_state(_LAST_ALERT_DATE_KEY, _today())


def build_alert_message(consecutive_failures: int) -> str:
    return (
        f"ReelBrain: {consecutive_failures} consecutive cookie-auth failures on "
        "Instagram fetches — burner cookies have very likely expired. "
        "Fix: see COOKIES.md (browser login -> export cookies.txt -> Render Secret File)."
    )


def send_ntfy_alert(message: str) -> bool:
    """POST to ntfy.sh/{NTFY_TOPIC} — no account, no auth. The topic name itself
    is the only thing gating who can post/subscribe, so pick something
    unguessable, not "reelbrain". Returns True on success; never raises."""
    if not NTFY_TOPIC:
        return False
    try:
        import httpx

        response = httpx.post(
            f"{NTFY_BASE_URL}/{NTFY_TOPIC}",
            content=message.encode("utf-8"),
            headers={
                "Title": "ReelBrain: cookies likely expired",
                "Priority": "high",
                "Tags": "cookie,warning",
            },
            timeout=NTFY_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return True
    except Exception:  # noqa: BLE001 - best-effort notification, never the critical path
        logger.warning("ntfy.sh alert failed", exc_info=True)
        return False


def send_notion_alert(message: str) -> Optional[dict]:
    """Creates a distinctly-titled page under the parent page — a lightweight
    "System Alerts" area without needing a whole new database."""
    if not NOTION_PARENT_PAGE_ID:
        logger.warning("NOTION_PARENT_PAGE_ID not set — cookie alert not written to Notion")
        return None
    try:
        client = notion_writer._client()
        page = client.pages.create(
            parent={"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
            properties={
                "title": {
                    "title": notion_writer._rich_text(
                        f"⚙️ System Alert — cookies likely expired — {_today()}"
                    )
                }
            },
            children=[
                {
                    "object": "block",
                    "type": "callout",
                    "callout": {
                        "rich_text": notion_writer._rich_text(message),
                        "icon": {"emoji": "⚠️"},
                    },
                }
            ],
        )
        return {"page_id": page["id"], "url": page["url"]}
    except Exception:  # noqa: BLE001 - best-effort notification, never the critical path
        logger.exception("cookie alert Notion write failed")
        return None


def send_gate_nudge(entries: list[dict]) -> bool:
    """FIX 3 (see PROGRESS.md): one plain ntfy push listing rows stuck in
    Awaiting DM for >24h — title + gate keyword each — so pending comment-gates
    don't silently rot. Deliberately NOT any form of DM automation (bot-DMing
    risks the burner account); just a reminder to go do the manual step.
    Best-effort: returns False (never raises) if NTFY_TOPIC isn't set or the
    request fails."""
    if not NTFY_TOPIC or not entries:
        return False
    lines = []
    for entry in entries:
        keyword = entry.get("gate_keyword") or "?"
        title = (entry.get("title") or entry.get("shortcode") or "(untitled)")[:70]
        lines.append(f'- "{title}" — comment keyword: {keyword}')
    message = (
        f"{len(entries)} reel(s) awaiting your DM for over 24h:\n" + "\n".join(lines)
    )
    try:
        import httpx

        response = httpx.post(
            f"{NTFY_BASE_URL}/{NTFY_TOPIC}",
            content=message.encode("utf-8"),
            headers={"Title": "ReelBrain: comment gates awaiting DM", "Tags": "hourglass_flowing_sand"},
            timeout=NTFY_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return True
    except Exception:  # noqa: BLE001 - best-effort notification, never the critical path
        logger.warning("gate-nudge ntfy push failed", exc_info=True)
        return False


def check_and_alert() -> dict:
    """Called from the nightly job. Fires at most once per calendar day while
    fetcher.cookie_health_status() reports "degraded"."""
    failures = fetcher.get_consecutive_auth_failures()
    status = fetcher.cookie_health_status()

    if status != "degraded":
        return {"cookie_health": status, "alert_sent": False}
    if _already_alerted_today():
        return {"cookie_health": status, "alert_sent": False, "reason": "already alerted today"}

    message = build_alert_message(failures)
    notion_result = send_notion_alert(message)
    ntfy_sent = send_ntfy_alert(message)
    _mark_alerted_today()
    return {
        "cookie_health": status,
        "alert_sent": True,
        "notion_page": notion_result,
        "ntfy_sent": ntfy_sent,
    }
