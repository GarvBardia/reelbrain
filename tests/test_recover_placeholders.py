"""scripts/recover_placeholders.py — the automated recovery worker. All mocked."""
import logging

from scripts import recover_placeholders as rp


# --- selection query ------------------------------------------------------------

def _page(shortcode, title, status):
    return {
        "id": f"pg-{shortcode}", "url": f"https://notion.so/pg-{shortcode}",
        "properties": {
            "Shortcode": {"rich_text": [{"plain_text": shortcode}]},
            "Title": {"title": [{"plain_text": title}]},
            "My note": {"rich_text": []},
            "Status": {"select": {"name": status}},
            "Reel URL": {"url": f"https://www.instagram.com/reel/{shortcode}/"},
            "Gate keyword": {"rich_text": []},
            "Topics": {"multi_select": []},
        },
    }


def test_selection_includes_failed_retry_rows(monkeypatch):
    """Failed—retry rows never had a successful fetch (Title is still the raw
    permalink). Render's blocked IP is usually why, so the home-IP worker is
    exactly the right place to retry them -- rather than archiving them."""
    from app import notion_writer

    pages = [
        _page("FAILED1", "https://www.instagram.com/reel/FAILED1/", "⚠️ Failed — retry"),
        _page("FINE1", "A real title", "📥 Inbox"),
    ]
    monkeypatch.setattr(notion_writer, "find_saves_pages_since", lambda iso: pages)
    assert [r["shortcode"] for r in rp.find_placeholder_rows()] == ["FAILED1"]


def test_selection_catches_bare_permalink_title_on_any_status(monkeypatch):
    """A row whose Title is still its own permalink never got extracted,
    whatever status it now carries."""
    from app import notion_writer

    pages = [_page("BARE1", "https://www.instagram.com/reel/BARE1/", "📥 Inbox")]
    monkeypatch.setattr(notion_writer, "find_saves_pages_since", lambda iso: pages)
    assert [r["shortcode"] for r in rp.find_placeholder_rows()] == ["BARE1"]


def test_unrelated_url_in_title_does_not_falsely_match(monkeypatch):
    """The permalink check requires the row's OWN shortcode in the URL, so a
    row whose title merely happens to start with a link isn't swept in."""
    from app import notion_writer

    pages = [_page("REAL1", "https://example.com/some-article-about-things", "📥 Inbox")]
    monkeypatch.setattr(notion_writer, "find_saves_pages_since", lambda iso: pages)
    assert rp.find_placeholder_rows() == []


def test_selection_matches_photo_manual_or_placeholder_title(monkeypatch):
    from app import notion_writer

    pages = [
        _page("PHOTO1", "A real title", rp.PHOTO_MANUAL_LABEL),        # yes: photo-manual status
        _page("PLACE1", rp.PLACEHOLDER_TITLE, "📥 Inbox"),              # yes: placeholder title, any status
        _page("BOTH1", rp.PLACEHOLDER_TITLE, rp.PHOTO_MANUAL_LABEL),   # yes: both
        _page("FINE1", "A real title", "📥 Inbox"),                     # no
        _page("", rp.PLACEHOLDER_TITLE, rp.PHOTO_MANUAL_LABEL),        # no: no shortcode
    ]
    monkeypatch.setattr(notion_writer, "find_saves_pages_since", lambda iso: pages)

    rows = rp.find_placeholder_rows()
    assert [r["shortcode"] for r in rows] == ["PHOTO1", "PLACE1", "BOTH1"]


# --- run_worker loop ------------------------------------------------------------

def _rows(*shortcodes):
    return [
        {"shortcode": s, "permalink": f"https://www.instagram.com/p/{s}/",
         "page_id": f"pg-{s}", "note": None, "title": rp.PLACEHOLDER_TITLE}
        for s in shortcodes
    ]


