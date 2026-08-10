"""app/taxonomy.py + scripts/apply_taxonomy.py — the merge transform and its
priority-invariance guarantee. All pure/mocked: no Notion, no filesystem writes
except tmp_path."""
from app import taxonomy as tx
from app.gemini_pipe import compute_priority
from scripts import apply_taxonomy as at


# --- the merge transform -------------------------------------------------------------

def test_apply_merges_rewrites_a_merged_tag():
    assert tx.apply_merges(["claude", "web-design"]) == ["claude-ai", "web-design"]


def test_apply_merges_collapses_duplicate_when_target_already_present():
    # claude -> claude-ai, but claude-ai is already there: tag count shrinks, and
    # that is correct — this is why row tag-counts can drop while rows cannot.
    assert tx.apply_merges(["claude", "claude-ai"]) == ["claude-ai"]


def test_apply_merges_preserves_order_and_leaves_unmerged_tags():
    assert tx.apply_merges(["web-design", "ai-workflow", "seo-optimization"]) == \
        ["web-design", "ai-workflows", "seo-optimization"]


def test_apply_merges_is_a_noop_for_rows_with_no_merged_tags():
    topics = ["obsidian", "second-brain"]
    assert tx.apply_merges(topics) == topics


def test_protected_tags_are_never_merge_sources():
    # mcp-servers/fable-5/agentic-os must stay tags (proposal §3) -- merging them
    # would silently demote/promote priority.
    assert not (tx.PROTECTED_TAGS & set(tx.MERGES))


def test_proposal_declares_exactly_twenty_merges():
    assert len(tx.MERGES) == 20


# --- write-time singular/plural canonicalization (2026-08-09 taxonomy-collapse fix) --

def test_canonicalize_plurals_rewrites_singular_to_existing_canonical_plural():
    # "startups" is already canonical (business-building parent); "startup" is not.
    assert tx.canonicalize_plurals(["startup"]) == ["startups"]


def test_canonicalize_plurals_rewrites_hyphenated_singular():
    # "mcp-servers" is canonical (claude-ecosystem parent); "mcp-server" is not.
    assert tx.canonicalize_plurals(["mcp-server"]) == ["mcp-servers"]


def test_canonicalize_plurals_dedupes_when_both_spellings_present():
    assert tx.canonicalize_plurals(["startup", "startups"]) == ["startups"]


def test_canonicalize_plurals_leaves_tag_with_no_canonical_match_untouched():
    assert tx.canonicalize_plurals(["some-genuinely-new-topic"]) == ["some-genuinely-new-topic"]


def test_canonicalize_plurals_is_a_noop_for_already_canonical_tags():
    assert tx.canonicalize_plurals(["startups", "web-design"]) == ["startups", "web-design"]


def test_canonicalize_plurals_preserves_order():
    assert tx.canonicalize_plurals(["web-design", "startup", "obsidian"]) == \
        ["web-design", "startups", "obsidian"]


def test_canonicalize_plurals_respects_custom_canonical_set():
    assert tx.canonicalize_plurals(["widget"], canonical={"widgets"}) == ["widgets"]
    assert tx.canonicalize_plurals(["widgets"], canonical=set()) == ["widgets"]


# --- parent grouping -----------------------------------------------------------------

def test_group_topics_under_parents_places_children_and_leaves_loose():
    grouped, loose = tx.group_topics_under_parents(
        ["claude-ai", "web-design", "looksmaxxing"])
    assert grouped["claude-ecosystem"] == ["claude-ai"]
    assert grouped["web-and-design"] == ["web-design"]
    assert loose == ["looksmaxxing"]


def test_group_excludes_the_non_topic_automation_tag():
    grouped, loose = tx.group_topics_under_parents(["near-duplicate", "obsidian"])
    assert "near-duplicate" not in loose
    assert grouped["productivity-knowledge"] == ["obsidian"]


def test_parent_for_topic():
    assert tx.parent_for_topic("mcp-servers") == "claude-ecosystem"
    assert tx.parent_for_topic("nonexistent-tag") is None


def test_similarity_flags_near_duplicates_but_not_unrelated():
    assert tx.normalized_similarity("ai-workflow", "ai-workflows") > 0.9
    assert tx.normalized_similarity("obsidian", "cryptocurrency") < 0.5


# --- the priority-invariance gate (the STOP condition) -------------------------------

def _row(shortcode, topics, value_score=3):
    return {"shortcode": shortcode, "page_id": f"pg-{shortcode}",
            "topics": topics, "priority": "", "value_score": value_score}


def test_plan_flags_only_rows_whose_tags_actually_change():
    rows = [_row("A", ["claude"]), _row("B", ["obsidian"])]
    changes, violations = at.plan_merges(rows)
    assert [c["shortcode"] for c in changes] == ["A"]
    assert violations == []


