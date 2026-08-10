"""Pydantic schemas shared across the pipeline. Mirrors DATA_SCHEMA.md."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CaptureRequest(BaseModel):
    """Strict: unknown fields rejected, all strings bounded (defense against
    junk hitting a public endpoint — the URL itself is validated separately by
    fetcher.normalize_url, which is the real gate)."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    note: Optional[str] = Field(default=None, max_length=2000)
    secret: str = Field(min_length=1, max_length=256)


class AttachRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shortcode_or_note: Optional[str] = Field(default=None, max_length=500)
    resource_url: str = Field(min_length=1, max_length=2048)
    secret: str = Field(min_length=1, max_length=256)

    @field_validator("resource_url")
    @classmethod
    def _must_be_http_url(cls, v: str) -> str:
        if not (v.startswith("https://") or v.startswith("http://")):
            raise ValueError("resource_url must be an http(s) URL")
        return v


class AttachConfirmRequest(BaseModel):
    """POST /attach/confirm: commits a specific candidate the caller chose
    from a prior /attach "needs_confirmation" response. shortcode is
    REQUIRED and must be exact — this endpoint never guesses either."""

    model_config = ConfigDict(extra="forbid")

    shortcode: str = Field(min_length=1, max_length=64)
    resource_url: str = Field(min_length=1, max_length=2048)
    secret: str = Field(min_length=1, max_length=256)

    @field_validator("resource_url")
    @classmethod
    def _must_be_http_url(cls, v: str) -> str:
        if not (v.startswith("https://") or v.startswith("http://")):
            raise ValueError("resource_url must be an http(s) URL")
        return v


class NightlyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret: str = Field(min_length=1, max_length=256)


class ReelData(BaseModel):
    """Output of fetcher.fetch_reel. Only shortcode/permalink are guaranteed present."""

    shortcode: str
    permalink: str
    video_path: Optional[str] = None
    caption: Optional[str] = None
    creator_username: Optional[str] = None
    creator_fullname: Optional[str] = None
    taken_at: Optional[str] = None
    like_count: Optional[int] = None
    # Only set by the OG-tag fallback (fetcher.fetch_og_metadata); yt-dlp's own
    # metadata doesn't populate it. Not written to Notion today.
    thumbnail_url: Optional[str] = None
    # True only when fetcher determined this is a photo/carousel post (yt-dlp's
    # "no video formats found" signature) with no OG-tag caption recoverable
    # either. Forces a distinct terminal status in main.py's run_pipeline instead
    # of the normal comment-gate/value-score decision — retrying can never help
    # a non-video post, so it must not land as "Failed — retry".
    is_photo_or_carousel: bool = False
    # Fetcher-supplied note surfaced on the Notion row's "My note" regardless of
    # success/failure (see main.py's _note_with_failure_reason). No emoji prefix
    # here — that's added uniformly by the note-building helper.
    fetch_note: Optional[str] = None
    # yt-dlp's own reported size (bytes) for the file it just downloaded, if it
    # reported one (see fetcher._expected_download_size). gemini_pipe.py checks
    # the actual file on disk against this before invoking ffmpeg — a defensive
    # check against a truncated/still-being-written file (see PROGRESS.md).
    expected_video_size: Optional[int] = None


class ResourceMentioned(BaseModel):
    name: str
    type: Literal["tool", "book", "site", "person", "course", "other"]
    url_if_stated: Optional[str] = None


class CommentGate(BaseModel):
    detected: bool = False
    keyword: Optional[str] = None
    promised_resource: Optional[str] = None


class ResearchContextItem(BaseModel):
    """One entry of the second-pass, search-grounded research writeup (see
    gemini_pipe.run_research_context). `context` is EITHER a real 2-3 sentence
    grounded writeup, OR the literal string "not found via search" -- never a
    silent, unlabeled fallback to Gemini's own training data. That distinction
    is the entire point of this pass; see PROGRESS.md."""

    topic: str
    context: str
    # Where the context actually came from: "search-grounding" (a grounded
    # Gemini call with real Google Search chunks), or "web-fetch" (the free
    # fallback -- DuckDuckGo top result / GitHub README fetched for real and
    # summarized). Either way the text is anchored to fetched material, never
    # unlabeled model memory.
    source: str = "search-grounding"


