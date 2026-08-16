"""scripts/notion_deep_clean.py — all mocked (no Notion/Gemini/clock)."""
from datetime import datetime, timedelta, timezone

from scripts import notion_deep_clean as ndc


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def _page(shortcode, *, title="A real title", status="📥 Inbox", topics=(), value_score="3",
          gate_keyword=None, gate_resource=None, created_days_ago=1.0,
          content_type="unknown", page_id=None, last_edited=""):
    return {
        "id": page_id or f"pg-{shortcode}",
        "created_time": _iso(created_days_ago),
        "last_edited_time": last_edited,
        "properties": {
            "Content type": {"select": {"name": content_type}} if content_type else {},
            "Shortcode": {"rich_text": [{"plain_text": shortcode}]},
            "Title": {"title": [{"plain_text": title}]},
            "Status": {"select": {"name": status}},
            "Topics": {"multi_select": [{"name": t} for t in topics]},
            "Value score": {"select": {"name": value_score}} if value_score else {},
            "Gate keyword": {"rich_text": [{"plain_text": gate_keyword}] if gate_keyword else []},
            "Gate resource": {"url": gate_resource},
            "My note": {"rich_text": []},
            "Reel URL": {"url": f"https://www.instagram.com/reel/{shortcode}/"},
            "Priority": {"select": {"name": "Low"}},
        },
    }


# --- Condition 1: permanent placeholder failure ---------------------------------

def test_permanent_placeholder_failure_requires_exhausted_attempts():
    old_placeholder = _page("PERM1", title=ndc.PLACEHOLDER_TITLE, status=ndc.PHOTO_MANUAL_LABEL,
                            created_days_ago=10)
    progress_exhausted = {"PERM1": {"status": "no_caption", "attempts": 3}}
    cands = ndc.find_archive_candidates([old_placeholder], progress_exhausted)
    assert cands[0]["shortcode"] == "PERM1"
    assert "permanent placeholder failure" in cands[0]["reasons"][0]


def test_placeholder_never_attempted_is_not_archived():
    # every placeholder row has topics=[]/value_score="3"/no gate by
    # construction (see gemini_pipe.degraded_extraction) -- condition 2
    # deliberately excludes placeholder titles so this doesn't get swept up
    # as "pure noise" before the recovery worker ever touches it.
    old_placeholder = _page("NEVER1", title=ndc.PLACEHOLDER_TITLE, status=ndc.PHOTO_MANUAL_LABEL,
                            created_days_ago=10)
    assert ndc.find_archive_candidates([old_placeholder], {}) == []


def test_placeholder_with_attempts_remaining_is_not_archived():
    old_placeholder = _page("RETRY1", title=ndc.PLACEHOLDER_TITLE, status=ndc.PHOTO_MANUAL_LABEL,
                            created_days_ago=10)
    progress_partial = {"RETRY1": {"status": "error", "attempts": 1}}
    assert ndc.find_archive_candidates([old_placeholder], progress_partial) == []


def test_placeholder_recovered_is_not_archived_even_if_old():
    old_placeholder = _page("OK1", title=ndc.PLACEHOLDER_TITLE, status=ndc.PHOTO_MANUAL_LABEL,
                            created_days_ago=10)
    progress_recovered = {"OK1": {"status": "recovered", "attempts": 0}}
    assert ndc.find_archive_candidates([old_placeholder], progress_recovered) == []


def test_placeholder_too_recent_is_not_archived():
    recent_placeholder = _page("RECENT1", title=ndc.PLACEHOLDER_TITLE, status=ndc.PHOTO_MANUAL_LABEL,
                               created_days_ago=2)
    progress_exhausted = {"RECENT1": {"status": "no_caption", "attempts": 3}}
    assert ndc.find_archive_candidates([recent_placeholder], progress_exhausted) == []


# --- Condition 2: pure noise ------------------------------------------------------

def test_raw_caption_dump_row_matches():
    """Condition 2: no topics, no gate, and content_type "unknown" -- the
    marker that extraction degraded, so main_point is a raw caption dump."""
    row = _page("NOISE1", topics=(), gate_keyword=None, gate_resource=None, content_type="unknown")
    cands = ndc.find_archive_candidates([row], {})
    assert cands[0]["shortcode"] == "NOISE1"
    assert "raw caption dump" in cands[0]["reasons"][0]


def test_row_with_real_extraction_is_spared_even_without_topics():
    """A real extraction (any content_type but "unknown") means main_point is
    genuine analysis, not a caption dump -- topics can be backfilled by Job B."""
    row = _page("REAL1", topics=(), gate_keyword=None, gate_resource=None, content_type="tutorial")
    assert ndc.find_archive_candidates([row], {}) == []


