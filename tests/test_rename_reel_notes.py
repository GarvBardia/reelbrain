"""scripts/rename_reel_notes.py — all mocked, no live Notion/vault (uses tmp_path)."""
from scripts import rename_reel_notes as rrn


def _note(shortcode: str, title: str) -> str:
    return (
        f"---\nshortcode: {shortcode}\nstatus: \"done\"\n---\n\n"
        f"# {title}\n\nSome body text.\n"
    )


def _make_vault(tmp_path):
    (tmp_path / "reels").mkdir()
    (tmp_path / "topics").mkdir()
    (tmp_path / "creators").mkdir()
    (tmp_path / "resources").mkdir()
    return tmp_path


# --- parsing -----------------------------------------------------------------

def test_parse_reel_note_extracts_shortcode_and_title(tmp_path):
    vault = _make_vault(tmp_path)
    path = vault / "reels" / "2026-07-20-ABC123.md"
    path.write_text(_note("ABC123", "Build a Claude MCP server"), encoding="utf-8")
    parsed = rrn.parse_reel_note(path)
    assert parsed == {"shortcode": "ABC123", "title": "Build a Claude MCP server", "old_stem": "2026-07-20-ABC123"}


def test_parse_reel_note_none_when_no_frontmatter(tmp_path):
    vault = _make_vault(tmp_path)
    path = vault / "reels" / "broken.md"
    path.write_text("# just a title, no frontmatter\n", encoding="utf-8")
    assert rrn.parse_reel_note(path) is None


def test_parse_reel_note_none_when_no_h1(tmp_path):
    vault = _make_vault(tmp_path)
    path = vault / "reels" / "broken2.md"
    path.write_text("---\nshortcode: X1\n---\n\nno heading here\n", encoding="utf-8")
    assert rrn.parse_reel_note(path) is None


# --- compute_renames -----------------------------------------------------------

def test_compute_renames_slugifies_and_skips_already_correct():
    notes = [
        {"shortcode": "A1", "title": "Build a Claude MCP server", "old_stem": "2026-07-20-A1"},
        {"shortcode": "B2", "title": "already-slugified-name", "old_stem": "already-slugified-name"},
    ]
    renames = rrn.compute_renames(notes)
    assert renames == {"2026-07-20-A1": "build-a-claude-mcp-server"}


def test_compute_renames_disambiguates_collisions_with_shortcode_suffix():
    notes = [
        {"shortcode": "AAA1", "title": "Same Exact Title", "old_stem": "2026-07-19-AAA1"},
        {"shortcode": "BBB2", "title": "Same Exact Title", "old_stem": "2026-07-20-BBB2"},
    ]
    renames = rrn.compute_renames(notes)
    assert renames["2026-07-19-AAA1"] == "same-exact-title"
    assert renames["2026-07-20-BBB2"] == "same-exact-title-bbb2"
    # never two old stems mapping to the same new stem
    assert len(set(renames.values())) == len(renames)


def test_compute_renames_truncates_to_60_chars():
    long_title = "x" * 100
    notes = [{"shortcode": "A1", "title": long_title, "old_stem": "old"}]
    renames = rrn.compute_renames(notes)
    assert len(renames["old"]) == 60


# --- rewrite_links ---------------------------------------------------------------

def test_rewrite_links_updates_plain_and_aliased_wikilinks():
    text = "See [[reels/2026-07-20-A1]] and [[reels/2026-07-20-A1|My Title]] for details."
    rewritten = rrn.rewrite_links(text, {"2026-07-20-A1": "build-a-claude-mcp-server"})
    assert "[[reels/build-a-claude-mcp-server]]" in rewritten
    assert "[[reels/build-a-claude-mcp-server|My Title]]" in rewritten


def test_rewrite_links_leaves_unmapped_stems_untouched():
    text = "[[reels/some-other-note]]"
    assert rrn.rewrite_links(text, {"2026-07-20-A1": "new-slug"}) == text


# --- apply_renames: full integration over a fake vault --------------------------

def test_apply_renames_renames_files_and_updates_links_everywhere(tmp_path):
    vault = _make_vault(tmp_path)
    (vault / "reels" / "2026-07-20-A1.md").write_text(_note("A1", "Build a Claude MCP server"), encoding="utf-8")
    (vault / "topics" / "claude-ai.md").write_text(
        "## Saved Reels\n\n- [[reels/2026-07-20-A1|Build a Claude MCP server]] — High\n", encoding="utf-8"
    )
    (vault / "_index.md").write_text("- [[reels/2026-07-20-A1|Build a Claude MCP server]]\n", encoding="utf-8")

    rename_map = {"2026-07-20-A1": "build-a-claude-mcp-server"}
    count = rrn.apply_renames(vault, rename_map)

    assert count == 1
    assert not (vault / "reels" / "2026-07-20-A1.md").exists()
    assert (vault / "reels" / "build-a-claude-mcp-server.md").exists()
    assert "reels/build-a-claude-mcp-server" in (vault / "topics" / "claude-ai.md").read_text(encoding="utf-8")
    assert "reels/build-a-claude-mcp-server" in (vault / "_index.md").read_text(encoding="utf-8")


def test_apply_renames_skips_missing_old_file(tmp_path):
    vault = _make_vault(tmp_path)
    count = rrn.apply_renames(vault, {"nonexistent": "new-name"})
    assert count == 0


def test_apply_renames_never_touches_obsidian_config_dir(tmp_path):
    vault = _make_vault(tmp_path)
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "workspace.md").write_text("[[reels/2026-07-20-A1]]", encoding="utf-8")
    (vault / "reels" / "2026-07-20-A1.md").write_text(_note("A1", "T"), encoding="utf-8")
    rrn.apply_renames(vault, {"2026-07-20-A1": "t"})
    # untouched even though it contains a matching link
    assert (vault / ".obsidian" / "workspace.md").read_text(encoding="utf-8") == "[[reels/2026-07-20-A1]]"
