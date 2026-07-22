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


# --- platform-suffix stripping --------------------------------------------------

def test_strip_platform_noise_removes_google_docs_suffix():
    assert attach_matching._strip_platform_noise("Looksmaxxingprompt - Google Docs") == "Looksmaxxingprompt"


def test_strip_platform_noise_removes_google_drive_suffix():
    assert attach_matching._strip_platform_noise("My File - Google Drive") == "My File"


def test_strip_platform_noise_removes_github_variants():
    assert attach_matching._strip_platform_noise("README.md · GitHub") == "README.md"
    assert attach_matching._strip_platform_noise("GitHub - owner/repo") == "owner/repo"


def test_strip_platform_noise_leaves_unrelated_titles_untouched():
    assert attach_matching._strip_platform_noise("A Totally Normal Article Title") == "A Totally Normal Article Title"


def test_platform_noise_no_longer_produces_a_false_word_overlap():
    """The exact live incident mechanism: "google" showing up purely because
    the fetched page title ends in "- Google Docs" must no longer count as a
    real word-overlap match against an unrelated candidate."""
    candidate = _candidate("UNRELATED", title="Build websites using Google Firebase tools")
    score = attach_matching.score_candidate("Some Doc Title - Google Docs", "", candidate)
    assert score == 0


# --- gate_keyword weighting ------------------------------------------------------

def test_gate_keyword_match_scores_the_full_weight():
    candidate = _candidate("A", gate_keyword="face")
    score = attach_matching.score_candidate("", "an isolated image of the face", candidate)
    assert score == attach_matching.GATE_KEYWORD_MATCH_WEIGHT


def test_gate_keyword_match_is_whole_word_not_substring():
    """"ai" must not match inside "again" or "aisle" -- only as its own word."""
    candidate = _candidate("A", gate_keyword="ai")
    score = attach_matching.score_candidate("", "walking again down the aisle", candidate)
    assert score == 0


def test_gate_keyword_match_works_for_short_keywords():
    """A short, deliberately-chosen keyword like "AI" must still count --
    gate_keyword matching is NOT limited by MIN_WORD_LEN."""
    candidate = _candidate("A", gate_keyword="AI")
    score = attach_matching.score_candidate("this is an AI tool", "", candidate)
    assert score >= attach_matching.GATE_KEYWORD_MATCH_WEIGHT


def test_gate_keyword_match_is_case_insensitive():
    candidate = _candidate("A", gate_keyword="SEND")
    score = attach_matching.score_candidate("please send this over", "", candidate)
    assert score >= attach_matching.GATE_KEYWORD_MATCH_WEIGHT


def test_no_gate_keyword_match_when_absent_from_resource():
    candidate = _candidate("A", gate_keyword="WEBSITE", title="unrelated title words shared here plenty")
    score = attach_matching.score_candidate("shared here plenty words", "", candidate)
    assert score < attach_matching.GATE_KEYWORD_MATCH_WEIGHT


def test_gate_keyword_match_outranks_several_generic_word_overlaps():
    """The core of the fix: one real gate_keyword match must outrank a
    candidate with MORE shared words that are all generic/stopwords."""
    real_target = _candidate("REAL", gate_keyword="face", title="completely unrelated words otherwise")
    false_candidate = _candidate(
        "FALSE", gate_keyword="",
        title="using create design based high with", topics=["with"],
    )
    resource_title, resource_description = "Some Resource", "an image of the face using create design based high with"
    real_score = attach_matching.score_candidate(resource_title, resource_description, real_target)
    false_score = attach_matching.score_candidate(resource_title, resource_description, false_candidate)
    assert real_score > false_score


# --- stopwords ---------------------------------------------------------------

def test_generic_stopwords_do_not_count_as_a_match():
    candidate = _candidate("A", title="using with based high create design")
    score = attach_matching.score_candidate("using with based high create design", "", candidate)
    assert score == 0


def test_non_stopword_overlap_still_counts():
    candidate = _candidate("A", title="scrollworld animation toolkit")
    score = attach_matching.score_candidate("scrollworld animation toolkit", "", candidate)
    assert score >= 3


# --- the exact real-world regression case (DbAKlYYNEGY vs 3 false candidates) --
#
# Real live incident (see PROGRESS.md): resource_url was a Google Doc titled
# "Looksmaxxingprompt - Google Docs" whose description mentions "the face".
# The intended target, DbAKlYYNEGY, has gate_keyword "face" but was NOT
# offered -- it scored 2 (old weighting) and got outranked by three unrelated
# rows scoring 3-4 on generic words + the "Google Docs" title artifact.

def _real_candidates():
    return [
        _candidate(
            "DbAKlYYNEGY",
            title="Use Claude AI as a free personal stylist by uploading a selfie with a specific prompt to receive a c",
            topics=["claude-ai", "looksmaxxing", "productivity-hacks", "style-ai"],
            gate_keyword="face",
        ),
        _candidate(
            "DaiWZTfs3x9",
            title="How to create stylized AI animations using Grok's imagine tool combined with motion prompts.",
            topics=["ai-plugins", "claude-ai", "ai-video", "generative-ai", "animation-workflow"],
            gate_keyword="Winter",
        ),
        _candidate(
            "DawD8vcNJC7",
            title="Build luxury Apple-style scroll animation websites in under five minutes without code using Google F",
            topics=["claude-ai", "no-code", "web-design", "ai-tools", "productivity-hacks"],
            gate_keyword="WEBSITE",
        ),
        _candidate(
            "DayP5WwtYM5",
            title="How to build a node-based workflow to generate and edit high-quality 4K AI videos using Arcads and G",
            topics=["ai-plugins", "productivity-hacks", "ai-video", "ai-generation"],
            gate_keyword="AI",
        ),
    ]


# The real fetched title/description from resource_lookup.fetch_resource_title_and_description
# against the actual Google Doc — captured once, live, then hardcoded here so
# the test never makes a network call.
_REAL_RESOURCE_TITLE = "Looksmaxxingprompt - Google Docs"
_REAL_RESOURCE_DESCRIPTION = (
    "Create a clean, minimal, high-end facial aesthetics report based on the uploaded photo, "
    "using a black or white editorial design with thin linework, rounded cards, generous "
    "spacing, modern typography, and a refined luxury feel. Include an isolated front-facing "
    "image of the face, presented as an an..."
)


def test_real_incident_regression_dbaklyynegy_now_wins():
    candidates = _real_candidates()

    scores = {
        c["shortcode"]: attach_matching.score_candidate(_REAL_RESOURCE_TITLE, _REAL_RESOURCE_DESCRIPTION, c)
        for c in candidates
    }
    assert scores["DbAKlYYNEGY"] == 5  # GATE_KEYWORD_MATCH_WEIGHT, gate_keyword="face" matched, no generic overlap left
    assert scores["DaiWZTfs3x9"] == 0  # its only shared words (using/create/with) are now stopwords
    assert scores["DawD8vcNJC7"] == 1  # "luxury" survives -- a genuine, if coincidental, non-generic overlap
    assert scores["DayP5WwtYM5"] == 0  # its only shared words (based/using/high) are now stopwords

    ranked = attach_matching.rank_candidates(_REAL_RESOURCE_TITLE, _REAL_RESOURCE_DESCRIPTION, candidates)
    assert ranked[0]["shortcode"] == "DbAKlYYNEGY"  # wins outright, not just "appears in top 3"
    assert ranked[0]["match_score"] == 5