def test_dry_run_attempts_nothing(tmp_path):
    attempts, printed = [], []
    summary = rp.run_worker(
        _rows("A1"), str(tmp_path / "p.json"), dry_run=True,
        recover_fn=lambda f: attempts.append(f) or {"status": "recovered"},
        print_fn=printed.append,
    )
    assert attempts == []
    assert summary["recovered"] == 0
    assert any("would attempt: A1" in line for line in printed)


def test_success_is_terminal_and_failures_count_attempts(tmp_path):
    progress_file = str(tmp_path / "p.json")
    rp.run_worker(
        _rows("OK1", "BAD1"), progress_file,
        recover_fn=lambda f: {"status": "recovered", "path": "caption-only", "new_status": "done"}
        if f["shortcode"] == "OK1" else {"status": "error", "detail": "boom"},
        print_fn=lambda m: None,
    )
    progress = rp.load_progress(progress_file)
    assert progress["OK1"]["status"] == "recovered"
    assert progress["OK1"]["attempts"] == 0
    assert progress["BAD1"]["attempts"] == 1

    # second run: recovered row skipped, failed row retried (attempt 2)
    attempted = []
    rp.run_worker(
        _rows("OK1", "BAD1"), progress_file,
        recover_fn=lambda f: attempted.append(f["shortcode"]) or {"status": "error", "detail": "boom"},
        print_fn=lambda m: None,
    )
    assert attempted == ["BAD1"]
    assert rp.load_progress(progress_file)["BAD1"]["attempts"] == 2


def test_three_failed_attempts_is_a_permanent_skip(tmp_path):
    progress_file = str(tmp_path / "p.json")
    rp.save_progress(progress_file, {"DEAD1": {"status": "no_caption", "attempts": rp.MAX_ATTEMPTS}})
    attempted, printed = [], []
    summary = rp.run_worker(
        _rows("DEAD1"), progress_file,
        recover_fn=lambda f: attempted.append(f) or {"status": "recovered"},
        print_fn=printed.append,
    )
    assert attempted == []
    assert summary["skipped"] == 1
    assert any("permanent" in line for line in printed)


def test_quota_stop_halts_run_without_burning_attempts(tmp_path):
    progress_file = str(tmp_path / "p.json")
    attempted = []

    def recover(fields):
        attempted.append(fields["shortcode"])
        if fields["shortcode"] == "Q1":
            return {"status": "quota_stop", "detail": "Gemini 429"}
        return {"status": "recovered", "path": "full", "new_status": "done"}

    summary = rp.run_worker(_rows("Q1", "NEVER1"), progress_file,
                            recover_fn=recover, print_fn=lambda m: None)
    assert attempted == ["Q1"]          # run stopped before NEVER1
    assert summary["quota_stopped"] is True
    progress = rp.load_progress(progress_file)
    assert "Q1" not in progress          # attempt NOT counted for a quota stop
    assert "NEVER1" not in progress


# --- quota detection + status routing -------------------------------------------

def test_quota_watcher_detects_429_in_gemini_log():
    watcher = rp._QuotaWatcher()
    gemini_logger = logging.getLogger("reelbrain.gemini")
    gemini_logger.addHandler(watcher)
    try:
        gemini_logger.warning("call failed: 429 RESOURCE_EXHAUSTED for entity x")
    finally:
        gemini_logger.removeHandler(watcher)
    assert watcher.quota_hit is True


def test_quota_watcher_ignores_non_quota_errors():
    watcher = rp._QuotaWatcher()
    gemini_logger = logging.getLogger("reelbrain.gemini")
    gemini_logger.addHandler(watcher)
    try:
        gemini_logger.warning("call failed: 503 service unavailable")
    finally:
        gemini_logger.removeHandler(watcher)
    assert watcher.quota_hit is False


def test_routed_status_preserves_detected_gate():
    from app.models import Extraction

    gated = Extraction(main_point="x", comment_gate={"detected": True, "keyword": "SEND"})
    plain = Extraction(main_point="x")
    assert rp._routed_status(gated) == "awaiting_dm"
    assert rp._routed_status(plain) == "done"
