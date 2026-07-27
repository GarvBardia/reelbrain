"""Weekly spot-check of AUTO-ATTACHED resources (agent E1).

Auto-attaching is the one place the system commits a write without asking, so
it needs a standing review habit rather than blind trust. This reads the
durable "🔍 Attach Audit Log" Notion page and prints every auto_attach from the
last N days with its confidence and scores, plus the row it landed on, so a
wrong one is obvious at a glance.

Read-only — never changes anything. If an entry looks wrong, fix that row in
Notion directly (clear its Gate resource and re-run /attach).

Usage:
    python scripts/review_auto_attaches.py             # last 7 days
    python scripts/review_auto_attaches.py --days 30
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

AUDIT_LOG_TITLE = "🔍 Attach Audit Log"
DEFAULT_DAYS = 7

# attach_audit.format_entry writes:  [<ts> UTC] | outcome=... | key=value | ...
_TS_RE = re.compile(r"\[([\d\-]+ [\d:]+) UTC\]")
_FIELD_RE = re.compile(r"(\w+)=('[^']*'|[^|]+)")


def parse_entry(line: str) -> dict:
    """One audit line -> {"timestamp", "outcome", ...}. Unparseable lines
    return {} so a malformed entry never breaks the review."""
    ts_match = _TS_RE.search(line)
    if not ts_match:
        return {}
    try:
        timestamp = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return {}
    fields = {k: v.strip().strip("'") for k, v in _FIELD_RE.findall(line)}
    fields["timestamp"] = timestamp
    return fields


def fetch_audit_lines(parent_page_id: str) -> list[str]:
    from app import notion_writer

    client = notion_writer._client()
    page_id = notion_writer.find_child_page_by_title(client, parent_page_id, AUDIT_LOG_TITLE)
    if not page_id:
        return []
    lines, cursor = [], None
    while True:
        kwargs = {"block_id": page_id}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.blocks.children.list(**kwargs)
        for block in resp.get("results", []):
            if block.get("type") == "paragraph":
                text = notion_writer._rt_text(block["paragraph"].get("rich_text"))
                if text:
                    lines.append(text)
        if not resp.get("has_more"):
            return lines
        cursor = resp.get("next_cursor")


def recent_auto_attaches(lines: list[str], days: int, now: datetime | None = None) -> list[dict]:
    """auto_attached entries newer than `days`, newest first."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    entries = []
    for line in lines:
        parsed = parse_entry(line)
        if not parsed or parsed.get("outcome") != "auto_attached":
            continue
        if parsed["timestamp"] < cutoff:
            continue
        entries.append(parsed)
    return sorted(entries, key=lambda e: e["timestamp"], reverse=True)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    args = parser.parse_args()

    parent = os.environ.get("NOTION_PARENT_PAGE_ID", "").strip()
    if not parent:
        sys.exit("NOTION_PARENT_PAGE_ID not set")

    lines = fetch_audit_lines(parent)
    entries = recent_auto_attaches(lines, args.days)

    print(f"{len(entries)} auto-attach(es) in the last {args.days} day(s) "
          f"(of {len(lines)} audit entries total)\n")
    if not entries:
        print("nothing to review.")
        return

    from app import notion_writer

    for e in entries:
        shortcode = e.get("shortcode", "?")
        print(f"  {e['timestamp']:%Y-%m-%d %H:%M}  {shortcode}")
        print(f"      {e.get('detail', '(no detail)')}")
        print(f"      resource: {e.get('resource_url', '?')[:100]}")
        try:
            page = notion_writer.find_page_by_shortcode(shortcode)
            if page:
                fields = notion_writer.extract_saves_fields(page)
                print(f"      landed on: {fields['title'][:80]}")
        except Exception:  # noqa: BLE001 - review must not fail on one lookup
            print("      landed on: (lookup failed)")
        print()

    print("If any of these look wrong: clear that row's Gate resource in Notion "
          "and re-run /attach for the resource.")


if __name__ == "__main__":
    main()
