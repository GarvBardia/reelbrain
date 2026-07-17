"""Bulk-import URL reading, progress tracking, and daily-cap self-throttle logic.
submit_capture (the actual HTTP call) is never exercised for real — every test
that drives run_bulk_import injects a fake submit_fn."""
import json

from scripts import bulk_import


# --- URL reading ---------------------------------------------------------------

def test_read_urls_skips_blank_lines_and_comments(tmp_path):
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text(
        "https://www.instagram.com/reel/AAA111/\n"
        "\n"
        "# a comment line\n"
        "   \n"
        "https://www.instagram.com/reel/BBB222/\n"
    )
    assert bulk_import.read_urls(str(urls_file)) == [
        "https://www.instagram.com/reel/AAA111/",
        "https://www.instagram.com/reel/BBB222/",
    ]


def test_read_urls_strips_whitespace(tmp_path):
    urls_file = tmp_path / "urls.txt"
    urls_file.write_text("  https://www.instagram.com/reel/AAA111/  \n")
    assert bulk_import.read_urls(str(urls_file)) == ["https://www.instagram.com/reel/AAA111/"]


# --- progress file ---------------------------------------------------------------

def test_load_progress_missing_file_returns_empty_dict(tmp_path):
    assert bulk_import.load_progress(str(tmp_path / "nope.json")) == {}


def test_save_and_load_progress_roundtrip(tmp_path):
    path = str(tmp_path / "progress.json")
    data = {"https://x/1/": {"status": "processing", "date": "2026-07-17"}}
    bulk_import.save_progress(path, data)
    assert bulk_import.load_progress(path) == data


def test_save_progress_is_atomic_no_leftover_tmp_file(tmp_path):
    path = str(tmp_path / "progress.json")
    bulk_import.save_progress(path, {"a": 1})
    assert not (tmp_path / "progress.json.tmp").exists()


def test_count_submitted_today_only_counts_processing_from_today(monkeypatch):
    monkeypatch.setattr(bulk_import, "today_str", lambda: "2026-07-17")
    progress = {
        "u1": {"status": "processing", "date": "2026-07-17"},
        "u2": {"status": "processing", "date": "2026-07-16"},  # yesterday
        "u3": {"status": "duplicate", "date": "2026-07-17"},   # doesn't consume a fetch
        "u4": {"status": "error", "date": "2026-07-17"},
        "u5": {"status": "processing", "date": "2026-07-17"},
    }
    assert bulk_import.count_submitted_today(progress) == 2


# --- run_bulk_import: the core driving loop --------------------------------------

def _urls_file(tmp_path, *urls):
    path = tmp_path / "urls.txt"
    path.write_text("\n".join(urls) + "\n")
    return str(path)


def _fake_submitter(outcomes: dict[str, dict]):
    """outcomes: url -> result dict (as submit_capture would return)."""
    calls = []

    def _submit(base_url, secret, url):
        calls.append(url)
        return outcomes[url]

    _submit.calls = calls
    return _submit


def test_happy_path_all_new_captures(tmp_path):
    urls = ["https://x/A/", "https://x/B/", "https://x/C/"]
    urls_file = _urls_file(tmp_path, *urls)
    progress_file = str(tmp_path / "progress.json")
    submit = _fake_submitter({u: {"status": "processing", "http_status": 202, "detail": {}} for u in urls})
    sleeps = []

    summary = bulk_import.run_bulk_import(
        urls_file, progress_file, "https://api", "secret",
        spacing_seconds=20, max_per_day=25,
        submit_fn=submit, sleep_fn=lambda s: sleeps.append(s), jitter_fn=lambda: 0,
        print_fn=lambda *a: None,
    )

    assert summary == {"captured": 3, "duplicates": 0, "errors": 0, "skipped": 0,
                       "total_urls": 3, "stopped_early": False}
    assert submit.calls == urls
    # spacing between each of the 3 submissions, none after the last
    assert sleeps == [20, 20]

    progress = bulk_import.load_progress(progress_file)
    assert all(progress[u]["status"] == "processing" for u in urls)


