"""scripts/pipeline_health.py — the pure check/consolidation/alert-decision
logic. Fully mocked: no Notion, no network, no clock, no files, no
subprocess."""
from datetime import datetime, timezone

from scripts import pipeline_health as ph


# --- backend health ------------------------------------------------------------

def test_backend_pass_when_all_ok():
    c = ph.check_backend_health({"status": "ok", "cookie_health": "ok", "sqlite_vec": True})
    assert c.state == "pass"


def test_backend_fail_when_unreachable():
    c = ph.check_backend_health(None)
    assert c.state == "fail" and "unreachable" in c.detail


def test_backend_fail_on_degraded_cookies():
    c = ph.check_backend_health({"status": "ok", "cookie_health": "degraded", "sqlite_vec": True})
    assert c.state == "fail" and "cookie_health" in c.detail


# --- nightly freshness (the core check this script exists for) -----------------

def test_freshness_pass_when_recent():
    c = ph.check_nightly_freshness(log_age_hours=5.0)
    assert c.state == "pass"


def test_freshness_fail_when_stale():
    c = ph.check_nightly_freshness(log_age_hours=48.0)
    assert c.state == "fail"
    assert "48.0h ago" in c.detail


def test_freshness_fail_when_log_never_existed():
    c = ph.check_nightly_freshness(log_age_hours=None)
    assert c.state == "fail" and "has it ever run" in c.detail


def test_freshness_includes_scheduler_detail_when_given():
    c = ph.check_nightly_freshness(log_age_hours=48.0, scheduler_detail="scheduler: refused by policy (4320)")
    assert "4320" in c.detail


def test_freshness_respects_custom_max_age():
    # a run 20h ago is fine at the default 30h limit but stale at a 12h limit
    assert ph.check_nightly_freshness(20.0).state == "pass"
    assert ph.check_nightly_freshness(20.0, max_age_hours=12.0).state == "fail"


# --- ollama ----------------------------------------------------------------------

def test_ollama_pass_when_reachable():
    assert ph.check_ollama(True).state == "pass"


def test_ollama_degraded_not_fail_when_down():
    # local backfill degrading cleanly is a known, non-fatal shape -- doesn't
    # deserve the same severity as a genuinely broken backend/vault
    c = ph.check_ollama(False)
    assert c.state == "degraded"


# --- backlog trend (delta reused from daily_capture_report, not recomputed) ----

def test_backlog_trend_pass_first_day():
    c = ph.check_backlog_trend(total_backlog=131, delta=None)
    assert c.state == "pass" and "no prior day" in c.detail


def test_backlog_trend_degraded_when_grew():
    c = ph.check_backlog_trend(total_backlog=139, delta=6)
    assert c.state == "degraded" and "GREW by 6" in c.detail


def test_backlog_trend_pass_when_shrank():
    c = ph.check_backlog_trend(total_backlog=110, delta=-12)
    assert c.state == "pass" and "shrank by 12" in c.detail


def test_backlog_trend_pass_when_unchanged():
    assert ph.check_backlog_trend(100, 0).state == "pass"


# --- vault sync ------------------------------------------------------------------

def test_vault_pass_when_matched():
    assert ph.check_vault_sync(236, 236).state == "pass"


def test_vault_fail_when_drifted():
    c = ph.check_vault_sync(236, 244)
    assert c.state == "fail" and "drift -8" in c.detail


# --- gemini quota ------------------------------------------------------------------

def test_quota_pass_normal():
    c = ph.check_gemini_quota(remaining=14, backlog=50, quota_log_age_hours=5.0)
    assert c.state == "pass"


def test_quota_degraded_when_full_and_stale_with_backlog():
    # confirmed live 2026-08-27: quota sat at 20/20 for 3 days while backlog
    # grew, because nothing was running -- not because nothing needed doing
    c = ph.check_gemini_quota(remaining=20, backlog=139, quota_log_age_hours=72.0)
    assert c.state == "degraded"
    assert "looks like nothing is running" in c.detail


def test_quota_pass_when_full_but_backlog_is_zero():
    # a genuinely empty backlog explains an untouched quota just fine
    c = ph.check_gemini_quota(remaining=20, backlog=0, quota_log_age_hours=72.0)
    assert c.state == "pass"


def test_quota_pass_when_full_and_fresh():
    # unused quota is not suspicious if the pipeline JUST ran
    c = ph.check_gemini_quota(remaining=20, backlog=139, quota_log_age_hours=2.0)
    assert c.state == "pass"