def test_plan_reports_zero_priority_drift_on_the_real_merge_map():
    """Every §2 merge, applied to a row carrying ONLY that source tag, must not
    move the row's priority. This is the proposal's central §3 claim, asserted
    directly against the live merge map + real compute_priority."""
    rows = [_row(src, [src], value_score=1) for src in tx.MERGES]
    _, violations = at.plan_merges(rows)
    assert violations == []


def test_plan_would_catch_a_priority_drifting_merge():
    """Guard the guard: a merge that DID drop a CLAUDE keyword must be reported,
    so we know the gate actually fires."""
    bad_merges = {"claude-mcp": "developer-tools"}  # loses the "mcp"/"claude" match
    row = _row("X", ["claude-mcp"], value_score=1)
    before = compute_priority(["claude-mcp"], 1)
    after = compute_priority(tx.apply_merges(["claude-mcp"], bad_merges), 1)
    assert before == "High" and after == "Low"  # the drift the gate must catch


def test_apply_to_notion_dry_run_writes_nothing():
    calls = []
    changes = [{"shortcode": "A", "page_id": "pg-A", "before": ["claude"],
                "after": ["claude-ai"], "priority_before": "High", "priority_after": "High"}]
    result = at.apply_to_notion(changes, dry_run=True,
                                write_fn=lambda pid, t: calls.append((pid, t)),
                                print_fn=lambda m: None)
    assert calls == []
    assert result["planned"] == 1 and result["written"] == 0


def test_apply_to_notion_live_writes_the_after_tags():
    calls = []
    changes = [{"shortcode": "A", "page_id": "pg-A", "before": ["claude"],
                "after": ["claude-ai"], "priority_before": "High", "priority_after": "High"}]
    result = at.apply_to_notion(changes, dry_run=False,
                                write_fn=lambda pid, t: calls.append((pid, t)),
                                print_fn=lambda m: None)
    assert calls == [("pg-A", ["claude-ai"])]
    assert result["written"] == 1


# --- orphan topic-note handling ------------------------------------------------------

def _auto_note(path, name, user_notes=""):
    from app.obsidian_sync import AUTO_END, AUTO_START
    body = f"# {name}\n\n## Notes\n{user_notes}\n\n{AUTO_START}\n- generated line\n{AUTO_END}\n"
    path.write_text(body, encoding="utf-8")


def test_pure_auto_orphan_is_deletable(tmp_path):
    topics = tmp_path / "topics"
    topics.mkdir()
    _auto_note(topics / "claude.md", "claude")  # 'claude' is a real merge source
    deletable, preserved, unrelated = at.find_orphaned_topic_notes(
        tmp_path, live_tags=set(), merged_away=set(tx.MERGES))
    assert [p.name for p in deletable] == ["claude.md"]
    assert preserved == [] and unrelated == []


def test_orphan_with_user_notes_is_preserved(tmp_path):
    topics = tmp_path / "topics"
    topics.mkdir()
    _auto_note(topics / "claude-code.md", "claude-code", user_notes="My own research here.")
    deletable, preserved, unrelated = at.find_orphaned_topic_notes(
        tmp_path, live_tags=set(), merged_away=set(tx.MERGES))
    assert deletable == []
    assert [p.name for p in preserved] == ["claude-code.md"]


def test_live_topic_note_is_not_an_orphan(tmp_path):
    topics = tmp_path / "topics"
    topics.mkdir()
    _auto_note(topics / "web-design.md", "web-design")
    deletable, preserved, unrelated = at.find_orphaned_topic_notes(
        tmp_path, live_tags={"web-design"}, merged_away=set(tx.MERGES))
    assert deletable == [] and preserved == [] and unrelated == []


def test_pre_existing_orphan_not_from_a_merge_is_left_untouched(tmp_path):
    """A dead topic note whose tag was never a merge source is reported as
    unrelated and never deleted -- this step's remit is the merge only."""
    topics = tmp_path / "topics"
    topics.mkdir()
    _auto_note(topics / "geo-search.md", "geo-search")  # not in MERGES
    deletable, preserved, unrelated = at.find_orphaned_topic_notes(
        tmp_path, live_tags=set(), merged_away=set(tx.MERGES))
    assert deletable == [] and preserved == []
    assert [p.name for p in unrelated] == ["geo-search.md"]


def test_strip_auto_block_keeps_user_content(tmp_path):
    topics = tmp_path / "topics"
    topics.mkdir()
    note = topics / "loved-tag.md"
    _auto_note(note, "loved-tag", user_notes="Irreplaceable note.")
    at.strip_auto_block(note)
    text = note.read_text(encoding="utf-8")
    assert "Irreplaceable note." in text
    assert "generated line" not in text
    assert "orphaned by a taxonomy merge" in text
