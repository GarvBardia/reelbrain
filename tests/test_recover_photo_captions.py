"""scripts/recover_photo_captions.py — all mocked. The OG formats in the
caption-cleaning tests are the REAL shapes captured during the live viability
probe (see PROGRESS.md), not invented fixtures."""
from scripts import recover_photo_captions as rpc


# --- clean_og_caption ----------------------------------------------------------

def test_clean_caption_from_og_title_quoted():
    tags = {
        "og:title": 'Chase AI on Instagram: "Comment “agent” to get my free Claude code guides & skills"',
        "og:description": '1,382 likes, 483 comments - chase.h.ai on July 9, 2026: "Comment “agent” to get my free Claude code guides & skills".',
    }
    caption, username = rpc.clean_og_caption(tags)
    assert caption == "Comment “agent” to get my free Claude code guides & skills"
    assert username == "chase.h.ai"


def test_clean_caption_falls_back_to_description_with_stats_prefix_stripped():
    tags = {
        "og:title": "Some Person on Instagram",  # no quoted caption in title
        "og:description": '12 likes, 3 comments - someuser on January 2, 2026: "the actual caption text here"',
    }
    caption, username = rpc.clean_og_caption(tags)
    assert caption == "the actual caption text here"
    assert username == "someuser"


def test_clean_caption_none_when_no_caption_content():
    assert rpc.clean_og_caption({"og:image": "https://x/y.jpg"}) == (None, None)
    # title without the 'on Instagram: "..."' shape and description without stats prefix
    caption, _ = rpc.clean_og_caption({"og:title": "Instagram", "og:description": "Instagram"})
    assert caption is None


# --- find_placeholder_rows filter ----------------------------------------------

def _page(shortcode, title, status, topics=()):
    return {
        "id": f"pg-{shortcode}", "url": f"https://notion.so/pg-{shortcode}",
        "properties": {
            "Shortcode": {"rich_text": [{"plain_text": shortcode}]},
            "Title": {"title": [{"plain_text": title}]},
            "My note": {"rich_text": []},
            "Status": {"select": {"name": status}},
            "Reel URL": {"url": f"https://www.instagram.com/reel/{shortcode}/"},
            "Gate keyword": {"rich_text": []},
            "Topics": {"multi_select": [{"name": t} for t in topics]},
        },
    }


def test_find_placeholder_rows_selects_unrecovered_photo_rows(monkeypatch):
    from app import notion_writer

    pages = [
        _page("PH1", rpc.PLACEHOLDER_TITLE, rpc.PHOTO_MANUAL_LABEL),  # yes: placeholder
        # yes: caption-as-title but NO real topics (pre-guard degraded write)
        _page("HALFDONE1", "comment agent for my guides", rpc.PHOTO_MANUAL_LABEL,
              topics=("near-duplicate",)),
        # no: real title AND real topics -> genuinely recovered
        _page("RECOVERED1", "A real recovered title", rpc.PHOTO_MANUAL_LABEL,
              topics=("claude-ai", "resource-sharing")),
        _page("VID1", rpc.PLACEHOLDER_TITLE, "📥 Inbox"),               # no: not photo-manual
    ]
    monkeypatch.setattr(notion_writer, "find_saves_pages_since", lambda iso: pages)

    rows = rpc.find_placeholder_rows()
    assert [r["shortcode"] for r in rows] == ["PH1", "HALFDONE1"]


# --- run_recovery loop ----------------------------------------------------------

def _rows(*shortcodes):
    return [
        {"shortcode": s, "permalink": f"https://www.instagram.com/reel/{s}/",
         "page_id": f"pg-{s}", "note": None, "title": rpc.PLACEHOLDER_TITLE}
        for s in shortcodes
    ]


def test_dry_run_lists_without_recovering(tmp_path):
    attempts = []
    printed = []

    summary = rpc.run_recovery(
        _rows("A1", "B2"), str(tmp_path / "p.json"), 10, dry_run=True,
        recover_fn=lambda f: attempts.append(f) or {"status": "recovered"},
        sleep_fn=lambda s: None, jitter_fn=lambda: 0, print_fn=printed.append,
    )

    assert attempts == []
    assert summary["recovered"] == 0
    assert any("would attempt: A1" in line for line in printed)


