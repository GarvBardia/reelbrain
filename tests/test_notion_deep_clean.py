"""scripts/notion_deep_clean.py — all mocked (no Notion/Gemini/clock)."""
from datetime import datetime, timedelta, timezone

from scripts import notion_deep_clean as ndc


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def _page(shortcode, *, title="A real title", status="📥 Inbox", topics=(), value_score="3",
          gate_keyword=None, gate_resource=None, created_days_ago=1.0):
    return {
        "id": f"pg-{shortcode}",
        "created_time": _iso(created_days_ago),
        "properties": {
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

def test_pure_noise_row_matches():
    row = _page("NOISE1", topics=(), value_score="3", gate_keyword=None, gate_resource=None)
    cands = ndc.find_archive_candidates([row], {})
    assert cands[0]["shortcode"] == "NOISE1"
    assert "pure noise" in cands[0]["reasons"][0]


def test_noise_row_with_gate_keyword_is_spared():
    row = _page("GATED1", topics=(), value_score="3", gate_keyword="SEND")
    assert ndc.find_archive_candidates([row], {}) == []


def test_noise_row_with_gate_resource_is_spared():
    row = _page("HASRES1", topics=(), value_score="3", gate_resource="https://x.com/r")
    assert ndc.find_archive_candidates([row], {}) == []


def test_noise_row_wrong_value_score_is_spared():
    row = _page("VAL4", topics=(), value_score="4")
    assert ndc.find_archive_candidates([row], {}) == []


def test_noise_row_with_topics_is_spared():
    row = _page("HASTOPICS", topics=("claude-ai",), value_score="3")
    assert ndc.find_archive_candidates([row], {}) == []


# --- Condition 3: stale low-signal ------------------------------------------------

def test_stale_low_signal_matches():
    row = _page("STALE1", status=ndc.LOW_SIGNAL_LABEL, created_days_ago=45, value_score="1")
    cands = ndc.find_archive_candidates([row], {})
    assert cands[0]["shortcode"] == "STALE1"
    assert "stale low-signal" in cands[0]["reasons"][0]


def test_recent_low_signal_is_spared():
    row = _page("FRESH1", status=ndc.LOW_SIGNAL_LABEL, created_days_ago=5, value_score="1")
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

    count = ndc.apply_archive([{"shortcode": "A1", "title": "t", "reasons": ["x"]}])
    assert count == 1
    assert set_calls == [("pg-A1", "archived")]


def test_apply_archive_skips_missing_page(monkeypatch):
    from app import notion_writer

    monkeypatch.setattr(notion_writer, "find_page_by_shortcode", lambda sc: None)
    count = ndc.apply_archive([{"shortcode": "GONE1", "title": "t", "reasons": ["x"]}])
    assert count == 0


# --- Job B: fix_topics -----------------------------------------------------------

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