def test_failed_retry_row_is_never_archived_by_condition_2():
    """REGRESSION (live): 4 "⚠️ Failed — retry" rows matched condition 2
    literally -- no topics, no gate, no real extraction -- but their fetch
    NEVER SUCCEEDED, so "raw caption dump" really meant "we never got any
    content". Archiving would have silently discarded 4 saved reels the
    pipeline never genuinely tried. recover_placeholders.py owns them now."""
    row = _page("FAILED1", title="https://www.instagram.com/reel/FAILED1/",
                status="⚠️ Failed — retry", topics=(), gate_keyword=None,
                gate_resource=None, content_type="")
    assert ndc.find_archive_candidates([row], {}) == []


def test_photo_manual_row_is_never_archived_by_condition_2():
    """Photo — manual rows belong to condition 1, which has its own
    attempts-tracking. Condition 2 must not short-circuit that."""
    row = _page("PHOTO1", title="some caption text", status=ndc.PHOTO_MANUAL_LABEL,
                topics=(), gate_keyword=None, gate_resource=None, content_type="unknown")
    assert ndc.find_archive_candidates([row], {}) == []


def test_processing_row_is_never_archived_by_condition_2():
    row = _page("PROC1", title="t", status="processing", topics=(),
                gate_keyword=None, gate_resource=None, content_type="unknown")
    assert ndc.find_archive_candidates([row], {}) == []


def test_inbox_row_with_no_real_extraction_is_still_archivable():
    """The condition must still fire for rows the pipeline has genuinely
    finished with -- otherwise the carve-out would disable it entirely."""
    row = _page("DONE1", title="a real caption dump", status="📥 Inbox", topics=(),
                gate_keyword=None, gate_resource=None, content_type="unknown")
    cands = ndc.find_archive_candidates([row], {})
    assert [c["shortcode"] for c in cands] == ["DONE1"]


def test_noise_row_with_gate_keyword_is_spared():
    row = _page("GATED1", topics=(), value_score="3", gate_keyword="SEND")
    assert ndc.find_archive_candidates([row], {}) == []


def test_noise_row_with_gate_resource_is_spared():
    row = _page("HASRES1", topics=(), value_score="3", gate_resource="https://x.com/r")
    assert ndc.find_archive_candidates([row], {}) == []


def test_noise_row_with_empty_content_type_still_matches():
    """A row that never got a Content type at all is equally "no real
    extraction" -- treated the same as "unknown"."""
    row = _page("NOCT1", topics=(), gate_keyword=None, gate_resource=None, content_type="")
    cands = ndc.find_archive_candidates([row], {})
    assert cands[0]["shortcode"] == "NOCT1"


def test_noise_row_with_topics_is_spared():
    row = _page("HASTOPICS", topics=("claude-ai",), value_score="3")
    assert ndc.find_archive_candidates([row], {}) == []


# --- Condition 2b (NEW): duplicate shortcodes, keep the richest -------------------

def test_duplicate_shortcodes_keep_the_row_with_a_gate_resource():
    """Gate resource outranks everything -- it's a DM'd link you'd have to
    re-fetch by hand if the wrong row survived."""
    rich = _page("DUP1", page_id="pg-rich", gate_resource="https://x.com/r", content_type="unknown")
    poor = _page("DUP1", page_id="pg-poor", content_type="tutorial", topics=("a", "b", "c"))
    losers = ndc.find_duplicate_shortcode_losers([poor, rich])
    assert [l[1]["id"] for l in losers] == ["pg-poor"]
    assert losers[0][2]["id"] == "pg-rich"


def test_duplicate_shortcodes_prefer_real_extraction_over_topic_count():
    real = _page("DUP2", page_id="pg-real", content_type="tutorial")
    degraded = _page("DUP2", page_id="pg-degraded", content_type="unknown", topics=("a", "b", "c", "d"))
    losers = ndc.find_duplicate_shortcode_losers([degraded, real])
    assert [l[1]["id"] for l in losers] == ["pg-degraded"]


def test_duplicate_shortcodes_break_final_tie_on_recency():
    older = _page("DUP3", page_id="pg-old", content_type="tutorial", last_edited="2026-07-01T00:00:00Z")
    newer = _page("DUP3", page_id="pg-new", content_type="tutorial", last_edited="2026-07-20T00:00:00Z")
    losers = ndc.find_duplicate_shortcode_losers([older, newer])
    assert [l[1]["id"] for l in losers] == ["pg-old"]


