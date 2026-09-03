"""
schemas.py
Pydantic v2 schemas for request/response validation.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional, List, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, ConfigDict

from models import (
    DepartmentEnum,
    UserRoleEnum,
    CameraTypeEnum,
    OwnershipEnum,
    ConnectivityStatusEnum,
    HealthStatusEnum,
    StorageTypeEnum,
)


# ---------------------------------------------------------------------------
# Auth / Users
# ---------------------------------------------------------------------------

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    department: DepartmentEnum
    role: UserRoleEnum = UserRoleEnum.VIEWER


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    department: DepartmentEnum
    role: UserRoleEnum
    is_active: bool
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str  # user id
    department: str
    role: str
    exp: Optional[int] = None


# ---------------------------------------------------------------------------
# Cameras
# ---------------------------------------------------------------------------

class StorageDetails(BaseModel):
    retention_days: int = Field(gt=0, le=3650)
    storage_type: StorageTypeEnum
    capacity_tb: float = Field(gt=0)


class CameraBase(BaseModel):
    camera_identifier: str = Field(min_length=1, max_length=128)
    camera_name: str = Field(min_length=1, max_length=255)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    department: DepartmentEnum
    camera_type: CameraTypeEnum
    ownership: OwnershipEnum
    connectivity_status: ConnectivityStatusEnum = ConnectivityStatusEnum.OFFLINE
    storage_details: StorageDetails
    health_status: HealthStatusEnum = HealthStatusEnum.OPERATIONAL
    installation_date: date
    last_ping: Optional[datetime] = None
    stream_endpoint: Optional[str] = None

    @field_validator("installation_date")
    @classmethod
    def installation_date_not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("installation_date cannot be in the future")
        return v

    @field_validator("stream_endpoint")
    @classmethod
    def validate_stream_endpoint(cls, v: Optional[str]) -> Optional[str]:
        if v and not (v.startswith("rtsp://") or v.startswith("http://") or v.startswith("https://")):
            raise ValueError("stream_endpoint must be an RTSP or HTTP(S) URL")
        return v


class CameraCreate(CameraBase):
    pass


class CameraUpdate(BaseModel):
    """Partial update — all fields optional."""
    camera_name: Optional[str] = None
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    camera_type: Optional[CameraTypeEnum] = None
    ownership: Optional[OwnershipEnum] = None
    connectivity_status: Optional[ConnectivityStatusEnum] = None
    storage_details: Optional[StorageDetails] = None
    health_status: Optional[HealthStatusEnum] = None
    last_ping: Optional[datetime] = None
    stream_endpoint: Optional[str] = None


class CameraOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    camera_identifier: str
    camera_name: str
    latitude: float
    longitude: float
    department: DepartmentEnum
    camera_type: CameraTypeEnum
    ownership: OwnershipEnum
    connectivity_status: ConnectivityStatusEnum
    storage_details: dict
    health_status: HealthStatusEnum
    installation_date: date
    last_ping: Optional[datetime] = None
    stream_endpoint: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class CameraListResponse(BaseModel):
    total: int
    items: List[CameraOut]


# ---------------------------------------------------------------------------
# Bulk upload
# ---------------------------------------------------------------------------

class RowError(BaseModel):
    row_number: int
    errors: List[str]
    raw_data: dict


class BulkUploadResult(BaseModel):
    total_rows: int
    inserted_count: int
    failed_count: int
    errors: List[RowError]


# ---------------------------------------------------------------------------
# Vendor onboarding webhook
# ---------------------------------------------------------------------------

class VendorCameraRegister(CameraBase):
    vendor_reference_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Analytics / Gap Analysis
# ---------------------------------------------------------------------------

class AgeingCameraOut(BaseModel):
    id: uuid.UUID
    camera_identifier: str
    department: DepartmentEnum
    installation_date: date
    age_years: float
    health_status: HealthStatusEnum
    reason: List[Literal["age_gt_5_years", "maintenance_required"]]


class DensityCluster(BaseModel):
    cluster_id: int
    camera_count: int
    centroid_lat: float
    centroid_lon: float


class GridCell(BaseModel):
    grid_lat: float
    grid_lon: float
    camera_count: int
    is_dead_zone: bool


class GapAnalysisReport(BaseModel):
    generated_at: datetime
    scope_department: Optional[str]
    total_cameras: int
    ageing_infrastructure: dict
    coverage_density: dict
    summary: dict
