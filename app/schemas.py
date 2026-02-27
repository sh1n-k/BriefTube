from __future__ import annotations

from pydantic import BaseModel, Field


class ChannelCreate(BaseModel):
    channel_id: str = Field(min_length=1)
    channel_name: str = Field(min_length=1)


class ChannelOut(BaseModel):
    channel_id: str
    channel_name: str
    rss_url: str
    is_active: int
    last_seen_published_at: str | None = None
    created_at: str


class VideoOut(BaseModel):
    video_id: str
    channel_id: str
    title: str
    upload_time: str
    thumbnail_path: str | None = None
    pipeline_status: str
    retry_count: int
    created_at: str


class ArticleOut(BaseModel):
    video_id: str
    title: str
    lead: str
    body: str
    fact_box: str | None = None
    timestamps: str | None = None
    created_at: str


class TranscriptOut(BaseModel):
    video_id: str
    raw_text: str
    language: str | None = None
    source_type: str
    created_at: str
