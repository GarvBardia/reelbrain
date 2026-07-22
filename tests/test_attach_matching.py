from app import attach_matching


def _candidate(shortcode, title="", note="", topics=(), gate_keyword="", created_at=""):
    return {
        "shortcode": shortcode, "title": title, "note": note,
        "topics": list(topics), "gate_keyword": gate_keyword, "created_at": created_at,
    }


def test_score_candidate_counts_shared_meaningful_words():
    candidate = _candidate("A", title="Higgsfield is offering a filmmaker grant")
    score = attach_matching.score_candidate("Higgsfield filmmaker grant details", "", candidate)
    assert score >= 2  # "higgsfield", "filmmaker", "grant" all shared


def test_score_candidate_ignores_short_words():
    candidate = _candidate("A", title="the a in on it")
    score = attach_matching.score_candidate("the a in on it", "", candidate)
    assert score == 0  # every shared word is below MIN_WORD_LEN


def test_score_candidate_zero_when_resource_text_is_empty():
    candidate = _candidate("A", title="Anything at all here")
    assert attach_matching.score_candidate("", "", candidate) == 0


def test_score_candidate_checks_note_topics_and_gate_keyword_too():
    candidate = _candidate("A", note="mentions scrollworld here", topics=["scrollworld"], gate_keyword="scrollworld")
    score = attach_matching.score_candidate("scrollworld open source repo", "", candidate)
    assert score >= 1


def test_rank_candidates_sorts_by_score_descending():
    candidates = [
        _candidate("LOW", title="shares one word overlap"),
        _candidate("HIGH", title="shares word overlap plenty more overlap words"),
    ]
    ranked = attach_matching.rank_candidates("overlap words plenty more shares", "", candidates)
    assert [c["shortcode"] for c in ranked] == ["HIGH", "LOW"]


def test_rank_candidates_excludes_below_threshold():
    candidates = [_candidate("NOMATCH", title="completely unrelated pasta recipe")]
    ranked = attach_matching.rank_candidates("quarterly financial spreadsheet template", "", candidates)
    assert ranked == []


def test_rank_candidates_caps_at_top_n():
    candidates = [_candidate(f"C{i}", title="shared overlap word here") for i in range(10)]
    ranked = attach_matching.rank_candidates("shared overlap word", "", candidates)
    assert len(ranked) == attach_matching.TOP_N_CANDIDATES


def test_rank_candidates_ties_broken_by_most_recently_created():
    candidates = [
        _candidate("OLDER", title="shared overlap word", created_at="2026-01-01"),
        _candidate("NEWER", title="shared overlap word", created_at="2026-06-01"),
    ]
    ranked = attach_matching.rank_candidates("shared overlap word", "", candidates)
    assert [c["shortcode"] for c in ranked] == ["NEWER", "OLDER"]


def test_rank_candidates_each_result_carries_its_own_match_score():
    candidates = [_candidate("A", title="shared overlap word")]
    ranked = attach_matching.rank_candidates("shared overlap word", "", candidates)
    assert ranked[0]["match_score"] >= 1
