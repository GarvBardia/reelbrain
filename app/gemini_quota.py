"""A REAL local Gemini quota tracker — persists actual call timestamps per model.

WHY THIS EXISTS (the incident): the free tier is ~20 requests/day PER MODEL, and
each concrete model version has its OWN independent cap. When GEMINI_MODEL was
the rolling alias `gemini-flash-latest`, calls scattered across whatever Flash
version Google currently pointed it at (2.5 / 3.5 / 3.6 over a 28-day window),
so daily_runner's "20/day budget" was fiction: it might route to a model already
exhausted by other usage and 429 on call one. We've since pinned GEMINI_MODEL to
one concrete version — but "assume 20 fresh every midnight" is still a guess.
This tracks the truth: how many calls THIS install actually made to a specific
model today, so remaining budget is measured, not assumed.

Reset boundary: Google resets free-tier RPD at midnight PACIFIC. zoneinfo has no
IANA DB on this box (no tzdata), so we compute the US Pacific UTC offset from the
DST rules directly (PDT = UTC-7 from the 2nd Sunday of March to the 1st Sunday of
November, else PST = UTC-8). Being off by an hour at the DST switch twice a year
is harmless for a daily counter, and this keeps the app dependency-free.

Everything is pure + injectable (path, now) so it's testable without real time
or a real file.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DAILY_LIMIT = int(os.environ.get("GEMINI_DAILY_LIMIT", "20"))
QUOTA_FILE = Path(os.environ.get("GEMINI_QUOTA_FILE", "gemini_quota.json"))
RETENTION_DAYS = 4  # keep a little history for debugging; prune the rest


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The nth (1-based) `weekday` (Mon=0..Sun=6) of a month."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _pacific_offset_hours(dt_utc: datetime) -> int:
    """-7 during US Pacific Daylight Time, else -8. DST runs from 02:00 local on
    the 2nd Sunday of March to 02:00 local on the 1st Sunday of November."""
    y = dt_utc.year
    dst_start = _nth_weekday(y, 3, 6, 2)   # 2nd Sunday of March
    dst_end = _nth_weekday(y, 11, 6, 1)    # 1st Sunday of November
    # Approximate the switch at the day boundary (the 02:00 local nuance doesn't
    # matter for a per-day counter). Compare in UTC-ish terms via date.
    today = dt_utc.date()
    return -7 if dst_start <= today < dst_end else -8


def pacific_quota_day(dt_utc: datetime) -> str:
    """The Pacific-local calendar day (YYYY-MM-DD) that `dt_utc` falls in — the
    unit Google's RPD resets on."""
    local = dt_utc + timedelta(hours=_pacific_offset_hours(dt_utc))
    return local.strftime("%Y-%m-%d")


def _now_utc(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []  # a corrupt tracker must never crash a call site


def _save(path: Path, records: list[dict]) -> None:
    try:
        path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    except OSError:
        pass  # best-effort; losing a record is better than failing the pipeline


def _prune(records: list[dict], now: datetime) -> list[dict]:
    cutoff = (now - timedelta(days=RETENTION_DAYS)).timestamp()
    return [r for r in records if r.get("ts", 0) >= cutoff]


def record_call(model: str, path: Path = QUOTA_FILE, now: datetime | None = None) -> None:
    """Append one call for `model`. Recorded at ATTEMPT time (before the request
    returns), so a 429'd attempt still counts — a deliberately CONSERVATIVE
    choice: the tracker may over-count by the handful of rejected calls, which
    can only make us stop early, never blow past the real cap."""
    now = _now_utc(now)
    records = _prune(_load(path), now)
    records.append({"ts": now.timestamp(), "model": model, "day": pacific_quota_day(now)})
    _save(path, records)


def calls_today(model: str, path: Path = QUOTA_FILE, now: datetime | None = None) -> int:
    now = _now_utc(now)
    today = pacific_quota_day(now)
    return sum(1 for r in _load(path) if r.get("model") == model and r.get("day") == today)


def remaining_today(
    model: str,
    limit: int = DEFAULT_DAILY_LIMIT,
    path: Path = QUOTA_FILE,
    now: datetime | None = None,
) -> int:
    """True remaining budget for `model` today: limit minus calls already made,
    floored at 0."""
    return max(0, limit - calls_today(model, path=path, now=now))
