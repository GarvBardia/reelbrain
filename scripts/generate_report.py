"""Generate REPORT.md — a comprehensive snapshot of everything in the Saves DB,
grouped by topic, with one-line summaries, value scores, and resource status.

LOCAL-ONLY (reads Notion live). Run: python scripts/generate_report.py
Writes REPORT.md in the repo root.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

# Statuses whose rows are still placeholders / not real extractions.
PLACEHOLDER_TITLE = "No caption or transcript available."


def _oneline(text: str, max_len: int = 110) -> str:
    """Collapse whitespace/newlines (raw-caption titles are full of them) and
    truncate, so each report row stays a single readable line."""
    out = " ".join((text or "").split())
    return out if len(out) <= max_len else out[:max_len].rstrip() + "…"


def _row_fields(page: dict) -> dict:
    from app import notion_writer

    props = page.get("properties", {})
    base = notion_writer.extract_digest_fields(page)
    base["title"] = _oneline(base["title"])
    base["gate_resource"] = (props.get("Gate resource") or {}).get("url") or ""
    base["gate_keyword"] = notion_writer._rt_text((props.get("Gate keyword") or {}).get("rich_text")) or ""
    return base


def collect_rows() -> list[dict]:
    from app import notion_writer

    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    rows = [_row_fields(p) for p in pages]
    return [r for r in rows if r["shortcode"]]


def _resource_state(row: dict) -> str:
    """attached / pending-DM / n-a."""
    if row["gate_resource"]:
        return "attached"
    if row["status_label"] == "⏳ Awaiting DM" or row["gate_keyword"]:
        return "pending"
    return "n/a"


def build_markdown(rows: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total = len(rows)
    placeholders = [r for r in rows if r["title"] == PLACEHOLDER_TITLE]
    real = [r for r in rows if r["title"] != PLACEHOLDER_TITLE]
    attached = [r for r in rows if _resource_state(r) == "attached"]
    pending = [r for r in rows if _resource_state(r) == "pending"]

    # priority + status tallies
    prio = defaultdict(int)
    status = defaultdict(int)
    for r in rows:
        prio[r["priority"] or "(none)"] += 1
        status[r["status_label"] or "(none)"] += 1

    lines: list[str] = []
    lines.append("# ReelBrain — Master Report")
    lines.append("")
    lines.append(f"_Generated {now} from the live Notion Saves database._")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append(f"- **Total rows:** {total}")
    lines.append(f"- **Real AI extractions:** {len(real)}  ·  **Placeholders (photo/carousel, no caption yet):** {len(placeholders)}")
    lines.append(f"- **Resources attached:** {len(attached)}  ·  **Resources pending (Awaiting DM / gated):** {len(pending)}")
    lines.append(f"- **Priority:** " + ", ".join(f"{k} {v}" for k, v in sorted(prio.items())))
    lines.append(f"- **Status:** " + ", ".join(f"{k} {v}" for k, v in sorted(status.items(), key=lambda kv: -kv[1])))
    lines.append("")

    # --- grouped by topic ---
    by_topic: dict[str, list[dict]] = defaultdict(list)
    untagged: list[dict] = []
    for r in rows:
        topics = [t for t in r["topics"] if t != "near-duplicate"]
        if not topics:
            untagged.append(r)
        for t in topics:
            by_topic[t].append(r)

    lines.append("## By topic")
    lines.append("")
    lines.append("_A reel appears under each of its topics. `[R]` = resource attached, "
                 "`[P]` = resource pending, value score in parens._")
    lines.append("")
    for topic in sorted(by_topic, key=lambda t: (-len(by_topic[t]), t)):
        rs = sorted(by_topic[topic], key=lambda r: -(int(r["value_score"]) if str(r["value_score"]).isdigit() else 0))
        lines.append(f"### {topic} ({len(rs)})")
        for r in rs:
            state = _resource_state(r)
            tag = "[R]" if state == "attached" else "[P]" if state == "pending" else "   "
            vs = r["value_score"] or "-"
            title = r["title"] or r["shortcode"]
            lines.append(f"- {tag} ({vs}) {title}  ·  `{r['shortcode']}`")
        lines.append("")

    if untagged:
        lines.append(f"### (no topics) ({len(untagged)})")
        for r in sorted(untagged, key=lambda r: r["shortcode"]):
            state = _resource_state(r)
            tag = "[R]" if state == "attached" else "[P]" if state == "pending" else "   "
            lines.append(f"- {tag} ({r['value_score'] or '-'}) {r['title'] or r['shortcode']}  ·  `{r['shortcode']}`")
        lines.append("")

    # --- resources pending list (actionable) ---
    lines.append("## Resources still pending (do the DM, then /attach the link)")
    lines.append("")
    if pending:
        for r in sorted(pending, key=lambda r: r["gate_keyword"] or ""):
            kw = r["gate_keyword"] or "?"
            lines.append(f"- comment **{kw}** — {r['title'] or r['shortcode']}  ·  `{r['shortcode']}`")
    else:
        lines.append("_None._")
    lines.append("")

    return "\n".join(lines) + "\n"


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    rows = collect_rows()
    md = build_markdown(rows)
    out = Path(__file__).resolve().parent.parent / "REPORT.md"
    out.write_text(md, encoding="utf-8")
    print(f"wrote {out} — {len(rows)} rows")


if __name__ == "__main__":
    main()
