"""Phase H: no reel may exist without topics. All mocked."""
from app import gemini_pipe, notion_writer, topic_guarantee

tg = topic_guarantee  # short alias used by the newer cases below
from app.models import Extraction, ReelData
from scripts import enforce_topics


# --- fallback derivation ------------------------------------------------------------

def test_fallback_uses_named_entities_first():
    tags = topic_guarantee.derive_fallback_tags(["Firecrawl", "Claude Code"], "tutorial")
    assert tags[:2] == ["firecrawl", "claude-code"]
    assert "tutorials" in tags


def test_fallback_uses_content_type_when_no_entities():
    assert topic_guarantee.derive_fallback_tags([], "resource_drop") == ["resource-sharing"]


def test_fallback_is_honest_when_there_is_nothing():
    """Never invents a subject — 'uncategorized' is a truthful label AND is
    what enforce_topics looks for later."""
    assert topic_guarantee.derive_fallback_tags([], "") == [topic_guarantee.UNCATEGORIZED_TAG]
    assert topic_guarantee.derive_fallback_tags([], "unknown") == [topic_guarantee.UNCATEGORIZED_TAG]


def test_fallback_drops_junk_entities():
    tags = topic_guarantee.derive_fallback_tags(["7", "x", "GSAP"], "")
    assert tags == ["gsap"]


def test_fallback_respects_max_topics():
    tags = topic_guarantee.derive_fallback_tags([f"Entity{i}" for i in range(20)], "tutorial")
    assert len(tags) <= topic_guarantee.MAX_TOPICS


def test_ensure_topics_passes_real_tags_through_untouched():
    extraction = Extraction(main_point="x", topic_tags=["claude-ai", "mcp-servers"])
    assert topic_guarantee.ensure_topics(extraction) == ["claude-ai", "mcp-servers"]


# --- the write guard (this is what makes it structural) ------------------------------

def _reel():
    return ReelData(shortcode="TG1", permalink="https://www.instagram.com/reel/TG1/")


def test_writer_never_persists_empty_topics():
    extraction = Extraction(main_point="x", named_entities=["Firecrawl"], content_type="tutorial")
    props = notion_writer._build_properties(_reel(), extraction, "done", None, None, None)
    names = [o["name"] for o in props["Topics"]["multi_select"]]
    assert names, "the guard must never allow an empty Topics write"
    assert names == ["firecrawl", "tutorials"]


def test_writer_falls_back_to_uncategorized_rather_than_empty():
    props = notion_writer._build_properties(_reel(), Extraction(main_point="x"), "done", None, None, None)
    assert [o["name"] for o in props["Topics"]["multi_select"]] == ["uncategorized"]


def test_writer_leaves_real_topics_alone():
    extraction = Extraction(main_point="x", topic_tags=["claude-ai"])
    props = notion_writer._build_properties(_reel(), extraction, "done", None, None, None)
    assert [o["name"] for o in props["Topics"]["multi_select"]] == ["claude-ai"]


def test_writer_logs_loudly_when_it_has_to_intervene(caplog):
    import logging
    with caplog.at_level(logging.ERROR):
        notion_writer._build_properties(_reel(), Extraction(main_point="x"), "done", None, None, None)
    assert any("REFUSING to write" in r.message for r in caplog.records)


# --- the extraction retry ------------------------------------------------------------

def test_retry_fires_only_when_topics_empty():
    calls = []
    empty = Extraction(main_point="x", topic_tags=[])
    good = '{"main_point": "x", "topic_tags": ["claude-ai", "mcp-servers"], "value_score": 3, "content_type": "tutorial"}'
    result = gemini_pipe._retry_if_topics_empty(
        empty, "PROMPT", lambda p: calls.append(p) or good)
    assert result.topic_tags == ["claude-ai", "mcp-servers"]
    assert len(calls) == 1
    assert gemini_pipe.EMPTY_TOPICS_RETRY_INSTRUCTION in calls[0]


def test_retry_does_not_fire_when_topics_present():
    calls = []
    filled = Extraction(main_point="x", topic_tags=["claude-ai"])
    result = gemini_pipe._retry_if_topics_empty(filled, "P", lambda p: calls.append(p) or "{}")
    assert calls == [] and result is filled


def test_retry_failure_keeps_original_and_never_raises():
    empty = Extraction(main_point="x", topic_tags=[])

    def boom(prompt):
        raise RuntimeError("gemini down")

    result = gemini_pipe._retry_if_topics_empty(empty, "P", boom)
    assert result is empty  # guard still applies the fallback downstream


def test_retry_returning_empty_again_keeps_original():
    empty = Extraction(main_point="x", topic_tags=[])
    still_empty = '{"main_point": "x", "topic_tags": [], "value_score": 3, "content_type": "tutorial"}'
    result = gemini_pipe._retry_if_topics_empty(empty, "P", lambda p: still_empty)
    assert result.topic_tags == []


