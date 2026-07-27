"""app/gemini_quota.py — the real per-model daily call tracker. Pure/injected:
time and file path are passed in, nothing touches the wall clock or a real file
outside tmp_path."""
from datetime import datetime, timezone

from app import gemini_quota as q


def _utc(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


# --- Pacific reset boundary --------------------------------------------------------

def test_pacific_day_uses_pst_offset_in_winter():
    # Jan 5 2026, 03:00 UTC -> 2026-01-04 19:00 PST (UTC-8), still Jan 4 in Pacific
    assert q.pacific_quota_day(_utc(2026, 1, 5, 3)) == "2026-01-04"


def test_pacific_day_uses_pdt_offset_in_summer():
    # Jul 5 2026, 03:00 UTC -> 2026-07-04 20:00 PDT (UTC-7), still Jul 4 in Pacific
    assert q.pacific_quota_day(_utc(2026, 7, 5, 3)) == "2026-07-04"


def test_dst_starts_second_sunday_of_march():
    # 2026: 2nd Sunday of March is the 8th
    assert q._pacific_offset_hours(_utc(2026, 3, 9)) == -7
    assert q._pacific_offset_hours(_utc(2026, 3, 1)) == -8


def test_dst_ends_first_sunday_of_november():
    # 2026: 1st Sunday of November is the 1st
    assert q._pacific_offset_hours(_utc(2026, 10, 31)) == -7
    assert q._pacific_offset_hours(_utc(2026, 11, 2)) == -8


# --- recording + counting ----------------------------------------------------------

def test_records_and_counts_calls_for_a_model(tmp_path):
    path = tmp_path / "q.json"
    now = _utc(2026, 7, 28)
    for _ in range(3):
        q.record_call("gemini-2.5-flash", path=path, now=now)
    assert q.calls_today("gemini-2.5-flash", path=path, now=now) == 3


def test_counts_are_per_model_independent(tmp_path):
    path = tmp_path / "q.json"
    now = _utc(2026, 7, 28)
    q.record_call("gemini-2.5-flash", path=path, now=now)
    q.record_call("gemini-3.6-flash", path=path, now=now)
    q.record_call("gemini-3.6-flash", path=path, now=now)
    # THE INCIDENT in one assertion: each model has its own count.
    assert q.calls_today("gemini-2.5-flash", path=path, now=now) == 1
    assert q.calls_today("gemini-3.6-flash", path=path, now=now) == 2


def test_calls_from_a_previous_pacific_day_do_not_count(tmp_path):
    path = tmp_path / "q.json"
    q.record_call("gemini-2.5-flash", path=path, now=_utc(2026, 7, 27))
    # next Pacific day
    assert q.calls_today("gemini-2.5-flash", path=path, now=_utc(2026, 7, 28)) == 0


def test_remaining_today_decrements_from_the_limit(tmp_path):
    path = tmp_path / "q.json"
    now = _utc(2026, 7, 28)
    for _ in range(5):
        q.record_call("gemini-2.5-flash", path=path, now=now)
    assert q.remaining_today("gemini-2.5-flash", limit=20, path=path, now=now) == 15


def test_remaining_never_goes_negative(tmp_path):
    path = tmp_path / "q.json"
    now = _utc(2026, 7, 28)
    for _ in range(25):
        q.record_call("gemini-2.5-flash", path=path, now=now)
    assert q.remaining_today("gemini-2.5-flash", limit=20, path=path, now=now) == 0


def test_old_records_are_pruned(tmp_path):
    path = tmp_path / "q.json"
    q.record_call("gemini-2.5-flash", path=path, now=_utc(2026, 7, 1))
    # a call many days later triggers prune of the stale one
    q.record_call("gemini-2.5-flash", path=path, now=_utc(2026, 7, 28))
    import json
    records = json.loads(path.read_text(encoding="utf-8"))
    assert [r["day"] for r in records] == ["2026-07-28"]  # only the recent one survived


def test_corrupt_quota_file_is_treated_as_empty(tmp_path):
    path = tmp_path / "q.json"
    path.write_text("not json{", encoding="utf-8")
    assert q.calls_today("gemini-2.5-flash", path=path, now=_utc(2026, 7, 28)) == 0
