"""scripts/batch_retry_stale_titles.py — all mocked: the heuristic, the
Notion-candidate filter, and the retry loop (submit/sleep/jitter injected,
never the network or the clock)."""
from scripts import batch_retry_stale_titles as brt


# --- looks_like_raw_caption_title: real titles from the live audit -------------

def test_heuristic_matches_titles_starting_with_comment():
    assert brt.looks_like_raw_caption_title(
        'Comment "TOOLS" to get the link (must be following)', "TOOLS") is True
    assert brt.looks_like_raw_caption_title(
        "comment “ads” for the full install guide \U0001f4c8\n\nskill 1. /spy", "ads") is True
    assert brt.looks_like_raw_caption_title(
        "Comment GUIDE and I'll send you all five prompts ready to copy.", "GUIDE") is True


def test_heuristic_matches_quoted_gate_keyword_mid_title():
    # "comment <keyword>" appearing mid-title, not just at the start
    assert brt.looks_like_raw_caption_title(
        'Follow & Comment “RESUME” for the links + 20 more', "RESUME") is True
    assert brt.looks_like_raw_caption_title(
        "Just comment ‘Design’ and I’ll share you the link.", "Design") is True
    assert brt.looks_like_raw_caption_title(
        '(FREE) Claude + Apify = International Clients\n\nComment "International" for free Guide.',
        "International") is True


def test_heuristic_rejects_synthesized_titles():
    # FIX 1's real live output for DZSFkNppVW_ — must NOT match
    assert brt.looks_like_raw_caption_title(
        "You can run your entire Meta ads workflow inside Claude using five custom skills",
        "ads") is False
    assert brt.looks_like_raw_caption_title(
        "Three concrete steps to fix a broken sleep schedule.", None) is False
    assert brt.looks_like_raw_caption_title("", "SEND") is False


def test_heuristic_keyword_check_requires_the_comment_prefix():
    # the keyword alone appearing in a synthesized title must not match
    assert brt.looks_like_raw_caption_title(
        "A guide to running Meta ads with Claude skills", "ads") is False


# --- find_stale_title_candidates: Notion filter --------------------------------

def _page(shortcode, title, status="\U0001f4e5 Inbox", keyword=None):
    return {
        "id": f"pg-{shortcode}", "url": f"https://notion.so/pg-{shortcode}",
        "properties": {
            "Shortcode": {"rich_text": [{"plain_text": shortcode}]},
            "Title": {"title": [{"plain_text": title}]},
            "My note": {"rich_text": []},
            "Status": {"select": {"name": status}},
            "Reel URL": {"url": f"https://www.instagram.com/reel/{shortcode}/"},
            "Gate keyword": {"rich_text": [{"plain_text": keyword}] if keyword else []},
        },
    }


def test_find_candidates_filters_heuristic_and_skips_photo_manual(monkeypatch):
    from app import notion_writer

    pages = [
        _page("RAW1", 'Comment "TOOLS" to get the link', keyword="TOOLS"),
        _page("SYNTH1", "A real synthesized title about Claude workflows", keyword="SEND"),
        _page("PHOTO1", "comment GUIDE for the thing", status=brt.PHOTO_MANUAL_LABEL, keyword="GUIDE"),
        _page("RAW2", "comment SKILL and I'll send you the skill", keyword="SKILL"),
    ]
    monkeypatch.setattr(notion_writer, "find_saves_pages_since", lambda iso: pages)

    candidates = brt.find_stale_title_candidates()

    assert [c["shortcode"] for c in candidates] == ["RAW1", "RAW2"]
    # photo-manual excluded even though its title matches the heuristic
    assert all(c["shortcode"] != "PHOTO1" for c in candidates)


# --- run_batch_retry: loop mechanics -------------------------------------------

def _candidates(*shortcodes):
    return [{"shortcode": s, "title": f"title {s}", "status": "\U0001f4e5 Inbox"} for s in shortcodes]


def test_dry_run_lists_but_never_submits(tmp_path):
    submitted = []
    printed = []

    summary = brt.run_batch_retry(
        _candidates("A1", "B2"), str(tmp_path / "p.json"), "https://x", 20, 25,
        dry_run=True,
        submit_fn=lambda b, s: submitted.append(s) or {"status": "processing"},
        sleep_fn=lambda s: None, jitter_fn=lambda: 0, print_fn=printed.append,
    )

    assert submitted == []
    assert summary["retried"] == 0
    assert any("would retry: A1" in line for line in printed)
    assert any("would retry: B2" in line for line in printed)


def test_retries_space_out_and_record_progress(tmp_path):
    progress_file = str(tmp_path / "p.json")
    sleeps = []

    summary = brt.run_batch_retry(
        _candidates("A1", "B2"), progress_file, "https://x", 20, 25,
        submit_fn=lambda b, s: {"status": "processing", "http_status": 202, "detail": {}},
        sleep_fn=sleeps.append, jitter_fn=lambda: 0, print_fn=lambda m: None,
    )

    assert summary["retried"] == 2
    assert sleeps == [20]  # spacing between the two, none after the last
    progress = brt.load_progress(progress_file)
    assert progress["A1"]["status"] == "processing"
    assert progress["B2"]["status"] == "processing"


def test_rerun_skips_already_retried_rows(tmp_path):
    progress_file = str(tmp_path / "p.json")
    brt.save_progress(progress_file, {"A1": {"status": "processing", "date": "2020-01-01"}})
    submitted = []

    summary = brt.run_batch_retry(
        _candidates("A1", "B2"), progress_file, "https://x", 20, 25,
        submit_fn=lambda b, s: submitted.append(s) or {"status": "processing", "http_status": 202},
        sleep_fn=lambda s: None, jitter_fn=lambda: 0, print_fn=lambda m: None,
    )

    assert submitted == ["B2"]  # A1 skipped
    assert summary["skipped"] == 1


def test_404_recorded_as_error_and_retryable_next_run(tmp_path):
    progress_file = str(tmp_path / "p.json")

    summary = brt.run_batch_retry(
        _candidates("GONE1"), progress_file, "https://x", 20, 25,
        submit_fn=lambda b, s: {"status": "error", "http_status": 404, "detail": "unknown shortcode"},
        sleep_fn=lambda s: None, jitter_fn=lambda: 0, print_fn=lambda m: None,
    )

    assert summary["errors"] == 1
    # error is NOT terminal — a rerun submits it again
    submitted = []
    brt.run_batch_retry(
        _candidates("GONE1"), progress_file, "https://x", 20, 25,
        submit_fn=lambda b, s: submitted.append(s) or {"status": "processing", "http_status": 202},
        sleep_fn=lambda s: None, jitter_fn=lambda: 0, print_fn=lambda m: None,
    )
    assert submitted == ["GONE1"]


def test_daily_cap_stops_early(tmp_path):
    progress_file = str(tmp_path / "p.json")
    submitted = []

    summary = brt.run_batch_retry(
        _candidates("A1", "B2", "C3"), progress_file, "https://x", 20, max_per_day=2,
        submit_fn=lambda b, s: submitted.append(s) or {"status": "processing", "http_status": 202},
        sleep_fn=lambda s: None, jitter_fn=lambda: 0, print_fn=lambda m: None,
    )

    assert submitted == ["A1", "B2"]
    assert summary["stopped_early"] is True