# --- enforce_topics sweep -------------------------------------------------------------

def _page(shortcode, topics=(), entities=(), content_type="tutorial"):
    return {
        "id": f"pg-{shortcode}",
        "properties": {
            "Shortcode": {"rich_text": [{"plain_text": shortcode}]},
            "Title": {"title": [{"plain_text": "T"}]},
            "Status": {"select": {"name": "📥 Inbox"}},
            "Topics": {"multi_select": [{"name": t} for t in topics]},
            "Named entities": {"multi_select": [{"name": e} for e in entities]},
            "Content type": {"select": {"name": content_type}},
        },
    }


def test_sweep_finds_only_topicless_rows():
    pages = [_page("HAS1", topics=("claude-ai",)), _page("EMPTY1", entities=("GSAP",))]
    assert [r["shortcode"] for r in enforce_topics.find_topicless_rows(pages)] == ["EMPTY1"]


def test_sweep_writes_derived_tags():
    writes = []
    rows = [{"shortcode": "E1", "page_id": "pg", "title": "T",
             "named_entities": ["GSAP"], "content_type": "tutorial"}]
    summary = enforce_topics.run_enforce(rows, write_fn=lambda p, t: writes.append((p, t)),
                                         print_fn=lambda m: None)
    assert writes == [("pg", ["gsap", "tutorials"])]
    assert summary["fixed"] == 1 and summary["uncategorized"] == 0


def test_sweep_counts_uncategorized_separately():
    rows = [{"shortcode": "E1", "page_id": "pg", "title": "T",
             "named_entities": [], "content_type": ""}]
    summary = enforce_topics.run_enforce(rows, write_fn=lambda p, t: None, print_fn=lambda m: None)
    assert summary["uncategorized"] == 1


def test_sweep_dry_run_writes_nothing():
    writes = []
    rows = [{"shortcode": "E1", "page_id": "pg", "title": "T",
             "named_entities": ["GSAP"], "content_type": "tutorial"}]
    enforce_topics.run_enforce(rows, dry_run=True, write_fn=lambda p, t: writes.append(p),
                               print_fn=lambda m: None)
    assert writes == []


def test_sweep_continues_past_a_failing_row():
    def flaky(page_id, topics):
        if page_id == "bad":
            raise RuntimeError("notion down")

    rows = [{"shortcode": "B1", "page_id": "bad", "title": "T", "named_entities": [], "content_type": ""},
            {"shortcode": "G1", "page_id": "ok", "title": "T", "named_entities": ["GSAP"], "content_type": ""}]
    summary = enforce_topics.run_enforce(rows, write_fn=flaky, print_fn=lambda m: None)
    assert summary == {"fixed": 1, "errors": 1, "uncategorized": 0, "total_rows": 2}


# --- degraded extraction is NOT "nothing to categorize" ------------------------------

def test_row_with_real_content_but_no_entities_is_pending_not_uncategorized():
    """REGRESSION (2026-07-31): a degraded extraction yields no entities and
    content_type='unknown', which used to resolve to 'uncategorized' — recording
    a transient failure as a permanent verdict. 19 rows with captions up to 295
    words were mislabelled this way."""
    tags = tg.derive_fallback_tags([], "unknown", has_content=True)
    assert tags == [tg.PENDING_EXTRACTION_TAG]


def test_row_with_genuinely_nothing_stays_uncategorized():
    assert tg.derive_fallback_tags([], "unknown", has_content=False) == [tg.UNCATEGORIZED_TAG]


def test_has_real_content_rejects_the_placeholder():
    assert not tg.has_real_content(tg.PLACEHOLDER_MAIN_POINT)


def test_has_real_content_rejects_a_too_thin_caption():
    assert not tg.has_real_content("short one")


def test_has_real_content_accepts_a_substantial_caption():
    assert tg.has_real_content(" ".join(["word"] * 12))


def test_entities_still_win_over_any_marker():
    tags = tg.derive_fallback_tags(["LangGraph"], "tutorial", has_content=True)
    assert tg.PENDING_EXTRACTION_TAG not in tags and tg.UNCATEGORIZED_TAG not in tags


def test_ensure_topics_marks_a_degraded_extraction_as_pending():
    from app.models import degraded_extraction

    extraction = degraded_extraction(" ".join(["real"] * 30))
    assert tg.ensure_topics(extraction) == [tg.PENDING_EXTRACTION_TAG]


def test_ensure_topics_marks_an_empty_degraded_extraction_uncategorized():
    from app.models import degraded_extraction

    assert tg.ensure_topics(degraded_extraction("")) == [tg.UNCATEGORIZED_TAG]
