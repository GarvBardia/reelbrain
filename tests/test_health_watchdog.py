"""scripts/health_watchdog.py — the six pure checks and the one-push decision.
Fully mocked: no Notion, no network, no clock."""
from datetime import datetime, timezone

from scripts import health_watchdog as hw


# --- 1. /health --------------------------------------------------------------------

def test_health_ok_when_cookie_ok_and_vec_true():
    c = hw.check_health({"cookie_health": "ok", "sqlite_vec": True})
    assert c.ok


def test_health_fails_on_degraded_cookies():
    c = hw.check_health({"cookie_health": "degraded", "sqlite_vec": True})
    assert not c.ok and "cookie_health" in c.detail


def test_health_fails_on_dead_sqlite_vec():
    c = hw.check_health({"cookie_health": "ok", "sqlite_vec": False})
    assert not c.ok and "sqlite_vec" in c.detail


def test_health_fails_when_unreachable():
    assert not hw.check_health(None).ok


# --- 2. reflection freshness -------------------------------------------------------

_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def test_reflection_fresh_within_26h():
    assert hw.check_reflection_fresh("2026-07-28T00:00:00.000Z", _NOW).ok  # 12h


def test_reflection_stale_past_26h():
    c = hw.check_reflection_fresh("2026-07-26T00:00:00.000Z", _NOW)  # 60h
    assert not c.ok


def test_reflection_missing_page_fails():
    assert not hw.check_reflection_fresh(None, _NOW).ok


# --- 3. counts ---------------------------------------------------------------------

def test_counts_match():
    assert hw.check_counts(174, 174).ok


def test_counts_drift_reported():
    c = hw.check_counts(174, 170)
    assert not c.ok and "+4" in c.detail


# --- 4. workflows ------------------------------------------------------------------

def test_workflows_all_success():
    assert hw.check_workflows({w: "success" for w in hw.WORKFLOWS}).ok


def test_workflows_one_failure():
    conclusions = {w: "success" for w in hw.WORKFLOWS}
    conclusions["nightly.yml"] = "failure"
    c = hw.check_workflows(conclusions)
    assert not c.ok and "nightly.yml=failure" in c.detail


def test_workflows_unknown_conclusion_counts_as_failure():
    conclusions = {w: "success" for w in hw.WORKFLOWS}
    conclusions["keepalive.yml"] = None
    assert not hw.check_workflows(conclusions).ok


def test_workflows_skipped_without_token_is_healthy_with_note():
    # None-for-the-whole-check = no GITHUB_TOKEN; a config gap must not push a
    # daily false alarm.
    c = hw.check_workflows(None)
    assert c.ok and "GITHUB_TOKEN" in c.detail


# --- 5. quota-stuck ----------------------------------------------------------------

def _line(used, budget=20, stopped=True):
    tail = ", STOPPED on a 429." if stopped else "."
    return f"[2026-07-28 00:00 UTC] live pass. Ran: x. Quota: {used}/{budget} calls used{tail}"


def test_quota_not_stuck_when_making_progress():
    # exhausted but used>0 every day = productive backfill, healthy
    lines = [_line(20), _line(20), _line(20)]
    assert hw.check_quota_not_stuck(lines).ok


def test_quota_stuck_after_three_zero_progress_429s():
    lines = [_line(0), _line(0), _line(0)]
    c = hw.check_quota_not_stuck(lines)
    assert not c.ok and "0 progress" in c.detail


def test_quota_streak_broken_by_a_recent_productive_run():
    # most-recent run productive -> streak resets, healthy even with old zeros
    lines = [_line(0), _line(0), _line(20)]
    assert hw.check_quota_not_stuck(lines).ok


def test_quota_healthy_on_empty_log():
    assert hw.check_quota_not_stuck([]).ok


# --- 6. empty topics ---------------------------------------------------------------

def test_no_empty_topics_is_healthy():
    assert hw.check_no_empty_topics([]).ok


def test_empty_topics_regression_flagged():
    c = hw.check_no_empty_topics(["AAA", "BBB"])
    assert not c.ok and "2 row(s)" in c.detail


# --- decision + message ------------------------------------------------------------

def test_summarize_healthy_when_all_ok():
    checks = [hw.Check("a", True, ""), hw.Check("b", True, "")]
    assert hw.summarize(checks)["healthy"]


def test_summarize_unhealthy_lists_failures():
    checks = [hw.Check("a", True, ""), hw.Check("b", False, "boom")]
    result = hw.summarize(checks)
    assert not result["healthy"]
    assert [c.name for c in result["failures"]] == ["b"]


def test_failure_message_lists_every_failure():
    msg = hw.build_failure_message([hw.Check("health", False, "cookie_health='degraded'"),
                                    hw.Check("counts", False, "Notion 5 vs vault 4")])
    assert "2 check(s) FAILED" in msg
    assert "health: cookie_health='degraded'" in msg
    assert "counts: Notion 5 vs vault 4" in msg
