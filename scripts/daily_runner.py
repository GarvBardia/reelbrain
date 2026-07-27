"""The autonomy core: spend each day's Gemini quota across pending work, in
priority order, then stop cleanly and resume tomorrow where it left off.

WHY THIS EXISTS: every remaining backlog item is quota-bound and, until now,
needed a human to run a script. The Gemini free tier is ~20 requests/day/model,
and it has blocked work on most days of this project. This turns "run six
scripts by hand and remember which one stopped where" into one scheduled pass.

PRIORITY ORDER (and why):
  1. backfill_named_entities  — HIGHEST. Both the taxonomy work and the
     /attach accuracy remeasurement are blocked on it, and it's the single
     most specific matching signal the system has. Everything else can wait.
  2. recover_placeholders     — photo/carousel + Failed-retry rows. Recovers
     content that does not exist anywhere else yet.
  3. enforce_topics           — FREE (zero Gemini calls; derives tags from
     data already on the row). Runs EVERY day regardless of quota state,
     including after the budget is gone. Placed after step 1 deliberately:
     its fallback derives tags FROM named_entities, so running it later in
     the order produces better tags than running it first (learned live --
     30 rows could only reach "uncategorized" because entities weren't
     backfilled yet).
  4. backfill_suggested_action
  5. backfill_plain_summary
  6. ingest_resources         — last: the resources are already attached and
     readable at any time; this only enriches the vault copy.

BUDGET MODEL — two independent mechanisms, both needed:
  * The BUDGET (DAILY_GEMINI_BUDGET, default 20) is a PLANNING device. It
    decides how work is *spread* across steps by slicing each step's pending
    rows to what's left. Without it, step 1 would consume the entire day
    every day and steps 2-6 would never run at all.
  * The 429 is the HARD stop. The budget is an estimate -- a recovery row can
    cost several calls (extraction + one research call per named entity) --
    so the real limit is whatever Google enforces. A _QuotaWatcher on the
    `reelbrain.gemini` logger catches it for EVERY step uniformly, including
    ingest_resources, which (unlike the other five) does not self-report
    quota_stopped in its result.
  Cost estimates per step are declared, not assumed: ROW_COST below.

Everything is resumable because each underlying script owns its own progress
file; this runner only decides who runs and how much.

Usage:
    python scripts/daily_runner.py --dry-run
    python scripts/daily_runner.py
    python scripts/daily_runner.py --budget 40      # if you raise the tier
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("reelbrain.daily_runner")

LOG_FILE = "daily_runner.log"
# Observed free-tier limit (generate_content_free_tier_requests = 20/day/model,
# seen repeatedly in live 429 bodies). Override with --budget or the env var if
# the account tier changes.
DAILY_GEMINI_BUDGET = int(os.environ.get("DAILY_GEMINI_BUDGET", "20"))
QUOTA_MARKERS = ("429", "RESOURCE_EXHAUSTED")

# Estimated Gemini calls PER ROW, used to slice each step's work to the
# remaining budget. Deliberately explicit rather than assuming 1 everywhere:
# a recovery row runs an extraction AND up to MAX_RESEARCH_ENTITIES research
# calls, so treating it as 1 would blow through the day's quota in two rows.
ROW_COST = {
    "named_entities": 1,
    "recover_placeholders": 3,
    "suggested_action": 1,
    "plain_summary": 1,
    "ingest_resources": 1,
}


class _QuotaWatcher(logging.Handler):
    """Every underlying script degrades rather than raising on a 429, and
    ingest_resources does not report quota_stopped at all. But they ALL log the
    exception through the `reelbrain.gemini` logger, so watching that one
    logger gives the runner a uniform quota signal across every step."""

    def __init__(self) -> None:
        super().__init__()
        self.quota_hit = False

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            text += " " + str(record.exc_info[1])
        if any(marker in text for marker in QUOTA_MARKERS):
            self.quota_hit = True


@dataclass
class Step:
    """One unit of daily work.

    free=True means it makes ZERO Gemini calls, so it is never budgeted and
    runs even after quota is exhausted.
    """
    name: str
    find_pending: Callable[[], list]
    run: Callable[[list, bool], dict]
    free: bool = False
    cost_per_row: int = 1
    # filled in during the run
    pending_before: int = 0
    processed: int = 0
    result: dict = field(default_factory=dict)
    ran: bool = False
    note: str = ""


def _count(value) -> int:
    """Result fields are not uniformly typed across the six scripts: most
    report counts as ints, but ingest_resources reports "written"/"degraded"/
    "unreadable" as LISTS of shortcodes. Caught by the first live dry-run --
    the arithmetic below assumed ints and crashed on a list."""
    if isinstance(value, (list, tuple, set)):
        return len(value)
    if isinstance(value, int):
        return value
    return 0


# Result keys that each represent one row the step actually spent a Gemini
# call on. Deliberately EXCLUDES ingest_resources' "unreadable" (the fetch
# failed before any model call) and every step's "skipped" (never touched).
_SPENT_KEYS = ("written", "recovered", "none_found", "errors", "degraded")


def _calls_used(step: Step, result: dict) -> int:
    """Best-effort count of quota actually consumed by a step. Uses rows the
    step reports having ACTED on (not rows offered), times the declared
    per-row cost."""
    if step.free:
        return 0
    acted = sum(_count(result.get(key, 0)) for key in _SPENT_KEYS)
    return acted * step.cost_per_row


def run_day(
    steps: list[Step],
    budget: int = DAILY_GEMINI_BUDGET,
    dry_run: bool = False,
    print_fn: Callable[[str], None] = print,
) -> dict:
    """Execute the day's pass. Returns a structured summary for the log.

    Pure orchestration: every step is injected, so this is fully testable
    without touching Notion, Gemini, or the clock.
    """
    watcher = _QuotaWatcher()
    gemini_logger = logging.getLogger("reelbrain.gemini")
    gemini_logger.addHandler(watcher)

    remaining = budget
    quota_stopped = False
    try:
        for step in steps:
            try:
                pending = step.find_pending()
            except Exception as exc:  # noqa: BLE001 - one step's lookup must not sink the day
                step.note = f"could not list pending work: {exc}"
                logger.warning("daily_runner: %s pending lookup failed", step.name, exc_info=True)
                continue
            step.pending_before = len(pending)

            if not pending:
                step.note = "nothing pending"
                continue

            if step.free:
                # Never budgeted, and deliberately NOT skipped when quota is
                # gone -- this is the one thing that still makes progress on
                # an exhausted day.
                step.result = step.run(pending, dry_run)
                step.processed = len(pending)
                step.ran = True
                continue

            if quota_stopped or watcher.quota_hit:
                step.note = "skipped — quota already exhausted this run"
                continue
            if remaining <= 0:
                step.note = "skipped — daily budget spent by higher-priority work"
                continue

            allowance = max(1, remaining // step.cost_per_row)
            slice_ = pending[:allowance]
            step.result = step.run(slice_, dry_run)
            step.processed = len(slice_)
            step.ran = True

            if not dry_run:
                spent = _calls_used(step, step.result)
                remaining -= spent

            if step.result.get("quota_stopped") or watcher.quota_hit:
                quota_stopped = True
                step.note = "stopped on quota"
    finally:
        gemini_logger.removeHandler(watcher)

    return {
        "budget": budget,
        "remaining": max(remaining, 0),
        "used": budget - max(remaining, 0),
        "quota_stopped": quota_stopped or watcher.quota_hit,
        "steps": steps,
        "dry_run": dry_run,
    }


def format_log_paragraph(summary: dict, named_entities_remaining: Optional[int],
                         model: str = "?") -> str:
    """ONE clear paragraph: what ran, quota used, what's still pending, and the
    named_entities countdown (the number the user actually wants to watch).

    Always names the Gemini model, and names it again on a 429 — so debugging a
    quota stop never requires opening the AI Studio dashboard to learn which
    model version actually hit its cap (the incident that motivated this)."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    mode = "DRY-RUN" if summary["dry_run"] else "live"

    ran_bits, pending_bits = [], []
    for step in summary["steps"]:
        if step.ran:
            done = sum(_count(step.result.get(key, 0))
                       for key in ("written", "recovered", "fixed", "none_found"))
            ran_bits.append(f"{step.name} ({done}/{step.pending_before})")
        elif step.note:
            pending_bits.append(f"{step.name}: {step.pending_before} pending — {step.note}")

    parts = [
        f"[{stamp}] {mode} pass (model {model}). "
        f"Ran: {', '.join(ran_bits) if ran_bits else 'nothing (no pending work)'}. "
        f"Quota: {summary['used']}/{summary['budget']} calls used"
        + (f", STOPPED on a 429 (model {model} exhausted)" if summary["quota_stopped"] else "")
        + "."
    ]
    if pending_bits:
        parts.append("Still pending — " + "; ".join(pending_bits) + ".")

    if named_entities_remaining is None:
        parts.append("named_entities countdown: unavailable (lookup failed).")
    elif named_entities_remaining == 0:
        parts.append("named_entities backfill is COMPLETE — the accuracy "
                     "remeasurement and taxonomy work are now unblocked.")
    else:
        days = max(1, -(-named_entities_remaining // max(1, summary["budget"])))
        parts.append(f"named_entities countdown: {named_entities_remaining} rows left "
                     f"(~{days} more day(s) at {summary['budget']} calls/day).")
    return " ".join(parts)


def format_dry_run_plan(summary: dict) -> str:
    """A dry run's real job: show what WOULD be attempted and its estimated
    quota cost, WITHOUT spending any. Reports per-step pending row counts and
    estimated call cost, mirroring how run_day would slice the budget.

    Estimation only — the 429, not this arithmetic, is the real limit (a
    recovery row can cost several calls). Free steps are shown at 0 cost."""
    budget = summary["budget"]
    remaining = budget
    lines = [f"DRY-RUN plan (estimates only; no Gemini calls, no writes) — "
             f"budget {budget} calls:"]
    for step in summary["steps"]:
        pending = step.pending_before
        if step.free:
            lines.append(f"  • {step.name}: {pending} pending, FREE "
                         f"(0 calls — runs regardless of budget)")
            continue
        if pending == 0:
            lines.append(f"  • {step.name}: nothing pending")
            continue
        if remaining <= 0:
            lines.append(f"  • {step.name}: {pending} pending, ~{pending * step.cost_per_row} "
                         f"calls to clear — SKIPPED (budget already spent above)")
            continue
        affordable = min(pending, max(1, remaining // step.cost_per_row))
        est = affordable * step.cost_per_row
        remaining -= est
        tail = "" if affordable == pending else f" (of {pending} pending; rest waits for tomorrow)"
        lines.append(f"  • {step.name}: would attempt {affordable} row(s) "
                     f"@ ~{step.cost_per_row} call(s) each = ~{est} calls{tail}")
    lines.append(f"  → estimated ~{budget - remaining}/{budget} calls would be spent this pass.")
    return "\n".join(lines)


def append_log(line: str, path: str = LOG_FILE) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# --- the real step wiring ---------------------------------------------------------


def build_steps() -> list[Step]:
    """The six steps in priority order, wired to the real scripts. Kept apart
    from run_day so the orchestration logic stays testable in isolation."""
    from app import notion_writer, obsidian_sync, store
    from scripts import (
        backfill_named_entities,
        backfill_plain_summary,
        backfill_suggested_action,
        enforce_topics,
        ingest_resources,
        recover_placeholders,
    )

    def _pages() -> list[dict]:
        return notion_writer.find_saves_pages_since("1970-01-01T00:00:00")

    return [
        Step(
            name="named_entities",
            cost_per_row=ROW_COST["named_entities"],
            find_pending=lambda: backfill_named_entities.find_rows_needing_entities(_pages()),
            run=lambda rows, dry: backfill_named_entities.run_backfill(
                rows, backfill_named_entities.DEFAULT_PROGRESS_FILE, dry_run=dry),
        ),
        Step(
            name="recover_placeholders",
            cost_per_row=ROW_COST["recover_placeholders"],
            find_pending=recover_placeholders.find_placeholder_rows,
            run=lambda rows, dry: recover_placeholders.run_worker(
                rows, recover_placeholders.DEFAULT_PROGRESS_FILE, dry_run=dry),
        ),
        Step(
            name="enforce_topics",
            free=True,
            find_pending=lambda: enforce_topics.find_topicless_rows(
                _pages(), include_upgradable=True),
            run=lambda rows, dry: enforce_topics.run_enforce(rows, dry_run=dry),
        ),
        Step(
            name="suggested_action",
            cost_per_row=ROW_COST["suggested_action"],
            find_pending=backfill_suggested_action.find_backfill_rows,
            run=lambda rows, dry: backfill_suggested_action.run_backfill(
                rows, backfill_suggested_action.DEFAULT_PROGRESS_FILE, dry_run=dry),
        ),
        Step(
            name="plain_summary",
            cost_per_row=ROW_COST["plain_summary"],
            find_pending=lambda: backfill_plain_summary.find_rows_needing_plain_summary(_pages()),
            run=lambda rows, dry: backfill_plain_summary.run_backfill(
                rows, backfill_plain_summary.DEFAULT_PROGRESS_FILE, dry_run=dry),
        ),
        Step(
            name="ingest_resources",
            cost_per_row=ROW_COST["ingest_resources"],
            find_pending=ingest_resources.find_gated_resources,
            run=lambda rows, dry: ingest_resources.run_ingest(
                rows, Path(obsidian_sync.VAULT_PATH),
                Path(ingest_resources.DEFAULT_PROGRESS_FILE), dry_run=dry,
                taxonomy=store.get_taxonomy(),
            ),
        ),
    ]


def count_named_entities_remaining() -> Optional[int]:
    from app import notion_writer
    from scripts import backfill_named_entities

    try:
        pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
        return len(backfill_named_entities.find_rows_needing_entities(pages))
    except Exception:  # noqa: BLE001 - the countdown is reporting, never load-bearing
        logger.warning("could not count remaining named_entities rows", exc_info=True)
        return None


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="list what each step WOULD do; makes no Gemini calls and no writes")
    parser.add_argument("--budget", type=int, default=None,
                        help="override the day's call budget; default is the TRUE "
                             "remaining quota for the configured model (gemini_quota)")
    parser.add_argument("--log-file", default=LOG_FILE)
    args = parser.parse_args()

    from app import gemini_pipe, gemini_quota

    model = gemini_pipe.GEMINI_MODEL
    # The real remaining budget for THIS model today, measured from persisted call
    # timestamps — not an assumed "20 fresh every run". A second run the same day
    # correctly sees a smaller budget; a model already spent elsewhere sees ~0.
    if args.budget is not None:
        budget = args.budget
    else:
        budget = gemini_quota.remaining_today(model, limit=DAILY_GEMINI_BUDGET)

    print(f"daily runner — model {model}, budget {budget} Gemini calls "
          f"(true remaining today)"
          f"{' (DRY-RUN)' if args.dry_run else ''}\n")

    summary = run_day(build_steps(), budget=budget, dry_run=args.dry_run)
    paragraph = format_log_paragraph(summary, count_named_entities_remaining(), model=model)

    print("\n" + "=" * 70)
    if args.dry_run:
        print(format_dry_run_plan(summary))
        print()
    print(paragraph)
    if not args.dry_run:
        append_log(paragraph, args.log_file)
        print(f"\n(appended to {args.log_file})")


if __name__ == "__main__":
    main()
