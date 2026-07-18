"""Read-only audit: lists every Saves row's Gate resource URL next to its Title
and Topics so a human can eyeball whether the attached DM resource actually
matches what the row is about. Also runs a few cheap automated consistency
checks (comment_gate vs gate_keyword, gate_resource vs status).

This NEVER writes anything back to Notion — report only. Requested after BUG 2
surfaced two rows where Gate keyword and Comment gate disagreed; this script
is how you (not Claude, since a live audit needs real Notion credentials this
session deliberately avoided) can check the rest of the database yourself.

Usage:
    python scripts/audit_gate_resources.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app import notion_writer


def _rt(props: dict, name: str) -> str:
    return notion_writer._rt_text((props.get(name) or {}).get("rich_text"))


def _select(props: dict, name: str) -> str:
    return ((props.get(name) or {}).get("select") or {}).get("name", "")


def _multi_select(props: dict, name: str) -> list[str]:
    return [o["name"] for o in (props.get(name) or {}).get("multi_select", [])]


def _checkbox(props: dict, name: str) -> bool:
    return bool((props.get(name) or {}).get("checkbox"))


def _url(props: dict, name: str) -> str:
    return (props.get(name) or {}).get("url") or ""


def _title(props: dict) -> str:
    return notion_writer._rt_text((props.get("Title") or {}).get("title"))


def fetch_all_saves_rows() -> list[dict]:
    """Every Saves page, fully paginated. Returns extracted field dicts, not
    raw Notion pages -- this script only ever reads."""
    client = notion_writer._client()
    ds_id = notion_writer._resolve_data_source_id(client, notion_writer.NOTION_DB_ID)

    rows = []
    cursor = None
    while True:
        kwargs = {"data_source_id": ds_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = client.data_sources.query(**kwargs)
        for page in resp["results"]:
            props = page.get("properties", {})
            rows.append({
                "shortcode": _rt(props, "Shortcode"),
                "title": _title(props),
                "topics": _multi_select(props, "Topics"),
                "status": _select(props, "Status"),
                "comment_gate": _checkbox(props, "Comment gate"),
                "gate_keyword": _rt(props, "Gate keyword"),
                "gate_resource": _url(props, "Gate resource"),
                "reel_url": _url(props, "Reel URL"),
                "page_url": page.get("url", ""),
            })
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return rows


def print_report(rows: list[dict]) -> None:
    print(f"Fetched {len(rows)} Saves rows.\n")

    # --- automated consistency checks (no judgment required) -----------------
    keyword_without_gate = [r for r in rows if r["gate_keyword"] and not r["comment_gate"]]
    resource_but_still_awaiting = [
        r for r in rows if r["gate_resource"] and r["status"] == "⏳ Awaiting DM"
    ]
    gated_without_keyword = [
        r for r in rows if r["comment_gate"] and not r["gate_keyword"]
    ]

    print("=== Automated checks ===")
    print(f"gate_keyword set but Comment gate is False/unchecked: {len(keyword_without_gate)}")
    for r in keyword_without_gate:
        print(f"  - {r['shortcode']!r:20} keyword={r['gate_keyword']!r:20} title={r['title']!r}")

    print(f"\nGate resource set but Status is still Awaiting DM: {len(resource_but_still_awaiting)}")
    for r in resource_but_still_awaiting:
        print(f"  - {r['shortcode']!r:20} resource={r['gate_resource']!r}")

    print(f"\nComment gate checked but no Gate keyword recorded: {len(gated_without_keyword)}")
    for r in gated_without_keyword:
        print(f"  - {r['shortcode']!r:20} title={r['title']!r}")

    # --- human-judgment table: every row with a Gate resource attached -------
    with_resource = [r for r in rows if r["gate_resource"]]
    print(f"\n=== Rows with a Gate resource attached ({len(with_resource)}) ===")
    print("Eyeball each 'resource' URL against the title/topics — does the")
    print("linked resource plausibly match what this row is about?\n")
    for r in with_resource:
        print(f"shortcode : {r['shortcode']}")
        print(f"title     : {r['title']}")
        print(f"topics    : {', '.join(r['topics'])}")
        print(f"keyword   : {r['gate_keyword']}")
        print(f"resource  : {r['gate_resource']}")
        print(f"notion    : {r['page_url']}")
        print("-" * 60)


if __name__ == "__main__":
    print_report(fetch_all_saves_rows())