def test_three_way_duplicate_archives_two_keeps_one():
    a = _page("DUP4", page_id="pg-a", content_type="tutorial", gate_resource="https://x/r")
    b = _page("DUP4", page_id="pg-b", content_type="tutorial")
    c = _page("DUP4", page_id="pg-c", content_type="unknown")
    losers = ndc.find_duplicate_shortcode_losers([a, b, c])
    assert sorted(l[1]["id"] for l in losers) == ["pg-b", "pg-c"]


def test_unique_shortcodes_produce_no_duplicate_losers():
    pages = [_page("U1", content_type="tutorial"), _page("U2", content_type="tutorial")]
    assert ndc.find_duplicate_shortcode_losers(pages) == []


def test_duplicate_condition_surfaces_in_candidates_keyed_by_page_id():
    """Candidates are keyed by page id, not shortcode -- two rows share a
    shortcode here and must stay distinguishable."""
    rich = _page("DUP5", page_id="pg-rich", content_type="tutorial", topics=("a",))
    poor = _page("DUP5", page_id="pg-poor", content_type="tutorial", topics=("a",))
    cands = ndc.find_archive_candidates([rich, poor], {})
    assert len(cands) == 1
    assert cands[0]["page_id"] in ("pg-rich", "pg-poor")
    assert "duplicate shortcode" in cands[0]["reasons"][0]


def test_apply_archive_targets_page_id_not_shortcode_lookup(monkeypatch):
    """REGRESSION: archiving by find_page_by_shortcode would return the FIRST
    match, which for a duplicate pair can be the row we chose to KEEP."""
    from app import notion_writer, store

    set_calls = []
    monkeypatch.setattr(notion_writer, "set_status", lambda pid, st: set_calls.append((pid, st)))
    monkeypatch.setattr(store, "update_save", lambda sc, **kw: None)
    monkeypatch.setattr(notion_writer, "find_page_by_shortcode",
                        lambda sc: (_ for _ in ()).throw(AssertionError("must not lookup by shortcode")))

    ndc.apply_archive([{"page_id": "pg-loser", "shortcode": "DUP1", "title": "t",
                        "reasons": ["duplicate shortcode -- keeping the richer row"]}])
    assert set_calls == [("pg-loser", "archived")]


def test_duplicate_loser_does_not_mark_shortcode_archived_locally(monkeypatch):
    """The shortcode still has a live winner row, so flipping it to archived
    in local SQLite would misrepresent the survivor."""
    from app import notion_writer, store

    local_updates = []
    monkeypatch.setattr(notion_writer, "set_status", lambda pid, st: None)
    monkeypatch.setattr(store, "update_save", lambda sc, **kw: local_updates.append((sc, kw)))

    ndc.apply_archive([{"page_id": "pg-loser", "shortcode": "DUP1", "title": "t",
                        "reasons": ["duplicate shortcode -- keeping the richer row"]}])
    assert local_updates == []

    ndc.apply_archive([{"page_id": "pg-x", "shortcode": "SOLO1", "title": "t",
                        "reasons": ["stale low-signal (45d old)"]}])
    assert local_updates == [("SOLO1", {"status": "archived"})]


# --- Condition 3: stale low-signal ------------------------------------------------

def test_stale_low_signal_matches():
    row = _page("STALE1", status=ndc.LOW_SIGNAL_LABEL, created_days_ago=45, value_score="1",
                content_type="entertainment", topics=("humor",))
    cands = ndc.find_archive_candidates([row], {})
    assert cands[0]["shortcode"] == "STALE1"
    assert "stale low-signal" in cands[0]["reasons"][0]


def test_recent_low_signal_is_spared():
    row = _page("FRESH1", status=ndc.LOW_SIGNAL_LABEL, created_days_ago=5, value_score="1",
                content_type="entertainment", topics=("humor",))
    assert ndc.find_archive_candidates([row], {}) == []


# --- Condition 4: near-duplicate-only, no real content ---------------------------

def test_near_dup_only_thin_body_matches():
    row = _page("NEARDUP1", topics=("near-duplicate",), value_score="3")
    cands = ndc.find_archive_candidates([row], {}, now_check_body_fn=lambda c, pid: True, client=object())
    assert cands[0]["shortcode"] == "NEARDUP1"
    assert "near-duplicate-only" in cands[0]["reasons"][0]


def test_near_dup_only_real_content_is_spared():
    row = _page("NEARDUP2", topics=("near-duplicate",), value_score="3")
    cands = ndc.find_archive_candidates([row], {}, now_check_body_fn=lambda c, pid: False, client=object())
    assert cands == []


