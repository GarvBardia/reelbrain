"""scripts/daily_capture_report.py — the pure trend, push-decision, and
summary logic. Fully mocked: no Notion, no vault, no network, no clock, no
files (history I/O is exercised via tmp_path only)."""
import json
from datetime import datetime, timezone

from scripts import daily_capture_report as dcr


# --- backlog membership ------------------------------------------------------------

def test_unknown_content_type_is_backlog():
    assert dcr.is_backlog_row("unknown", ["ai-tools"])


def test_empty_content_type_is_backlog():
    assert dcr.is_backlog_row("", ["ai-tools"])


def test_marker_topic_is_backlog_even_with_real_type():
    assert dcr.is_backlog_row("tutorial", ["pending-extraction"])
    assert dcr.is_backlog_row("insight", ["uncategorized"])


def test_processed_row_is_not_backlog():
    assert not dcr.is_backlog_row("tutorial", ["ai-tools", "claude-ai"])


def test_content_type_case_insensitive():
    assert dcr.is_backlog_row("UNKNOWN", ["ai-tools"])


# --- capture window ----------------------------------------------------------------

_NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def test_within_window_true_for_recent():
    assert dcr.within_window("2026-08-20T02:00:00.000Z", _NOW)  # 10h ago


def test_within_window_false_for_old():
    assert not dcr.within_window("2026-08-18T12:00:00.000Z", _NOW)  # 48h ago


def test_within_window_false_for_blank():
    assert not dcr.within_window("", _NOW)


# --- partition_capture -------------------------------------------------------------

def _row(created, ct, topics):
    return {"created_time": created, "content_type": ct, "topics": topics}


def test_partition_counts_captures_and_backlog():
    rows = [
        _row("2026-08-20T06:00:00Z", "unknown", []),          # today, pending
        _row("2026-08-20T07:00:00Z", "tutorial", ["ai"]),      # today, processed
        _row("2026-08-10T00:00:00Z", "unknown", ["x"]),        # old, backlog
        _row("2026-08-01T00:00:00Z", "insight", ["ai"]),       # old, done
    ]
    s = dcr.partition_capture(rows, _NOW)
    assert s["captured_today"] == 2
    assert s["captured_today_pending"] == 1
    assert s["captured_today_processed"] == 1
    assert s["total_backlog"] == 2      # the two unknowns
    assert s["needs_extraction"] == 2   # both unknown content_type


# --- pace parsing ------------------------------------------------------------------

def test_parse_recent_pace_averages_nonzero_runs():
    lines = [
        "[x] live pass. Gemini: 20/20 calls used | Local: 3 processed.",
        "[x] live pass. Gemini: 16/20 calls used, STOPPED on a 429.",
    ]
    assert dcr.parse_recent_pace(lines) == 18.0


def test_parse_recent_pace_skips_zero_runs():
    lines = [
        "[x] live pass. Gemini: 0/20 calls used.",
        "[x] live pass. Gemini: 10/20 calls used.",
    ]
    assert dcr.parse_recent_pace(lines) == 10.0


def test_parse_recent_pace_none_when_no_data():
    assert dcr.parse_recent_pace(["nothing here"]) is None


def test_parse_recent_pace_accepts_legacy_quota_label():
    # The log's field was labelled "Quota:" before it became "Gemini:";
    # both must count or every older run is silently ignored.
    lines = [
        "[x] live pass. Quota: 7/20 calls used, STOPPED on a 429.",
        "[x] live pass. Quota: 20/20 calls used. Still pending.",
    ]
    assert dcr.parse_recent_pace(lines) == 13.5


# --- processed_today (derived) -----------------------------------------------------

def test_processed_today_none_on_first_run():
    assert dcr.compute_processed_today(None, 5, 100) is None


def test_processed_today_backlog_shrank():
    # started at 120, 4 new unprocessed arrived, ended at 110 => 14 finished
    assert dcr.compute_processed_today(120, 4, 110) == 14


def test_processed_today_floored_at_zero():
    # ended higher than prev+new: assumptions broke; never report negative
    assert dcr.compute_processed_today(100, 2, 130) == 0


# --- trend delta -------------------------------------------------------------------

def test_trend_delta_grew():
    assert dcr.trend_delta(126, 132) == 6


def test_trend_delta_shrank():
    assert dcr.trend_delta(132, 120) == -12


def test_trend_delta_none_first_run():
    assert dcr.trend_delta(None, 132) is None


# --- push decision -----------------------------------------------------------------

