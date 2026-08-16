"""One command: how much work is pending, and what will it take to clear it.

Every backlog already knows its own pending count, but they were only ever
visible one script at a time, which made "how far from done are we" a research
project. This prints the whole picture plus an HONEST drain estimate.

The estimate deliberately reports calls, not days, as the primary number,
because "days" depends entirely on the account tier and that has been the single
most-misunderstood variable in this project (see PROGRESS.md 2026-07-31: a
billing block spent days looking like a 20/day rate limit). Days are shown as a
clearly-labelled scenario, not a fact.

Usage:
    python scripts/backlog_status.py
    python scripts/backlog_status.py --json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("reelbrain.backlog_status")

# Estimated Gemini calls per row, matching daily_runner.ROW_COST so the two
# never disagree about what a backlog costs. plain_summary is deliberately
# ABSENT (2026-08-16): it's LOCAL-routed now (Ollama, via app.llm_router), so
# it costs zero Gemini calls -- see LOCAL_TASKS below. suggested_action
# stayed on Gemini (Phase 4 quality comparison found local materially worse
# for this specific task -- see app.llm_router.TASK_PROVIDERS).
COSTS = {
    "named_entities": 1,
    "recover_placeholders": 3,   # extraction + research per entity
    "suggested_action": 1,
    "ingest_resources": 1,
}
# Text-only backlogs routed to the local Ollama provider instead of Gemini
# (PROGRESS.md 2026-08-16 permanent local-LLM fix for the free-tier quota
# bottleneck) -- real work, real wall-clock time, but zero Gemini calls, so
# they're reported separately from the Gemini-costed total below rather than
# inflating "how many Gemini calls to fully drain the backlog."
LOCAL_TASKS = ("plain_summary",)
FREE_TIER_RPD = 20


def collect() -> dict:
    from app import notion_writer, obsidian_sync
    from scripts import (backfill_named_entities, backfill_plain_summary,
                         backfill_suggested_action, enforce_topics,
                         ingest_resources, recover_placeholders)

    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    digests = [notion_writer.extract_digest_fields(p) for p in pages]
    digests = [d for d in digests if d["shortcode"]]

    def count(fn):
        try:
            return len(fn())
        except Exception:  # noqa: BLE001 - one broken lookup must not sink the report
            logger.warning("backlog lookup failed", exc_info=True)
            return None

    backlogs = {
        "named_entities": count(lambda: backfill_named_entities.find_rows_needing_entities(pages)),
        "recover_placeholders": count(recover_placeholders.find_placeholder_rows),
        "suggested_action": count(backfill_suggested_action.find_backfill_rows),
        "ingest_resources": count(ingest_resources.find_gated_resources),
    }
    local_backlogs = {
        "plain_summary": count(lambda: backfill_plain_summary.find_rows_needing_plain_summary(pages)),
    }
    free_backlog = count(lambda: enforce_topics.find_topicless_rows(pages, include_upgradable=True))

    total_calls = sum((n or 0) * COSTS[k] for k, n in backlogs.items())

    from collections import Counter
    marker_counts = Counter()
    for d in digests:
        topics = list(d["topics"])
        if len(topics) == 1 and topics[0] in ("uncategorized", "pending-extraction"):
            marker_counts[topics[0]] += 1

    vault_notes = len(obsidian_sync.existing_notes_by_shortcode(Path(obsidian_sync.VAULT_PATH)))
    return {
        "notion_rows": len(digests),
        "vault_notes": vault_notes,
        "count_drift": len(digests) - vault_notes,
        "backlogs": backlogs,
        "local_backlogs": local_backlogs,
        "enforce_topics_free": free_backlog,
        "total_gemini_calls": total_calls,
        "markers": dict(marker_counts),
    }


def format_report(data: dict) -> str:
    lines = [f"Notion rows: {data['notion_rows']}   vault notes: {data['vault_notes']}"
             f"   drift: {data['count_drift']:+d}", ""]
    lines.append("BACKLOG (Gemini)            PENDING   EST. CALLS")
    for name, n in data["backlogs"].items():
        if n is None:
            lines.append(f"  {name:24s}   ERROR")
            continue
        lines.append(f"  {name:24s} {n:5d}   {n * COSTS[name]:6d}")
    free = data["enforce_topics_free"]
    lines.append(f"  {'enforce_topics (FREE)':24s} {free if free is not None else 0:5d}        0")
    lines.append("")
    lines.append(f"TOTAL to fully drain (Gemini-costed work only): ~{data['total_gemini_calls']} Gemini calls")
    days = -(-data["total_gemini_calls"] // FREE_TIER_RPD) if data["total_gemini_calls"] else 0
    lines.append(f"  scenario A (free tier, {FREE_TIER_RPD}/day): ~{days} days")
    lines.append("  scenario B (paid tier): not rate-bound — the limit is spend, not days")
    lines.append("")
    lines.append("BACKLOG (LOCAL — Ollama, zero Gemini cost)   PENDING")
    for name, n in data.get("local_backlogs", {}).items():
        if n is None:
            lines.append(f"  {name:24s}   ERROR")
            continue
        lines.append(f"  {name:24s} {n:5d}   (wall-clock time only, see LOCAL_TIME_BUDGET_SECONDS)")
    if data["markers"]:
        lines.append("")
        lines.append("Fallback-marker rows: " + ", ".join(
            f"{k}={v}" for k, v in sorted(data["markers"].items())))
    return "\n".join(lines)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    data = collect()
    print(json.dumps(data, indent=2) if args.json else format_report(data))


if __name__ == "__main__":
    main()
