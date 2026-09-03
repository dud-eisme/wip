"""
models.py
SQLAlchemy / GeoAlchemy2 ORM models for the Centralised CCTV Registry.
"""
import enum
import uuid
from datetime import datetime, date

from sqlalchemy import (
    Column,
    String,
    Enum,
    Date,
    DateTime,
    ForeignKey,
    Float,
    Boolean,
    Index,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from geoalchemy2 import Geometry

from database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class DepartmentEnum(str, enum.Enum):
    POLICE = "Police"
    TRANSPORT = "Transport"
    MUNICIPAL = "Municipal"
    ADMIN = "Admin"


class UserRoleEnum(str, enum.Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class CameraTypeEnum(str, enum.Enum):
    PTZ = "PTZ"
    FIXED_BULLET = "Fixed Bullet"
    DOME = "Dome"
    ANPR = "ANPR"


class OwnershipEnum(str, enum.Enum):
    GOVERNMENT = "Government"
    PRIVATE = "Private"
    PPP = "PPP"


class ConnectivityStatusEnum(str, enum.Enum):
    ONLINE = "Online"
    OFFLINE = "Offline"
    DEGRADED = "Degraded"


class HealthStatusEnum(str, enum.Enum):
    OPERATIONAL = "Operational"
    MAINTENANCE_REQUIRED = "Maintenance Required"
    DEFECTIVE = "Defective"


class StorageTypeEnum(str, enum.Enum):
    LOCAL = "local"
    CLOUD = "cloud"
    HYBRID = "hybrid"


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    department = Column(Enum(DepartmentEnum, name="department_enum"), nullable=False, index=True)
    role = Column(Enum(UserRoleEnum, name="user_role_enum"), nullable=False, default=UserRoleEnum.VIEWER)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ---------------------------------------------------------------------------
# Cameras
# ---------------------------------------------------------------------------

class Camera(Base):
    __tablename__ = "cameras"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_identifier = Column(String, unique=True, nullable=False, index=True)
    camera_name = Column(String, nullable=False)

    # PostGIS point, WGS84 (lat/long)
    location = Column(Geometry(geometry_type="POINT", srid=4326), nullable=False)

    department = Column(Enum(DepartmentEnum, name="department_enum"), nullable=False, index=True)
    camera_type = Column(Enum(CameraTypeEnum, name="camera_type_enum"), nullable=False, index=True)
    ownership = Column(Enum(OwnershipEnum, name="ownership_enum"), nullable=False)
    connectivity_status = Column(
        Enum(ConnectivityStatusEnum, name="connectivity_status_enum"),
        nullable=False,
        default=ConnectivityStatusEnum.OFFLINE,
        index=True,
    )

    # {retention_days: int, storage_type: 'local'|'cloud'|'hybrid', capacity_tb: float}
    storage_details = Column(JSONB, nullable=False, default=dict)

    health_status = Column(
        Enum(HealthStatusEnum, name="health_status_enum"),
        nullable=False,
        default=HealthStatusEnum.OPERATIONAL,
        index=True,
    )

    installation_date = Column(Date, nullable=False)
    last_ping = Column(DateTime(timezone=True), nullable=True)

    # Placeholder handoff field for Model 2 (live streaming layer)
    stream_endpoint = Column(String, nullable=True)

    registered_by_vendor_key = Column(String, nullable=True)  # audit trail for vendor-pushed rows

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_cameras_department_type", "department", "camera_type"),
        Index("ix_cameras_location", "location", postgresql_using="gist"),
    )