class Extraction(BaseModel):
    """DATA_SCHEMA.md §3, extended with transcript/has_speech per BUILD_SPEC 1.3+1.4."""

    transcript: str = ""
    has_speech: Optional[bool] = False
    main_point: str = Field(..., max_length=200)
    supporting_points: list[str] = Field(default_factory=list, max_length=6)
    resources_mentioned: list[ResourceMentioned] = Field(default_factory=list)
    steps_or_framework: list[str] = Field(default_factory=list)
    quotable_lines: list[str] = Field(default_factory=list, max_length=3)
    topic_tags: list[str] = Field(default_factory=list)
    # Distinct from topic_tags (which stays categorical -- taxonomy
    # convergence, the Notion Topics multi-select, Obsidian topic index pages,
    # compute_priority's Claude-keyword match all depend on topic_tags staying
    # a small, reusable, category-level set). named_entities is per-reel and
    # specific on purpose: exact tool/product names, named techniques, stated
    # claims -- the look-up-able things gemini_pipe.run_research_context
    # actually researches. Never fed into the taxonomy.
    named_entities: list[str] = Field(default_factory=list)
    # ONE imperative next step the saver could actually take ("Install X and
    # test on one clip"), or the literal "none — informational" when there is
    # genuinely nothing to do. Drives the Notion "Suggested action" property
    # and the Obsidian note's "## Do" line. Empty string on the degraded path.
    suggested_action: str = ""
    # 1-2 sentences a reader with ZERO context could follow -- no jargon
    # without a plain-language gloss, no assuming they saw the reel. This is
    # the first line of every Obsidian note and the topic-page listing text.
    # main_point stays precise and names tools; plain_summary EXPLAINS.
    # Defaults to "" so rows extracted before this field existed still
    # validate (callers fall back to main_point).
    plain_summary: str = ""
    content_type: Literal[
        "tutorial",
        "insight",
        "resource_drop",
        "motivation",
        "news",
        "entertainment",
        "unknown",
    ] = "unknown"
    comment_gate: CommentGate = Field(default_factory=CommentGate)
    value_score: int = Field(ge=1, le=5, default=3)
    language: str = "en"
    # Computed post-extraction (see gemini_pipe.compute_priority), never set by
    # Gemini itself — drives the Notion "Priority" Select property and the
    # Obsidian "Action Needed"-style grouping. Plain text, no emoji.
    priority: Literal["High", "Medium", "Low"] = "Low"
    # Populated by gemini_pipe.run_research_context AFTER the main extraction
    # call, never by call 1 itself (the prompt explicitly tells call 1 to
    # leave this as [] -- see prompts/extraction.md). Empty on the degraded
    # path and whenever research_context wasn't attempted at all.
    research_context: list[ResearchContextItem] = Field(default_factory=list)

    @field_validator("main_point")
    @classmethod
    def _truncate_main_point(cls, v: str) -> str:
        return v[:200]


class ResourceExtraction(BaseModel):
    """Structured summary of a long-form DM'd resource (Drive doc, GitHub repo,
    web guide, PDF) attached via a comment-gate -- adapted from Extraction for
    longer-form content: no transcript/speech/comment-gate fields, since those
    are reel-specific."""

    summary: str = Field(..., max_length=800)
    key_takeaways: list[str] = Field(default_factory=list, max_length=8)
    topic_tags: list[str] = Field(default_factory=list)
    resource_kind: Literal["github_repo", "google_doc", "web_article", "pdf", "other"] = "other"
    # ONE imperative next step the reader could take with this resource
    # ("Clone the repo and run the demo"), or exactly "none — informational".
    # Same field/contract as Extraction.suggested_action on the reel notes.
    # Defaults to "" so resources summarized before this field existed still
    # validate.
    suggested_action: str = ""


class TopicRetag(BaseModel):
    """Output of the taxonomy-repair re-tag pass (PROGRESS.md, 2026-08-09
    taxonomy-collapse incident): re-picks topic_tags for an already-extracted
    row from its stored summary, against the current canonical taxonomy. Does
    not re-derive anything else about the row -- narrower on purpose than
    Extraction, since this pass never re-fetches or re-reads the source reel."""

    topic_tags: list[str] = Field(..., min_length=1, max_length=6)


def degraded_extraction(caption: Optional[str]) -> Extraction:
    """BUILD_SPEC 1.4: fallback when Gemini extraction fails twice."""
    main_point = (caption or "")[:200] or "No caption or transcript available."
    return Extraction(
        transcript="",
        has_speech=None,
        main_point=main_point,
        content_type="unknown",
    )
