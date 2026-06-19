"""Pydantic response models for the Stage 1 API."""
from __future__ import annotations

from pydantic import BaseModel


class UploadResponse(BaseModel):
    job_id: str
    filename: str
    eager: bool  # True = ran inline (no Redis); False = dispatched to a worker


class PageInfo(BaseModel):
    index: int
    sheet: str
    title: str
    keep: bool
    reason: str
    fill_colors: int
    pool_style: bool
    thumb: str | None = None


class JobStatus(BaseModel):
    job_id: str
    filename: str | None = None
    status: str                      # queued | running | done | error
    page_count: int | None = None
    kept_count: int | None = None
    pool_style_count: int | None = None
    error: str | None = None
    pages: list[PageInfo] | None = None