def test_quota_degraded_when_unreadable():
    assert ph.check_gemini_quota(remaining=None).state == "degraded"


# --- ntfy delivery: verified end-to-end, not just "no exception" -----------------

def test_ntfy_pass_when_not_exercised():
    c = ph.check_ntfy_delivery(attempted=False, status_code=None, error=None)
    assert c.state == "pass" and "not exercised" in c.detail


def test_ntfy_pass_when_ntfy_confirms_200():
    c = ph.check_ntfy_delivery(attempted=True, status_code=200, error=None)
    assert c.state == "pass" and "accepted" in c.detail


def test_ntfy_fail_on_real_rejection():
    # this is exactly the 2026-08-21..24 daily-digest incident shape --
    # ntfy.sh itself returning non-200, not our code raising
    c = ph.check_ntfy_delivery(attempted=True, status_code=429, error="Too Many Requests")
    assert c.state == "fail" and "429" in c.detail or "Too Many Requests" in c.detail


def test_ntfy_fail_on_network_error_not_just_status():
    c = ph.check_ntfy_delivery(attempted=True, status_code=None, error="ConnectError: timed out")
    assert c.state == "fail"


# --- consolidation: worst-state wins, then the alert decision -------------------

def test_summarize_all_pass():
    checks = [ph.Check("a", "pass", ""), ph.Check("b", "pass", "")]
    assert ph.summarize(checks) == "pass"


def test_summarize_degraded_beats_pass():
    checks = [ph.Check("a", "pass", ""), ph.Check("b", "degraded", "")]
    assert ph.summarize(checks) == "degraded"


def test_summarize_fail_beats_everything():
    checks = [ph.Check("a", "fail", ""), ph.Check("b", "degraded", ""), ph.Check("c", "pass", "")]
    assert ph.summarize(checks) == "fail"


def test_should_alert_only_on_non_pass():
    assert ph.should_alert("pass") is False
    assert ph.should_alert("degraded") is True
    assert ph.should_alert("fail") is True


# --- report formatting: one-liner healthy, itemized when not --------------------

def test_format_report_all_green_one_liner():
    checks = [ph.Check("a", "pass", "fine"), ph.Check("b", "pass", "fine")]
    report = ph.format_report(checks, "pass", "2026-08-27 09:00 UTC")
    assert "ALL GREEN" in report
    assert report.count("\n") == 0  # genuinely one line


def test_format_report_itemizes_only_when_not_healthy():
    checks = [
        ph.Check("backend", "pass", "ok"),
        ph.Check("nightly_pipeline", "fail", "stale 48h"),
        ph.Check("ollama", "degraded", "down"),
    ]
    report = ph.format_report(checks, "fail", "2026-08-27 09:00 UTC")
    assert "FAIL" in report
    assert "nightly_pipeline: stale 48h" in report
    assert "[WARN] ollama: down" in report
    assert "[OK  ] backend: ok" in report  # passing checks still listed for context


# --- Task Scheduler result-code decoding -----------------------------------------

def test_decode_scheduler_result_known_battery_code(monkeypatch):
    class FakeResult:
        returncode = 0
        stdout = (
            '"HostName","TaskName","Next Run Time","Status","Logon Mode",'
            '"Last Run Time","Last Result"\r\n'
            '"H","\\ReelBrain Nightly Runner","27-08-2026","Ready","Interactive",'
            '"26-08-2026 20:39:06","-2147020576"\r\n'
        )
    monkeypatch.setattr(ph.subprocess, "run", lambda *a, **k: FakeResult())
    detail = ph._decode_scheduler_result()
    assert "4320" in detail
    assert "battery" in detail.lower()


def test_decode_scheduler_result_success_code(monkeypatch):
    class FakeResult:
        returncode = 0
        stdout = (
            '"TaskName","Last Run Time","Last Result"\r\n'
            '"\\x","27-08-2026 18:00:00","0"\r\n'
        )
    monkeypatch.setattr(ph.subprocess, "run", lambda *a, **k: FakeResult())
    detail = ph._decode_scheduler_result()
    assert "success" in detail


def test_decode_scheduler_result_never_raises_on_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("schtasks not found")
    monkeypatch.setattr(ph.subprocess, "run", boom)
    assert ph._decode_scheduler_result() == ""
