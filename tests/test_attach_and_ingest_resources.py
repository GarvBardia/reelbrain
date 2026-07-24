"""scripts/attach_and_ingest_resources.py — all mocked (no network/Gemini/Notion/clock)."""
from pathlib import Path

from app.models import ResourceExtraction
from scripts import attach_and_ingest_resources as air


# --- URL normalization + dedup --------------------------------------------------

def test_normalize_strips_query_and_case():
    a = "https://github.com/oso95/scroll-world?mcp_token=abc&fbclid=x"
    b = "https://GitHub.com/oso95/scroll-world/"
    assert air.normalize_url(a) == air.normalize_url(b) == "github.com/oso95/scroll-world"


def test_dedupe_collapses_same_resource_different_tracking():
    urls = [
        "https://github.com/a/b?fbclid=1",
        "https://github.com/a/b?fbclid=2",
        "https://docs.google.com/document/d/XYZ/mobilebasic",
    ]
    unique, dups = air.dedupe(urls)
    assert len(unique) == 2
    assert dups["github.com/a/b"] == 1


# --- Part 1: confident-threshold logic ------------------------------------------

def test_classify_match_attaches_on_specific_keyword_win():
    ranked = [
        {"shortcode": "AAA", "match_score": air.CONFIDENT_ATTACH_SCORE + 1,
         "title": "t", "gate_keyword": "PROMPTS"},
        {"shortcode": "BBB", "match_score": 2, "title": "t", "gate_keyword": "x"},
    ]
    decision, top = air.classify_match(ranked, "a page all about claude prompts and workflows")
    assert decision == "attach"
    assert top["shortcode"] == "AAA"


def test_classify_match_rejects_generic_keyword_even_at_high_score():
    # the live false positive: gate_keyword "FREE" present, high score, but
    # "free" is content-free marketing noise -> must NOT auto-attach.
    ranked = [{"shortcode": "AAA", "match_score": 11, "title": "t", "gate_keyword": "FREE"}]
    assert air.classify_match(ranked, "get your free guide now, totally free") == ("unmatched", None)


def test_classify_match_rejects_specific_keyword_not_present_in_resource():
    ranked = [{"shortcode": "AAA", "match_score": air.CONFIDENT_ATTACH_SCORE,
               "title": "t", "gate_keyword": "STACK"}]
    assert air.classify_match(ranked, "an article that never mentions the keyword") == ("unmatched", None)


def test_classify_match_rejects_generic_overlap_below_keyword_weight():
    ranked = [{"shortcode": "AAA", "match_score": 3, "title": "t", "gate_keyword": "STACK"}]
    assert air.classify_match(ranked, "mentions stack once") == ("unmatched", None)


def test_classify_match_rejects_two_keyword_tie_as_ambiguous():
    ranked = [
        {"shortcode": "AAA", "match_score": air.CONFIDENT_ATTACH_SCORE, "title": "t", "gate_keyword": "STACK"},
        {"shortcode": "BBB", "match_score": air.CONFIDENT_ATTACH_SCORE, "title": "t", "gate_keyword": "STACK"},
    ]
    assert air.classify_match(ranked, "stack stack stack") == ("unmatched", None)


def test_classify_match_empty_is_unmatched():
    assert air.classify_match([], "") == ("unmatched", None)


# --- run(): skip-if-already-attached --------------------------------------------

def _extraction():
    return ResourceExtraction(
        summary="A real summary of the resource.", key_takeaways=["one", "two"],
        topic_tags=["claude-ai", "developer-tools"], resource_kind="github_repo",
        suggested_action="Clone the repo and run the demo",
    )


def _run(tmp_path, urls, **over):
    defaults = dict(
        dry_run=False, candidates=[], attached_map={}, reel_stems={}, taxonomy=[],
        fetch_fn=lambda url, kind: ("word " * 60, None),
        classify_fn=lambda url: "web_article",
        extract_fn=lambda *a: _extraction(),
        commit_fn=lambda sc, url: True,
        audit_fn=lambda *a, **k: None,
        sleep_fn=lambda s: None, print_fn=lambda *a, **k: None,
    )
    defaults.update(over)
    return air.run(urls, tmp_path, tmp_path / "prog.json", **defaults)