def test_duplicate_and_error_mix(tmp_path):
    urls = ["https://x/A/", "https://x/B/", "https://x/C/"]
    urls_file = _urls_file(tmp_path, *urls)
    progress_file = str(tmp_path / "progress.json")
    submit = _fake_submitter({
        "https://x/A/": {"status": "processing", "http_status": 202, "detail": {}},
        "https://x/B/": {"status": "duplicate", "http_status": 200, "detail": {"url": "notion://b"}},
        "https://x/C/": {"status": "error", "http_status": 400, "detail": "could not extract shortcode"},
    })

    summary = bulk_import.run_bulk_import(
        urls_file, progress_file, "https://api", "secret",
        spacing_seconds=1, max_per_day=25,
        submit_fn=submit, sleep_fn=lambda s: None, jitter_fn=lambda: 0,
        print_fn=lambda *a: None,
    )

    assert summary["captured"] == 1
    assert summary["duplicates"] == 1
    assert summary["errors"] == 1


def test_no_delay_after_a_duplicate(tmp_path):
    """A duplicate short-circuits server-side with no fetch — nothing to space out."""
    urls = ["https://x/A/", "https://x/B/"]
    urls_file = _urls_file(tmp_path, *urls)
    progress_file = str(tmp_path / "progress.json")
    submit = _fake_submitter({
        "https://x/A/": {"status": "duplicate", "http_status": 200, "detail": {}},
        "https://x/B/": {"status": "processing", "http_status": 202, "detail": {}},
    })
    sleeps = []

    bulk_import.run_bulk_import(
        urls_file, progress_file, "https://api", "secret",
        spacing_seconds=20, max_per_day=25,
        submit_fn=submit, sleep_fn=lambda s: sleeps.append(s), jitter_fn=lambda: 0,
        print_fn=lambda *a: None,
    )
    assert sleeps == []  # no sleep after A (duplicate) and none after B (last url)


def test_rerun_skips_terminal_urls(tmp_path):
    """Second run: URLs already 'processing' or 'duplicate' must not be resubmitted."""
    urls = ["https://x/A/", "https://x/B/", "https://x/C/"]
    urls_file = _urls_file(tmp_path, *urls)
    progress_file = str(tmp_path / "progress.json")
    bulk_import.save_progress(progress_file, {
        "https://x/A/": {"status": "processing", "date": "2026-01-01"},
        "https://x/B/": {"status": "duplicate", "date": "2026-01-01"},
    })
    submit = _fake_submitter({"https://x/C/": {"status": "processing", "http_status": 202, "detail": {}}})

    summary = bulk_import.run_bulk_import(
        urls_file, progress_file, "https://api", "secret",
        spacing_seconds=1, max_per_day=25,
        submit_fn=submit, sleep_fn=lambda s: None, jitter_fn=lambda: 0,
        print_fn=lambda *a: None,
    )

    assert submit.calls == ["https://x/C/"]  # A and B never resubmitted
    assert summary["skipped"] == 2
    assert summary["captured"] == 1


def test_errored_url_is_retried_on_a_later_run(tmp_path):
    """'error' status is NOT terminal — a rerun retries it (unlike processing/duplicate)."""
    urls = ["https://x/A/"]
    urls_file = _urls_file(tmp_path, *urls)
    progress_file = str(tmp_path / "progress.json")
    bulk_import.save_progress(progress_file, {
        "https://x/A/": {"status": "error", "date": "2026-01-01", "detail": "network error: timeout"},
    })
    submit = _fake_submitter({"https://x/A/": {"status": "processing", "http_status": 202, "detail": {}}})

    summary = bulk_import.run_bulk_import(
        urls_file, progress_file, "https://api", "secret",
        spacing_seconds=1, max_per_day=25,
        submit_fn=submit, sleep_fn=lambda s: None, jitter_fn=lambda: 0,
        print_fn=lambda *a: None,
    )
    assert submit.calls == ["https://x/A/"]
    assert summary["captured"] == 1
    assert summary["skipped"] == 0


