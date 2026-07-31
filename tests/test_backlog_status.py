"""scripts/backlog_status.py — the drain report. Pure formatting; collect() is
the only Notion-touching part and is not exercised here."""
from scripts import backlog_status as bs


def _data(**over):
    base = {"notion_rows": 191, "vault_notes": 174, "count_drift": 17,
            "backlogs": {"named_entities": 129, "recover_placeholders": 57,
                         "suggested_action": 119, "plain_summary": 132,
                         "ingest_resources": 28},
            "enforce_topics_free": 0, "total_gemini_calls": 579,
            "markers": {"uncategorized": 28, "pending-extraction": 19}}
    base.update(over)
    return base


def test_costs_match_daily_runner_so_the_two_never_disagree():
    from scripts import daily_runner
    assert bs.COSTS == daily_runner.ROW_COST


def test_recovery_is_costed_higher_than_a_plain_backfill():
    assert bs.COSTS["recover_placeholders"] > bs.COSTS["named_entities"]


def test_report_shows_total_calls_not_just_rows():
    out = bs.format_report(_data())
    assert "~579 Gemini calls" in out


def test_report_labels_days_as_a_scenario_not_a_fact():
    """Days depend entirely on account tier — the variable this project has
    repeatedly gotten wrong — so it must never read as a fact."""
    out = bs.format_report(_data())
    assert "scenario A" in out and "scenario B" in out
    assert "not rate-bound" in out


def test_free_step_is_shown_at_zero_cost():
    out = bs.format_report(_data(enforce_topics_free=5))
    assert "enforce_topics (FREE)" in out


def test_report_surfaces_fallback_marker_counts():
    out = bs.format_report(_data())
    assert "pending-extraction=19" in out and "uncategorized=28" in out


def test_a_failed_lookup_renders_as_error_not_zero():
    """A broken lookup must not silently read as 'nothing pending'."""
    data = _data()
    data["backlogs"]["plain_summary"] = None
    assert "ERROR" in bs.format_report(data)


def test_drift_is_signed():
    assert "+17" in bs.format_report(_data())