def test_already_attached_is_never_reattached_but_still_ingested(tmp_path):
    (tmp_path / "resources").mkdir()
    commits = []
    res = _run(
        tmp_path, ["https://github.com/a/b?fbclid=1"],
        attached_map={"github.com/a/b": "REEL1"},
        reel_stems={"REEL1": "2026-07-20-REEL1"},
        commit_fn=lambda sc, url: commits.append(sc) or True,
    )
    assert commits == []                              # never re-attached
    assert res["already"][0]["shortcode"] == "REEL1"
    assert len(res["ingested"]) == 1                  # still ingested
    # matched -> note carries the reel backlink
    note = next((tmp_path / "resources").glob("*.md")).read_text(encoding="utf-8")
    assert "source_shortcode: REEL1" in note
    assert "[[reels/2026-07-20-REEL1]]" in note


def test_confident_match_attaches_and_links(tmp_path):
    (tmp_path / "resources").mkdir()
    candidates = [{"shortcode": "GATE1", "title": "claude mcp servers", "note": "",
                   "topics": ["claude-ai"], "gate_keyword": "STACK", "created_at": "2026-07-20"}]
    committed = []
    res = _run(
        tmp_path, ["https://x.com/guide"],
        candidates=candidates, reel_stems={"GATE1": "2026-07-20-GATE1"},
        # content contains the gate keyword STACK verbatim -> score >= weight
        fetch_fn=lambda url, kind: ("Install the full STACK of claude mcp servers now", None),
        commit_fn=lambda sc, url: committed.append((sc, url)) or True,
    )
    assert committed == [("GATE1", "https://x.com/guide")]
    assert res["attached"][0]["shortcode"] == "GATE1"


def test_unmatched_goes_to_report_not_attached(tmp_path):
    (tmp_path / "resources").mkdir()
    candidates = [{"shortcode": "GATE1", "title": "cooking pasta recipes", "note": "",
                   "topics": ["food"], "gate_keyword": "PASTA", "created_at": "2026-07-20"}]
    committed = []
    res = _run(
        tmp_path, ["https://x.com/ai-guide"],
        candidates=candidates,
        fetch_fn=lambda url, kind: ("an unrelated article about quantum computing", None),
        commit_fn=lambda sc, url: committed.append(sc) or True,
    )
    assert committed == []
    assert len(res["unmatched"]) == 1
    assert res["unmatched"][0]["url"] == "https://x.com/ai-guide"


# --- unreadable flagging ---------------------------------------------------------

def test_unreadable_is_flagged_never_fabricated(tmp_path):
    (tmp_path / "resources").mkdir()
    extract_calls = []
    res = _run(
        tmp_path, ["https://drive.google.com/file/d/X/view"],
        classify_fn=lambda url: "google_drive_file",
        fetch_fn=lambda url, kind: (None, "requires login — manual review needed"),
        extract_fn=lambda *a: extract_calls.append(a) or _extraction(),
    )
    assert extract_calls == []                        # never summarized
    assert res["unreadable"][0]["reason"] == "requires login — manual review needed"
    assert list((tmp_path / "resources").glob("*.md")) == []   # nothing written


# --- vault note shape -----------------------------------------------------------

def test_note_shape_has_requested_frontmatter():
    note = air.build_resource_note(
        url="https://x.com/g", fetched_title="A Guide", source_type="web",
        extraction=_extraction(), matched_shortcode=None, reel_stem=None,
        date_ingested="2026-07-24",
    )
    for key in ("url:", "fetched_title:", "topic_tags:", "suggested_action:",
                "source_type: web", "date_ingested: 2026-07-24"):
        assert key in note
    assert "## Summary" in note and "## Key takeaways" in note and "## Do" in note
    assert "source_shortcode" not in note              # unmatched -> no backlink