def test_client_side_daily_cap_stops_cleanly(tmp_path):
    """This is the core ask: the script self-throttles to MAX_FETCHES_PER_DAY and
    stops with a clear message, rather than continuing to hammer a server that
    would silently background-fail every further submission today."""
    urls = ["https://x/A/", "https://x/B/", "https://x/C/"]
    urls_file = _urls_file(tmp_path, *urls)
    progress_file = str(tmp_path / "progress.json")
    # already at the cap from earlier submissions today
    bulk_import.save_progress(progress_file, {
        "https://x/PRIOR1/": {"status": "processing", "date": bulk_import.today_str()},
        "https://x/PRIOR2/": {"status": "processing", "date": bulk_import.today_str()},
    })
    submit = _fake_submitter({})  # must never be called
    messages = []

    summary = bulk_import.run_bulk_import(
        urls_file, progress_file, "https://api", "secret",
        spacing_seconds=1, max_per_day=2,
        submit_fn=submit, sleep_fn=lambda s: None, jitter_fn=lambda: 0,
        print_fn=lambda msg: messages.append(msg),
    )

    assert submit.calls == []
    assert summary["stopped_early"] is True
    assert summary["captured"] == 0
    assert any("daily cap reached" in m and "resume tomorrow" in m for m in messages)


def test_daily_cap_only_counts_this_scripts_own_submissions_today(tmp_path):
    urls = ["https://x/A/", "https://x/B/"]
    urls_file = _urls_file(tmp_path, *urls)
    progress_file = str(tmp_path / "progress.json")
    bulk_import.save_progress(progress_file, {
        "https://x/OLD/": {"status": "processing", "date": "2020-01-01"},  # long ago -> doesn't count
    })
    submit = _fake_submitter({u: {"status": "processing", "http_status": 202, "detail": {}} for u in urls})

    summary = bulk_import.run_bulk_import(
        urls_file, progress_file, "https://api", "secret",
        spacing_seconds=1, max_per_day=2,
        submit_fn=submit, sleep_fn=lambda s: None, jitter_fn=lambda: 0,
        print_fn=lambda *a: None,
    )
    assert summary["captured"] == 2
    assert summary["stopped_early"] is False


def test_cap_reached_mid_run_stops_before_next_url(tmp_path):
    urls = ["https://x/A/", "https://x/B/", "https://x/C/"]
    urls_file = _urls_file(tmp_path, *urls)
    progress_file = str(tmp_path / "progress.json")
    submit = _fake_submitter({
        "https://x/A/": {"status": "processing", "http_status": 202, "detail": {}},
        "https://x/B/": {"status": "processing", "http_status": 202, "detail": {}},
    })

    summary = bulk_import.run_bulk_import(
        urls_file, progress_file, "https://api", "secret",
        spacing_seconds=1, max_per_day=2,  # cap hits exactly after A and B
        submit_fn=submit, sleep_fn=lambda s: None, jitter_fn=lambda: 0,
        print_fn=lambda *a: None,
    )
    assert submit.calls == ["https://x/A/", "https://x/B/"]  # C never attempted
    assert summary["stopped_early"] is True
    assert summary["captured"] == 2


def test_auth_error_stops_entire_run_immediately(tmp_path):
    urls = ["https://x/A/", "https://x/B/"]
    urls_file = _urls_file(tmp_path, *urls)
    progress_file = str(tmp_path / "progress.json")
    submit = _fake_submitter({"https://x/A/": {"status": "auth_error", "http_status": 401, "detail": "invalid secret"}})

    summary = bulk_import.run_bulk_import(
        urls_file, progress_file, "https://api", "wrong-secret",
        spacing_seconds=1, max_per_day=25,
        submit_fn=submit, sleep_fn=lambda s: None, jitter_fn=lambda: 0,
        print_fn=lambda *a: None,
    )
    assert submit.calls == ["https://x/A/"]  # B never attempted — no point hammering with a bad secret
    assert summary["stopped_early"] is True