def test_push_when_backlog_grew():
    assert dcr.should_push(6) is True


def test_no_push_when_backlog_shrank():
    assert dcr.should_push(-12) is False


def test_no_push_when_steady():
    assert dcr.should_push(0) is False


def test_no_push_on_first_run():
    assert dcr.should_push(None) is False


# --- days-to-clear estimate --------------------------------------------------------

def test_days_uses_observed_pace():
    # 30 rows * 3 calls = 90 calls, at 18/day => 5 days
    assert dcr.estimate_days_to_clear(30, 18.0) == 5


def test_days_falls_back_when_no_pace():
    # 6 rows * 3 = 18 calls, fallback 18/day => 1 day
    assert dcr.estimate_days_to_clear(6, None) == 1


def test_days_zero_when_nothing_left():
    assert dcr.estimate_days_to_clear(0, 18.0) == 0


# --- history persistence -----------------------------------------------------------

def test_latest_prior_backlog_skips_today():
    hist = [
        {"date": "2026-08-18", "total_backlog": 126},
        {"date": "2026-08-19", "total_backlog": 130},
        {"date": "2026-08-20", "total_backlog": 999},  # an earlier run TODAY
    ]
    assert dcr.latest_prior_backlog(hist, "2026-08-20") == 130


def test_latest_prior_backlog_none_when_empty():
    assert dcr.latest_prior_backlog([], "2026-08-20") is None


def test_upsert_replaces_same_date():
    hist = [{"date": "2026-08-20", "total_backlog": 100, "captured_today": 1,
             "processed_today": 0}]
    out = dcr.upsert_today(hist, {"date": "2026-08-20", "total_backlog": 132,
                                  "captured_today": 9, "processed_today": 0})
    assert len(out) == 1 and out[0]["total_backlog"] == 132


def test_upsert_appends_new_date_sorted():
    hist = [{"date": "2026-08-19", "total_backlog": 126}]
    out = dcr.upsert_today(hist, {"date": "2026-08-20", "total_backlog": 132})
    assert [e["date"] for e in out] == ["2026-08-19", "2026-08-20"]


def test_load_history_missing_file(tmp_path):
    assert dcr.load_history(tmp_path / "nope.json") == []


def test_load_history_roundtrip(tmp_path):
    p = tmp_path / "backlog_history.json"
    entries = [{"date": "2026-08-19", "total_backlog": 126,
                "captured_today": 3, "processed_today": 5}]
    p.write_text(json.dumps(entries), encoding="utf-8")
    assert dcr.load_history(p) == entries


def test_load_history_corrupt_file(tmp_path):
    p = tmp_path / "backlog_history.json"
    p.write_text("{not valid json", encoding="utf-8")
    assert dcr.load_history(p) == []


# --- summary formatting ------------------------------------------------------------

def _snap(captured, pending, backlog, needs):
    return {"captured_today": captured, "captured_today_pending": pending,
            "captured_today_processed": captured - pending,
            "total_backlog": backlog, "needs_extraction": needs}


def test_summary_reports_growth_and_sync():
    s = dcr.format_summary(
        date_str="Aug 20", snapshot=_snap(9, 9, 132, 100), prev_backlog=126,
        processed_today=0, pace=18.0, days_to_clear=17, vault_notes=235,
        notion_rows=235)
    assert "9 captured today" in s
    assert "GREW from 126 to 132 (+6)" in s
    assert "Vault: 235/235, synced" in s


def test_summary_reports_shrink():
    s = dcr.format_summary(
        date_str="Aug 20", snapshot=_snap(2, 1, 114, 90), prev_backlog=126,
        processed_today=13, pace=18.0, days_to_clear=15, vault_notes=235,
        notion_rows=235)
    assert "shrank from 126 to 114 (-12)" in s


def test_summary_flags_vault_drift():
    s = dcr.format_summary(
        date_str="Aug 20", snapshot=_snap(0, 0, 100, 80), prev_backlog=100,
        processed_today=0, pace=None, days_to_clear=14, vault_notes=230,
        notion_rows=235)
    assert "DRIFT -5" in s


def test_summary_first_run_no_prior():
    s = dcr.format_summary(
        date_str="Aug 20", snapshot=_snap(5, 5, 132, 100), prev_backlog=None,
        processed_today=None, pace=18.0, days_to_clear=17, vault_notes=235,
        notion_rows=235)
    assert "first tracked day" in s
    assert "unknown (first run)" in s