def test_note_informational_action_omits_do_section():
    ext = _extraction()
    ext.suggested_action = "none — informational"
    note = air.build_resource_note(
        url="u", fetched_title="t", source_type="web", extraction=ext,
        matched_shortcode=None, reel_stem=None, date_ingested="2026-07-24",
    )
    assert "## Do" not in note


# --- _index.md unlinked block ---------------------------------------------------

def test_index_unlinked_block_is_idempotent(tmp_path):
    index = tmp_path / "_index.md"
    index.write_text("# Topics Index\n\nexisting content\n", encoding="utf-8")
    air.update_index_unlinked(index, [{"stem": "a-123456", "url": "https://x.com/a"}])
    first = index.read_text(encoding="utf-8")
    assert "## Unlinked resources" in first
    assert "[[resources/a-123456]]" in first
    assert "existing content" in first                # pre-existing content preserved
    # re-run with a different set -> single block, replaced not duplicated
    air.update_index_unlinked(index, [{"stem": "b-654321", "url": "https://x.com/b"}])
    second = index.read_text(encoding="utf-8")
    assert second.count(air._INDEX_START) == 1
    assert "b-654321" in second and "a-123456" not in second


# --- resume: terminal statuses are skipped --------------------------------------

def test_rerun_skips_already_ingested(tmp_path):
    (tmp_path / "resources").mkdir()
    prog = tmp_path / "prog.json"
    import json
    prog.write_text(json.dumps({"x.com/done": {"ingest_status": "ingested"}}), encoding="utf-8")
    fetches = []
    _run(tmp_path, ["https://x.com/done"], fetch_fn=lambda u, k: fetches.append(u) or ("w " * 60, None))
    assert fetches == []                               # terminal -> not re-fetched


# --- attach-only mode: zero Gemini calls, no note writes ------------------------

def test_attach_only_commits_attach_but_skips_ingest(tmp_path):
    (tmp_path / "resources").mkdir()
    candidates = [{"shortcode": "GATE1", "title": "claude mcp servers", "note": "",
                   "topics": ["claude-ai"], "gate_keyword": "STACK", "created_at": "2026-07-20"}]
    committed = []
    extract_calls = []
    res = _run(
        tmp_path, ["https://x.com/guide"], attach_only=True,
        candidates=candidates, reel_stems={"GATE1": "2026-07-20-GATE1"},
        fetch_fn=lambda url, kind: ("Install the full STACK of claude mcp servers now", None),
        commit_fn=lambda sc, url: committed.append((sc, url)) or True,
        extract_fn=lambda *a: extract_calls.append(a) or _extraction(),
    )
    assert committed == [("GATE1", "https://x.com/guide")]   # attach still happens
    assert res["attached"][0]["shortcode"] == "GATE1"
    assert extract_calls == []                                # zero Gemini calls
    assert res["ingested"] == []
    assert list((tmp_path / "resources").glob("*.md")) == []  # no note written


def test_attach_only_does_not_mark_ingest_status(tmp_path):
    (tmp_path / "resources").mkdir()
    _run(tmp_path, ["https://x.com/a"], attach_only=True)
    # nothing marked ingested/unreadable -> a later full run still processes it
    prog = tmp_path / "prog.json"
    import json
    saved = json.loads(prog.read_text(encoding="utf-8")) if prog.exists() else {}
    assert saved.get("x.com/a", {}).get("ingest_status") != "ingested"


# --- Gemini 429 stops the run cleanly -------------------------------------------

def test_quota_stop_halts_and_reports(tmp_path):
    import logging as _logging
    (tmp_path / "resources").mkdir()

    def extract_that_429s(*a):
        _logging.getLogger("reelbrain.gemini").warning("call failed: 429 RESOURCE_EXHAUSTED")
        return None

    res = _run(
        tmp_path, ["https://x.com/a", "https://x.com/b"],
        extract_fn=extract_that_429s,
    )
    assert res["quota_stopped"] is True
    assert len(res["ingested"]) == 0