def test_recovery_records_progress_and_spaces_fetches(tmp_path):
    progress_file = str(tmp_path / "p.json")
    sleeps = []

    summary = rpc.run_recovery(
        _rows("A1", "B2"), progress_file, 10,
        recover_fn=lambda f: {"status": "recovered", "title": "t", "topics": [], "priority": "Low"},
        sleep_fn=sleeps.append, jitter_fn=lambda: 0, print_fn=lambda m: None,
    )

    assert summary["recovered"] == 2
    assert sleeps == [10]  # between the two rows, none after the last
    assert rpc.load_progress(progress_file)["A1"]["status"] == "recovered"


def test_rerun_skips_recovered_but_retries_no_caption_and_errors(tmp_path):
    progress_file = str(tmp_path / "p.json")
    rpc.save_progress(progress_file, {
        "DONE1": {"status": "recovered"},
        "NOCAP1": {"status": "no_caption"},
        "ERR1": {"status": "error"},
    })
    attempted = []

    summary = rpc.run_recovery(
        _rows("DONE1", "NOCAP1", "ERR1"), progress_file, 10,
        recover_fn=lambda f: attempted.append(f["shortcode"]) or {"status": "recovered"},
        sleep_fn=lambda s: None, jitter_fn=lambda: 0, print_fn=lambda m: None,
    )

    assert attempted == ["NOCAP1", "ERR1"]  # recovered is terminal, others retry
    assert summary["skipped"] == 1


def test_no_caption_and_error_counted_separately(tmp_path):
    results = {"A1": {"status": "no_caption", "detail": "x"},
               "B2": {"status": "error", "detail": "y"}}

    summary = rpc.run_recovery(
        _rows("A1", "B2"), str(tmp_path / "p.json"), 10,
        recover_fn=lambda f: results[f["shortcode"]],
        sleep_fn=lambda s: None, jitter_fn=lambda: 0, print_fn=lambda m: None,
    )

    assert summary["no_caption"] == 1
    assert summary["errors"] == 1
    assert summary["recovered"] == 0


# --- recover_row uses the bot UA (the finding the whole script rests on) --------

def test_fetch_og_tags_uses_bot_user_agent(monkeypatch):
    calls = []

    class _Resp:
        text = '<meta property="og:title" content="X on Instagram: &quot;hi there&quot;" />'

        def raise_for_status(self):
            return None

    def _fake_get(url, headers=None, timeout=None, follow_redirects=None):
        calls.append(headers["User-Agent"])
        return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "get", _fake_get)

    tags = rpc.fetch_og_tags_with_bot_ua("https://www.instagram.com/reel/X/")
    assert tags is not None
    assert calls == [rpc.BOT_USER_AGENT]
    assert "facebookexternalhit" in calls[0]  # NOT the browser UA — that gets no tags


def test_degraded_extraction_is_retryable_and_never_written(monkeypatch):
    """Live finding: a transient Gemini 503 degrades the extraction to
    caption-as-title — that must NOT be written to Notion nor marked
    recovered; the row stays retryable for the next run."""
    from app import gemini_pipe, notion_writer, store
    from app.models import Extraction

    class _Resp:
        text = ('<meta property="og:title" content="X on Instagram: '
                '&quot;a long enough caption with plenty of words to extract from&quot;" />')

        def raise_for_status(self):
            return None

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _Resp())
    # degraded shape: unknown content type, no topics (what a 503 produces)
    monkeypatch.setattr(
        gemini_pipe, "run_caption_only_extraction",
        lambda caption, creator, note, taxonomy: Extraction(main_point=caption, content_type="unknown"),
    )
    monkeypatch.setattr(
        notion_writer, "update_page",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not write a degraded extraction")),
    )
    monkeypatch.setattr(store, "get_taxonomy", lambda: [])

    result = rpc.recover_row({"shortcode": "X1", "permalink": "https://www.instagram.com/reel/X1/",
                              "page_id": "pg-X1", "note": None})
    assert result["status"] == "error"
    assert "degraded" in result["detail"]
