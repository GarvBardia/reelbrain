"""scripts/vault_orphans.py — all mocked, tmp_path as the vault."""
from scripts import vault_orphans as vo


def _vault(tmp_path):
    for d in ("reels", "topics", "creators", "resources"):
        (tmp_path / d).mkdir()
    return tmp_path


def _reel(vault, stem, *, shortcode="SC1", topics=(), status="📥 Inbox", content=True):
    lines = ["---", f"shortcode: {shortcode}", f'status: "{status}"']
    if topics:
        lines.append("topics:")
        for t in topics:
            lines.append(f'  - "[[topics/{t}]]"')
    lines += ["---", "", f"# {stem}", ""]
    if content:
        lines += ["## Supporting points", "", "- a real supporting point"]
    else:
        lines += ["## Raw caption", "", "(no caption)"]
    (vault / "reels" / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- link graph + orphan detection ------------------------------------------------

def test_orphan_is_note_with_no_inbound_links(tmp_path):
    vault = _vault(tmp_path)
    _reel(vault, "linked-reel", shortcode="A1", topics=("claude-ai",))
    _reel(vault, "orphan-reel", shortcode="B2")
    (vault / "topics" / "claude-ai.md").write_text(
        "## Saved Reels\n\n- [[reels/linked-reel|Linked]]\n", encoding="utf-8")

    orphans = vo.find_orphans(vault)
    assert "reels/orphan-reel" in orphans["reels"]
    assert "reels/linked-reel" not in orphans.get("reels", [])


def test_root_entry_points_are_not_treated_as_orphans(tmp_path):
    vault = _vault(tmp_path)
    for name in vo.ROOT_ENTRY_POINTS:
        (vault / f"{name}.md").write_text("# entry point\n", encoding="utf-8")
    orphans = vo.find_orphans(vault)
    assert orphans.get("(root)", []) == []


def test_non_entrypoint_root_note_is_still_an_orphan(tmp_path):
    vault = _vault(tmp_path)
    (vault / "stray.md").write_text("# stray\n", encoding="utf-8")
    assert vo.find_orphans(vault)["(root)"] == ["stray"]


def test_self_link_does_not_rescue_a_note_from_orphanhood(tmp_path):
    vault = _vault(tmp_path)
    (vault / "reels" / "selfie.md").write_text(
        "---\nshortcode: S1\n---\n\n# selfie\n\n[[reels/selfie]]\n", encoding="utf-8")
    assert "reels/selfie" in vo.find_orphans(vault)["reels"]


def test_obsidian_config_dir_is_ignored(tmp_path):
    vault = _vault(tmp_path)
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "cfg.md").write_text("# config\n", encoding="utf-8")
    assert "cfg" not in vo.find_orphans(vault).get("(root)", [])


# --- content detection ------------------------------------------------------------

def test_real_content_detected_from_sections(tmp_path):
    vault = _vault(tmp_path)
    _reel(vault, "has-content", content=True)
    assert vo.note_has_real_content(vault / "reels" / "has-content.md") is True


def test_contentless_note_detected(tmp_path):
    vault = _vault(tmp_path)
    _reel(vault, "empty", content=False)
    assert vo.note_has_real_content(vault / "reels" / "empty.md") is False


def test_unreadable_note_never_counts_as_empty(tmp_path):
    vault = _vault(tmp_path)
    assert vo.note_has_real_content(vault / "reels" / "does-not-exist.md") is True


# --- classification ---------------------------------------------------------------

def test_topic_with_reels_but_missing_saved_reels_block_is_rebuildable(tmp_path):
    """Case 1 as actually reachable here: reel frontmatter links to the topic
    (so it's never an ORPHAN), but the topic's generated index block is
    missing, so browsing the topic shows nothing."""
    vault = _vault(tmp_path)
    _reel(vault, "r1", shortcode="A1", topics=("claude-ai",))
    _reel(vault, "r2", shortcode="B2", topics=("claude-ai",))
    (vault / "topics" / "claude-ai.md").write_text("# claude-ai\n", encoding="utf-8")

    report = vo.classify(vault)
    assert "topics/claude-ai" in report["rebuildable_topics"]
    # and it is correctly NOT an orphan -- the reels do link to it
    assert "topics/claude-ai" not in report["orphans"].get("topics", [])


def test_topic_with_intact_saved_reels_block_is_not_rebuildable(tmp_path):
    vault = _vault(tmp_path)
    _reel(vault, "r1", shortcode="A1", topics=("claude-ai",))
    _reel(vault, "r2", shortcode="B2", topics=("claude-ai",))
    (vault / "topics" / "claude-ai.md").write_text(
        "# claude-ai\n\n## Saved Reels\n\n- [[reels/r1]]\n- [[reels/r2]]\n", encoding="utf-8")
    assert vo.classify(vault)["rebuildable_topics"] == []


def test_topic_with_one_reel_is_not_rebuildable(tmp_path):
    vault = _vault(tmp_path)
    _reel(vault, "r1", shortcode="A1", topics=("lonely",))
    (vault / "topics" / "lonely.md").write_text("# lonely\n", encoding="utf-8")
    assert vo.classify(vault)["rebuildable_topics"] == []


def test_topic_referenced_by_reels_but_file_missing_is_rebuildable(tmp_path):
    vault = _vault(tmp_path)
    _reel(vault, "r1", shortcode="A1", topics=("ghost-topic",))
    _reel(vault, "r2", shortcode="B2", topics=("ghost-topic",))
    assert "topics/ghost-topic" in vo.classify(vault)["rebuildable_topics"]


