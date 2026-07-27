"""Daily health watchdog — silence means healthy.

Runs six independent checks and sends EXACTLY ONE ntfy push if (and only if)
something failed, listing every failure. A healthy day makes no noise at all,
so a push always means "look now". Meant to run nightly alongside daily_runner
(see nightly_autonomous.bat).

The six checks (spec Phase J4):
  1. GET /health — cookie_health ok AND sqlite_vec true.
  2. The "🌙 Daily Reflection" Notion page was edited within the last 26 hours
     (the nightly digest should refresh it every ~24h; 26h allows slack).
  3. Vault reel-note count == Notion Saves row count.
  4. All four GitHub Actions workflows' most recent run succeeded.
  5. daily_runner isn't STUCK on quota: not 3+ consecutive runs that hit a 429
     while making ZERO progress. (Deliberately "exhausted AND used==0" — during
     the active backfill, spending the full budget every day is SUCCESS, not a
     fault; only a run that hits the wall having done nothing signals a real
     stuck/broken state worth waking someone for.)
  6. Zero rows with empty Topics — the Phase H structural guarantee. Always
     true if nothing regressed; this catches the regression if not.

Every check is a pure function taking injected data, so the whole decision tree
is testable without Notion, the network, or the clock. main() wires the real
data sources.

Usage:
    python scripts/health_watchdog.py            # run checks, push only on failure
    python scripts/health_watchdog.py --dry-run  # print result, never push
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("reelbrain.health_watchdog")

WORKFLOWS = ["nightly.yml", "keepalive.yml", "daily-digest.yml", "weekly-digest.yml"]
REFLECTION_MAX_AGE_HOURS = 26
QUOTA_STUCK_DAYS = 3


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


# --- the six checks (pure) --------------------------------------------------------


def check_health(payload: Optional[dict]) -> Check:
    if payload is None:
        return Check("health", False, "GET /health failed or unreachable")
    problems = []
    if payload.get("cookie_health") != "ok":
        problems.append(f"cookie_health={payload.get('cookie_health')!r}")
    if payload.get("sqlite_vec") is not True:
        problems.append(f"sqlite_vec={payload.get('sqlite_vec')!r}")
    return Check("health", not problems, "; ".join(problems) or "cookie_health ok, sqlite_vec true")


def check_reflection_fresh(last_edited_iso: Optional[str], now: datetime) -> Check:
    if not last_edited_iso:
        return Check("reflection", False, "Daily Reflection page not found")
    edited = datetime.fromisoformat(last_edited_iso.replace("Z", "+00:00"))
    age = now - edited
    ok = age <= timedelta(hours=REFLECTION_MAX_AGE_HOURS)
    return Check("reflection", ok,
                 f"last edited {age.total_seconds() / 3600:.1f}h ago "
                 f"(limit {REFLECTION_MAX_AGE_HOURS}h)")


def check_counts(notion_rows: int, vault_notes: int) -> Check:
    ok = notion_rows == vault_notes
    return Check("counts", ok, f"Notion {notion_rows} vs vault {vault_notes}"
                 + ("" if ok else f" (drift {notion_rows - vault_notes:+d})"))


def check_workflows(conclusions: Optional[dict[str, Optional[str]]]) -> Check:
    """conclusions: {workflow_file: 'success'|'failure'|None}, or None for the
    whole check when it was SKIPPED (no GITHUB_TOKEN — the private repo can't be
    read unauthenticated). A skip is healthy-with-a-note, never a daily false
    alarm; but with a token present, a per-workflow None (unreadable run) IS a
    failure so a broken check surfaces rather than hides."""
    if conclusions is None:
        return Check("workflows", True, "skipped — set GITHUB_TOKEN to enable")
    bad = {wf: c for wf, c in conclusions.items() if c != "success"}
    return Check("workflows", not bad,
                 "all 4 last runs succeeded" if not bad
                 else "; ".join(f"{wf}={c}" for wf, c in bad.items()))


def check_quota_not_stuck(log_lines: list[str], days: int = QUOTA_STUCK_DAYS) -> Check:
    """Stuck == the last `days` daily_runner entries ALL hit a 429 with 0 calls
    used. Productive exhaustion (used>0) is healthy and does not count."""
    stuck_streak = 0
    for line in reversed([ln for ln in log_lines if ln.strip()]):
        hit_429 = "STOPPED on a 429" in line
        # "Quota: N/M calls used" -> zero progress iff N == 0
        used_zero = False
        marker = "Quota: "
        if marker in line:
            frac = line.split(marker, 1)[1].split(" calls", 1)[0]  # "N/M"
            used_zero = frac.strip().startswith("0/")
        if hit_429 and used_zero:
            stuck_streak += 1
            if stuck_streak >= days:
                return Check("quota", False,
                             f"{stuck_streak}+ consecutive runs hit a 429 with 0 progress")
        else:
            break  # streak broken by the most recent healthy/productive run
    return Check("quota", True, f"not stuck ({stuck_streak} zero-progress run(s) at tail)")


def check_no_empty_topics(empty_topic_shortcodes: list[str]) -> Check:
    ok = not empty_topic_shortcodes
    preview = ", ".join(empty_topic_shortcodes[:5])
    return Check("empty_topics", ok,
                 "zero rows with empty Topics" if ok
                 else f"{len(empty_topic_shortcodes)} row(s) with empty Topics: {preview}")


def summarize(checks: list[Check]) -> dict:
    failures = [c for c in checks if not c.ok]
    return {"healthy": not failures, "checks": checks, "failures": failures}


def build_failure_message(failures: list[Check]) -> str:
    lines = [f"ReelBrain health: {len(failures)} check(s) FAILED"]
    lines += [f"- {c.name}: {c.detail}" for c in failures]
    return "\n".join(lines)


# --- real data sources ------------------------------------------------------------


def _fetch_health(base_url: str) -> Optional[dict]:
    try:
        import httpx

        resp = httpx.get(f"{base_url.rstrip('/')}/health", timeout=15.0)
        resp.raise_for_status()
        return resp.json()
    except Exception:  # noqa: BLE001 - unreachable IS the failure signal
        logger.warning("health fetch failed", exc_info=True)
        return None


def _reflection_last_edited() -> Optional[str]:
    import os

    from app import notion_writer
    from app.digest import DAILY_DIGEST_TITLE

    parent = os.environ.get("NOTION_PARENT_PAGE_ID", "").strip()
    if not parent:
        return None
    client = notion_writer._client()
    page_id = notion_writer.find_child_page_by_title(client, parent, DAILY_DIGEST_TITLE)
    if not page_id:
        return None
    return client.pages.retrieve(page_id=page_id).get("last_edited_time")


def _workflow_conclusions(repo: str, token: str) -> Optional[dict[str, Optional[str]]]:
    """Last-run conclusion per workflow via the GitHub REST API. Returns None
    (check skipped) when no token is set — the repo is private, so an
    unauthenticated read 404s and there's nothing to verify. A fine-grained PAT
    with Actions:read (repo-scoped) is enough."""
    if not token:
        return None
    import httpx

    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json"}
    out: dict[str, Optional[str]] = {}
    for wf in WORKFLOWS:
        try:
            resp = httpx.get(
                f"https://api.github.com/repos/{repo}/actions/workflows/{wf}/runs",
                params={"per_page": 1}, headers=headers, timeout=30.0,
            )
            resp.raise_for_status()
            runs = resp.json().get("workflow_runs", [])
            out[wf] = runs[0].get("conclusion") if runs else None
        except Exception:  # noqa: BLE001 - a per-workflow None is itself a failure signal
            logger.warning("workflow status fetch failed for %s", wf, exc_info=True)
            out[wf] = None
    return out


def _empty_topic_shortcodes() -> list[str]:
    from app import notion_writer

    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    empty = []
    for page in pages:
        fields = notion_writer.extract_digest_fields(page)
        if fields["shortcode"] and not fields["topics"]:
            empty.append(fields["shortcode"])
    return empty


def run(dry_run: bool = False, print_fn: Callable[[str], None] = print) -> dict:
    import os

    from app import notion_writer, obsidian_sync

    base_url = os.environ.get("REELBRAIN_URL", "https://reelbrain.onrender.com")
    repo = os.environ.get("GITHUB_REPO", "GarvBardia/reelbrain")
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    now = datetime.now(timezone.utc)

    pages = notion_writer.find_saves_pages_since("1970-01-01T00:00:00")
    notion_rows = sum(1 for p in pages if notion_writer.extract_digest_fields(p)["shortcode"])
    vault_notes = len(obsidian_sync.existing_notes_by_shortcode(Path(obsidian_sync.VAULT_PATH)))

    log_path = Path("daily_runner.log")
    log_lines = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []

    checks = [
        check_health(_fetch_health(base_url)),
        check_reflection_fresh(_reflection_last_edited(), now),
        check_counts(notion_rows, vault_notes),
        check_workflows(_workflow_conclusions(repo, gh_token)),
        check_quota_not_stuck(log_lines),
        check_no_empty_topics(_empty_topic_shortcodes()),
    ]
    result = summarize(checks)

    for c in checks:
        print_fn(f"[{'OK ' if c.ok else 'FAIL'}] {c.name}: {c.detail}")

    if not result["healthy"] and not dry_run:
        from app import alerts

        sent = alerts.send_push(build_failure_message(result["failures"]),
                                title="ReelBrain health check", tags="rotating_light")
        print_fn(f"Pushed failure alert: {sent}")

    return result


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    logging.basicConfig(level=logging.WARNING)  # quiet: only our prints + real warnings

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="run checks but never push")
    args = parser.parse_args()

    result = run(dry_run=args.dry_run)
    print("\n" + json.dumps(
        {"healthy": result["healthy"],
         "failures": [c.name for c in result["failures"]]}, indent=2))
    sys.exit(0 if result["healthy"] else 1)


if __name__ == "__main__":
    main()
