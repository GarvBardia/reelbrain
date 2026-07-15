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


class ResourceMentioned(BaseModel):
    name: str
    type: Literal["tool", "book", "site", "person", "course", "other"]
    url_if_stated: Optional[str] = None


class CommentGate(BaseModel):
    detected: bool = False
    keyword: Optional[str] = None
    promised_resource: Optional[str] = None


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

    @field_validator("main_point")
    @classmethod
    def _truncate_main_point(cls, v: str) -> str:
        return v[:200]


def degraded_extraction(caption: Optional[str]) -> Extraction:
    """BUILD_SPEC 1.4: fallback when Gemini extraction fails twice."""
    main_point = (caption or "")[:200] or "No caption or transcript available."
    return Extraction(
        transcript="",
        has_speech=None,
        main_point=main_point,
        content_type="unknown",
    )