def test_reel_orphan_without_topics_needs_tags(tmp_path):
    vault = _vault(tmp_path)
    _reel(vault, "untagged", shortcode="U1", topics=(), content=True)
    report = vo.classify(vault)
    assert report["reel_needs_tags"] == ["reels/untagged"]
    assert report["deletable"] == []


def test_contentless_orphan_in_retry_state_is_protected_not_deleted(tmp_path):
    vault = _vault(tmp_path)
    _reel(vault, "failed", shortcode="F1", status="⚠️ Failed — retry", content=False)
    report = vo.classify(vault)
    assert report["deletable"] == []
    assert report["retry_protected"] == [("reels/failed", "⚠️ Failed — retry")]


def test_contentless_orphan_in_awaiting_dm_is_also_protected(tmp_path):
    vault = _vault(tmp_path)
    _reel(vault, "gated", shortcode="G1", status="⏳ Awaiting DM", content=False)
    assert vo.classify(vault)["deletable"] == []


def test_contentless_orphan_in_inert_state_is_deletable(tmp_path):
    vault = _vault(tmp_path)
    _reel(vault, "junk", shortcode="J1", status="📥 Inbox", content=False)
    assert vo.classify(vault)["deletable"] == ["reels/junk"]


# --- case 2 row building: the placeholder guard -----------------------------------

def _notion_page(shortcode, title):
    return {
        "id": f"pg-{shortcode}",
        "properties": {
            "Shortcode": {"rich_text": [{"plain_text": shortcode}]},
            "Title": {"title": [{"plain_text": title}]},
            "Status": {"select": {"name": "📥 Inbox"}},
        },
    }


def test_tagging_rows_exclude_placeholder_titles():
    """REGRESSION (found live): tagging a placeholder row makes Gemini describe
    the placeholder TEXT, not the reel -- it produced junk tags like
    'captions'/'transcripts'/'content-unavailable' that had to be reverted."""
    pages = [
        _notion_page("REAL1", "Build a Claude MCP server"),
        _notion_page("PLACE1", vo.PLACEHOLDER_TITLE),
    ]
    rows, skipped = vo.build_tagging_rows(pages, {"REAL1", "PLACE1"})
    assert [r["shortcode"] for r in rows] == ["REAL1"]
    assert skipped == 1


def test_tagging_rows_ignore_shortcodes_not_in_the_orphan_set():
    pages = [_notion_page("REAL1", "A title"), _notion_page("OTHER1", "Another title")]
    rows, _ = vo.build_tagging_rows(pages, {"REAL1"})
    assert [r["shortcode"] for r in rows] == ["REAL1"]


def test_tagging_rows_skip_rows_with_no_title():
    pages = [_notion_page("EMPTY1", "")]
    rows, skipped = vo.build_tagging_rows(pages, {"EMPTY1"})
    assert rows == [] and skipped == 0


# --- case 3: resource -> reel linking ---------------------------------------------

def _resource(vault, stem, url, parent=None):
    lines = ["---", f"url: {url}"]
    if parent:
        lines.append(f"source_shortcode: {parent}")
    lines += ["---", "", f"# {stem}", "", "## Summary", "", "text"]
    (vault / "resources" / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_resource_linked_by_normalized_gate_url(tmp_path):
    vault = _vault(tmp_path)
    _resource(vault, "guide", "https://github.com/a/b?fbclid=123")
    gate_map = {"github.com/a/b": "REEL9"}

    linked, unlinked = vo.link_resources_to_reels(
        vault, ["resources/guide"], gate_map, dry_run=False, print_fn=lambda *a: None)

    assert (linked, unlinked) == (1, [])
    assert "source_shortcode: REEL9" in (vault / "resources" / "guide.md").read_text(encoding="utf-8")


def test_resource_with_no_matching_gate_url_stays_unlinked(tmp_path):
    vault = _vault(tmp_path)
    _resource(vault, "orphan-res", "https://example.com/nothing")
    linked, unlinked = vo.link_resources_to_reels(
        vault, ["resources/orphan-res"], {}, dry_run=False, print_fn=lambda *a: None)
    assert (linked, unlinked) == (0, ["resources/orphan-res"])


def test_resource_linking_dry_run_writes_nothing(tmp_path):
    vault = _vault(tmp_path)
    _resource(vault, "guide", "https://github.com/a/b")
    vo.link_resources_to_reels(vault, ["resources/guide"], {"github.com/a/b": "R1"},
                               dry_run=True, print_fn=lambda *a: None)
    assert "source_shortcode" not in (vault / "resources" / "guide.md").read_text(encoding="utf-8")


# --- case 4: deletion -------------------------------------------------------------

def test_delete_dry_run_removes_nothing(tmp_path):
    vault = _vault(tmp_path)
    _reel(vault, "junk", shortcode="J1", content=False)
    count = vo.delete_orphans(vault, ["reels/junk"], dry_run=True, print_fn=lambda *a: None)
    assert count == 1
    assert (vault / "reels" / "junk.md").exists()


def test_delete_removes_note_and_archives_notion_row(tmp_path, monkeypatch):
    from app import notion_writer, store

    vault = _vault(tmp_path)
    _reel(vault, "junk", shortcode="J1", content=False)
    archived = []
    monkeypatch.setattr(notion_writer, "find_page_by_shortcode", lambda sc: {"id": f"pg-{sc}"})
    monkeypatch.setattr(notion_writer, "set_status", lambda pid, st: archived.append((pid, st)))
    monkeypatch.setattr(store, "update_save", lambda sc, **kw: None)

    count = vo.delete_orphans(vault, ["reels/junk"], dry_run=False, print_fn=lambda *a: None)

    assert count == 1
    assert not (vault / "reels" / "junk.md").exists()
    assert archived == [("pg-J1", "archived")]
