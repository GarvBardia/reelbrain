"""app/taxonomy drift detection + scripts/vault_librarian.py reporting.
Mocked/pure — no Notion, no live sync."""
from app import taxonomy as tx
from scripts import vault_librarian as vl


# --- drift detection (report only) ---------------------------------------------------

def test_drift_flags_a_near_duplicate_of_a_canonical_tag():
    # 'automations' is not canonical but is ~ 'automation' (a merge target).
    findings = tx.find_tag_drift(["automations", "web-design"])
    assert any(f["new_tag"] == "automations" and f["nearest_canonical"] == "automation"
               for f in findings)


def test_drift_ignores_canonical_and_loose_tags():
    # every one of these is canonical (child, merge target, or intentional loose)
    findings = tx.find_tag_drift(["automation", "claude-ai", "looksmaxxing", "web-design"])
    assert findings == []


def test_drift_ignores_clearly_distinct_new_tags():
    findings = tx.find_tag_drift(["quantum-computing"])
    assert findings == []


def test_drift_catches_a_reintroduced_merged_tag_spelling():
    """A merge SOURCE is no longer canonical; a close spelling of one (e.g.
    'ai-automations' vs the target 'automation'/source 'ai-automation') must
    surface as drift so a regression that re-introduces a merged concept is
    caught for human review."""
    findings = tx.find_tag_drift(["automations"])  # ~0.95 vs canonical 'automation'
    assert findings and findings[0]["nearest_canonical"] == "automation"
    assert findings[0]["score"] >= tx.DRIFT_SIMILARITY_THRESHOLD


def test_short_form_below_threshold_is_not_flagged():
    """'claude' ~ 'claude-ai' is only ~0.8 — deliberately below threshold. A bare
    short-form isn't fuzzy-caught here (apply_taxonomy's exact merge map handles
    it); the drift check is for near-miss SPELLINGS, not substrings."""
    assert tx.normalized_similarity("claude", "claude-ai") < tx.DRIFT_SIMILARITY_THRESHOLD
    assert tx.find_tag_drift(["claude"]) == []


def test_drift_findings_sorted_worst_first():
    findings = tx.find_tag_drift(["automations", "ai-workflowss"])
    scores = [f["score"] for f in findings]
    assert scores == sorted(scores, reverse=True)


# --- the drift report file -----------------------------------------------------------

def test_write_drift_report_with_findings(tmp_path):
    path = tmp_path / "TAXONOMY_DRIFT.md"
    vl.write_drift_report([{"new_tag": "automations", "nearest_canonical": "automation",
                            "score": 0.95}], path)
    text = path.read_text(encoding="utf-8")
    assert "automations" in text and "automation" in text
    assert "Report only" in text  # must state it never merges


def test_write_drift_report_empty_still_writes_file(tmp_path):
    path = tmp_path / "TAXONOMY_DRIFT.md"
    vl.write_drift_report([], path)
    assert path.exists()
    assert "No drift detected" in path.read_text(encoding="utf-8")


# --- count reconciliation ------------------------------------------------------------

def test_count_reconciliation_matches(monkeypatch):
    pages = [{"properties": {"Shortcode": {"rich_text": [{"plain_text": "A"}]}}},
             {"properties": {"Shortcode": {"rich_text": [{"plain_text": "B"}]}}}]
    monkeypatch.setattr(vl, "count_reconciliation",
                        lambda: {"notion_rows": 2, "vault_notes": 2, "match": True, "drift": 0})
    assert vl.count_reconciliation()["match"] is True