def test_near_dup_only_placeholder_row_is_spared_by_condition_4_too():
    # real-world case found in the live scan: several never-recovered
    # placeholder rows embed as near-duplicates of EACH OTHER (same generic
    # fallback text), so a literal reading of condition 4 would archive them
    # immediately -- same conflict with condition 1's attempts-tracking as
    # the pure-noise condition. Must be excluded here too.
    row = _page("PLACENEARDUP1", title=ndc.PLACEHOLDER_TITLE, status=ndc.PHOTO_MANUAL_LABEL,
                topics=("near-duplicate",), value_score="3")
    cands = ndc.find_archive_candidates([row], {}, now_check_body_fn=lambda c, pid: True, client=object())
    assert cands == []


def test_near_dup_plus_other_topic_is_spared():
    row = _page("NEARDUP3", topics=("near-duplicate", "claude-ai"), value_score="3")
    cands = ndc.find_archive_candidates([row], {}, now_check_body_fn=lambda c, pid: True, client=object())
    assert cands == []


def test_page_body_is_thin_detects_bulleted_supporting_points():
    client = type("C", (), {})()
    client.blocks = type("B", (), {})()
    client.blocks.children = type("Ch", (), {})()
    client.blocks.children.list = lambda block_id: {"results": [{"type": "bulleted_list_item"}]}
    assert ndc._page_body_is_thin(client, "pg1") is False


def test_page_body_is_thin_true_when_no_bullets_and_empty_transcript():
    calls = {"n": 0}

    def children_list(block_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"results": [{"type": "toggle", "id": "tg1",
                                 "toggle": {"rich_text": [{"plain_text": "Transcript"}]}}]}
        return {"results": [{"type": "paragraph",
                            "paragraph": {"rich_text": [{"plain_text": "(no speech detected)"}]}}]}

    client = type("C", (), {})()
    client.blocks = type("B", (), {})()
    client.blocks.children = type("Ch", (), {})()
    client.blocks.children.list = children_list
    assert ndc._page_body_is_thin(client, "pg1") is True


# --- multiple conditions on one row -> multiple reasons, no dupes ---------------

def test_row_matching_multiple_conditions_lists_all_reasons_once():
    row = _page("MULTI1", status=ndc.LOW_SIGNAL_LABEL, created_days_ago=45,
                topics=(), value_score="3", gate_keyword=None, gate_resource=None)
    cands = ndc.find_archive_candidates([row], {})
    assert len(cands) == 1
    assert len(cands[0]["reasons"]) == 2
    assert len(set(cands[0]["reasons"])) == 2  # no duplicate reasons


# --- apply_archive: sets status, never deletes -----------------------------------

def test_apply_archive_sets_status_never_deletes(monkeypatch):
    from app import notion_writer, store

    set_calls = []
    monkeypatch.setattr(notion_writer, "find_page_by_shortcode", lambda sc: {"id": f"pg-{sc}"})
    monkeypatch.setattr(notion_writer, "set_status", lambda page_id, status: set_calls.append((page_id, status)))
    monkeypatch.setattr(store, "update_save", lambda sc, **kw: None)

    count = ndc.apply_archive([{"page_id": "pg-A1", "shortcode": "A1", "title": "t", "reasons": ["x"]}])
    assert count == 1
    assert set_calls == [("pg-A1", "archived")]


def test_apply_archive_skips_candidate_with_no_page_id():
    count = ndc.apply_archive([{"page_id": "", "shortcode": "GONE1", "title": "t", "reasons": ["x"]}])
    assert count == 0


# --- Job B: fix_topics -----------------------------------------------------------

def test_find_topicless_rows_excludes_bare_permalink_titles():
    """REGRESSION (live): a Failed—retry row's Title is still its raw
    permalink, so Gemini tagged the URL itself -- 'instagram', 'reels',
    'short-form-video'. Had to be reverted by hand. Same bug class as the
    placeholder case below."""
    pages = [
        _page("REAL1", title="A real extracted title", topics=()),
        _page("FAILED1", title="https://www.instagram.com/reel/FAILED1/", topics=()),
    ]
    rows = ndc.find_topicless_rows(pages)
    assert [r["shortcode"] for r in rows] == ["REAL1"]


def test_find_topicless_rows_keeps_row_whose_title_merely_contains_a_link():
    """The permalink guard requires the row's OWN shortcode in the URL, so a
    genuine extraction that happens to start with a link isn't dropped."""
    pages = [_page("REAL2", title="https://example.com is the tool they recommend", topics=())]
    assert [r["shortcode"] for r in ndc.find_topicless_rows(pages)] == ["REAL2"]