def test_rate_limited_sleeps_extra_and_continues(tmp_path):
    urls = ["https://x/A/", "https://x/B/"]
    urls_file = _urls_file(tmp_path, *urls)
    progress_file = str(tmp_path / "progress.json")
    submit = _fake_submitter({
        "https://x/A/": {"status": "rate_limited", "http_status": 429, "detail": "rate limit exceeded"},
        "https://x/B/": {"status": "processing", "http_status": 202, "detail": {}},
    })
    sleeps = []

    summary = bulk_import.run_bulk_import(
        urls_file, progress_file, "https://api", "secret",
        spacing_seconds=20, max_per_day=25,
        submit_fn=submit, sleep_fn=lambda s: sleeps.append(s), jitter_fn=lambda: 0,
        print_fn=lambda *a: None,
    )
    assert submit.calls == ["https://x/A/", "https://x/B/"]  # continues after rate limit
    assert summary["errors"] == 1
    assert summary["captured"] == 1
    assert 40 in sleeps  # the extra rate-limited backoff (2x spacing) happened


def test_dry_run_makes_no_submissions_and_writes_no_progress(tmp_path):
    urls = ["https://x/A/", "https://x/B/"]
    urls_file = _urls_file(tmp_path, *urls)
    progress_file = str(tmp_path / "progress.json")
    submit = _fake_submitter({})  # must never be called

    summary = bulk_import.run_bulk_import(
        urls_file, progress_file, "https://api", "secret",
        spacing_seconds=1, max_per_day=25, dry_run=True,
        submit_fn=submit, sleep_fn=lambda s: None, jitter_fn=lambda: 0,
        print_fn=lambda *a: None,
    )
    assert submit.calls == []
    assert summary["captured"] == 0
    assert not (tmp_path / "progress.json").exists()


def test_jitter_is_added_to_spacing(tmp_path):
    urls = ["https://x/A/", "https://x/B/"]
    urls_file = _urls_file(tmp_path, *urls)
    progress_file = str(tmp_path / "progress.json")
    submit = _fake_submitter({u: {"status": "processing", "http_status": 202, "detail": {}} for u in urls})
    sleeps = []

    bulk_import.run_bulk_import(
        urls_file, progress_file, "https://api", "secret",
        spacing_seconds=20, max_per_day=25,
        submit_fn=submit, sleep_fn=lambda s: sleeps.append(s), jitter_fn=lambda: 3.5,
        print_fn=lambda *a: None,
    )
    assert sleeps == [23.5]


# --- submit_capture response-shape mapping (real function, fake httpx response) ---

class _FakeHttpxResponse:
    def __init__(self, status_code, json_body=None):
        self.status_code = status_code
        self._json_body = json_body if json_body is not None else {}

    def json(self):
        return self._json_body


def test_submit_capture_maps_202_to_processing(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeHttpxResponse(202, {"status": "processing", "shortcode": "X"}))
    result = bulk_import.submit_capture("https://api", "secret", "https://x/A/")
    assert result["status"] == "processing"
    assert result["http_status"] == 202


def test_submit_capture_maps_200_duplicate_body_to_duplicate(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeHttpxResponse(200, {"status": "duplicate", "url": "n://x"}))
    result = bulk_import.submit_capture("https://api", "secret", "https://x/A/")
    assert result["status"] == "duplicate"


def test_submit_capture_maps_401_to_auth_error(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeHttpxResponse(401, {"detail": "invalid secret"}))
    result = bulk_import.submit_capture("https://api", "wrong", "https://x/A/")
    assert result["status"] == "auth_error"


def test_submit_capture_maps_429_to_rate_limited(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeHttpxResponse(429, {"detail": "rate limit exceeded"}))
    result = bulk_import.submit_capture("https://api", "secret", "https://x/A/")
    assert result["status"] == "rate_limited"


def test_submit_capture_maps_400_to_error(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeHttpxResponse(400, {"detail": "could not extract shortcode"}))
    result = bulk_import.submit_capture("https://api", "secret", "https://x/A/")
    assert result["status"] == "error"
    assert result["http_status"] == 400


def test_submit_capture_network_error_maps_to_error(monkeypatch):
    import httpx

    def _boom(*a, **k):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(httpx, "post", _boom)
    result = bulk_import.submit_capture("https://api", "secret", "https://x/A/")
    assert result["status"] == "error"
    assert result["http_status"] is None
    assert "network error" in result["detail"]
