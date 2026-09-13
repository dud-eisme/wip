"""
schemas.py
Pydantic v2 request/response schemas for Model 2.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field, ConfigDict, field_validator

from models import SourceTypeEnum, JobStatusEnum


# ---------------------------------------------------------------------------
# Auth (mirrors Model 1's Token shape — tokens are interchangeable as long
# as SECRET_KEY matches)
# ---------------------------------------------------------------------------

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------------------------------------------------------------------------
# Sources registry
# ---------------------------------------------------------------------------

class CameraSourceCreate(BaseModel):
    camera_id: uuid.UUID
    source_name: str = Field(min_length=1, max_length=255)
    source_type: SourceTypeEnum
    source_url: str = Field(min_length=1, max_length=1024)
    is_active: bool = True

    @field_validator("source_url")
    @classmethod
    def validate_url_shape(cls, v: str, info) -> str:
        source_type = info.data.get("source_type")
        if source_type == SourceTypeEnum.RTSP and not v.lower().startswith("rtsp://"):
            raise ValueError("source_url must start with rtsp:// for source_type='rtsp'")
        if source_type == SourceTypeEnum.HTTP and not (v.lower().startswith("http://") or v.lower().startswith("https://")):
            raise ValueError("source_url must start with http:// or https:// for source_type='http'")
        return v


class CameraSourceUpdate(BaseModel):
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    is_active: Optional[bool] = None


class CameraSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    camera_id: uuid.UUID
    source_name: str
    source_type: SourceTypeEnum
    source_url: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CameraSourceListResponse(BaseModel):
    total: int
    items: List[CameraSourceOut]


class WorkerStatus(BaseModel):
    source_id: uuid.UUID
    is_running: bool
    frames_captured: int
    last_frame_at: Optional[datetime] = None
    last_error: Optional[str] = None


class WorkersStatusResponse(BaseModel):
    max_concurrent_sources: int
    active_worker_count: int
    workers: List[WorkerStatus]


# ---------------------------------------------------------------------------
# ANPR jobs & events
# ---------------------------------------------------------------------------

class AnprJobCreate(BaseModel):
    source_id: uuid.UUID = Field(description="A registered camera_sources.id to process.")
    frame_skip: Optional[int] = Field(default=None, ge=1, le=100, description="Override ANPR_FRAME_SKIP for this job.")
    max_frames: Optional[int] = Field(default=500, ge=1, le=20000, description="Safety cap on frames processed for this job.")


class AnprJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: Optional[uuid.UUID]
    camera_id: Optional[uuid.UUID]
    status: JobStatusEnum
    processed_frames: int
    events_found: int
    error_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class AnprEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: Optional[uuid.UUID]
    camera_id: Optional[uuid.UUID]
    source_id: Optional[uuid.UUID]
    plate_text: str
    confidence: float
    detected_at: datetime
    frame_number: Optional[int] = None
    snapshot_path: Optional[str] = None
    is_flagged: bool
    flagged_note: Optional[str] = None


class AnprEventListResponse(BaseModel):
    total: int
    items: List[AnprEventOut]


class EventFlagUpdate(BaseModel):
    is_flagged: bool
    flagged_note: Optional[str] = Field(default=None, max_length=500)