def test_find_topicless_rows_excludes_placeholders():
    pages = [
        _page("A1", title="A real title", topics=()),
        _page("PLACE1", title=ndc.PLACEHOLDER_TITLE, topics=()),
        _page("HASTOPICS1", title="Another", topics=("claude-ai",)),
    ]
    rows = ndc.find_topicless_rows(pages)
    assert [r["shortcode"] for r in rows] == ["A1"]


def test_fix_topics_writes_tags_and_priority(monkeypatch):
    from app import notion_writer

    updates = []
    fake_client = type("C", (), {"pages": type("P", (), {"update": staticmethod(
        lambda page_id, properties: updates.append((page_id, properties))
    )})()})()
    monkeypatch.setattr(notion_writer, "_client", lambda: fake_client)

    rows = [{"shortcode": "A1", "page_id": "pg-A1", "title": "Claude MCP servers guide"}]
    result = ndc.fix_topics(rows, ["claude-ai"], suggest_fn=lambda t, tax: ["claude-ai", "mcp-servers"])
    assert result["fixed"] == ["A1"]
    page_id, props = updates[0]
    assert page_id == "pg-A1"
    assert props["Topics"]["multi_select"] == [{"name": "claude-ai"}, {"name": "mcp-servers"}]
    assert props["Priority"]["select"]["name"] == "High"  # claude-ai matches a CLAUDE_KEYWORD


def test_fix_topics_stops_cleanly_on_quota(monkeypatch):
    from app import notion_writer

    monkeypatch.setattr(notion_writer, "_client", lambda: type("C", (), {})())

    def boom(title, tax):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    rows = [{"shortcode": "Q1", "page_id": "pg-Q1", "title": "t"},
            {"shortcode": "NEVER1", "page_id": "pg-N1", "title": "t"}]
    attempted = []
    result = ndc.fix_topics(
        rows, [],
        suggest_fn=lambda t, tax: attempted.append(t) or boom(t, tax),
    )
    assert attempted == ["t"]  # only the first row attempted
    assert result["quota_stopped"] is True
    assert result["fixed"] == []


def test_fix_topics_stops_cleanly_on_ollama_unavailable(monkeypatch):
    """Phase 5 hard boundary (PROGRESS.md 2026-08-16): Ollama down must stop
    the whole batch cleanly, never fall back to Gemini."""
    from app import notion_writer
    from app.local_llm import OllamaUnavailable

    monkeypatch.setattr(notion_writer, "_client", lambda: type("C", (), {})())

    def boom(title, tax):
        raise OllamaUnavailable("ollama not running")

    rows = [{"shortcode": "O1", "page_id": "pg-O1", "title": "t"},
            {"shortcode": "NEVER1", "page_id": "pg-N1", "title": "t"}]
    attempted = []
    result = ndc.fix_topics(
        rows, [],
        suggest_fn=lambda t, tax: attempted.append(t) or boom(t, tax),
    )
    assert attempted == ["t"]  # only the first row attempted
    assert result["quota_stopped"] is True
    assert result["fixed"] == []


def test_suggest_tags_is_routed_as_a_local_task():
    """PROGRESS.md 2026-08-16: this is one of the five tasks moved to local
    Ollama specifically to stop costing Gemini quota."""
    from app import llm_router

    assert llm_router.provider_for("notion_deep_clean_tagging") == llm_router.LOCAL


def test_suggest_tags_routes_through_llm_router(monkeypatch):
    from app import llm_router

    captured = {}

    def _fake_generate_text(task, prompt, **kwargs):
        captured["task"] = task
        captured["prompt"] = prompt
        return "claude-ai, developer-tools"

    monkeypatch.setattr(llm_router, "generate_text", _fake_generate_text)

    tags = ndc.suggest_tags("Claude MCP servers guide", ["claude-ai"])

    assert tags == ["claude-ai", "developer-tools"]
    assert captured["task"] == "notion_deep_clean_tagging"
    assert "Claude MCP servers guide" in captured["prompt"]


def test_fix_topics_skips_row_with_no_tags_returned(monkeypatch):
    from app import notion_writer

    updates = []
    fake_client = type("C", (), {"pages": type("P", (), {"update": staticmethod(
        lambda page_id, properties: updates.append((page_id, properties))
    )})()})()
    monkeypatch.setattr(notion_writer, "_client", lambda: fake_client)

    rows = [{"shortcode": "EMPTY1", "page_id": "pg-E1", "title": "t"}]
    result = ndc.fix_topics(rows, [], suggest_fn=lambda t, tax: [])
    assert result["fixed"] == []
    assert updates == []
