"""
models.py
SQLAlchemy models for Model 2 (Live Feed Relay + ANPR).

`User` maps to the *existing* `users` table created by Model 1 — same
columns, same table name. create_all() skips it because it already exists;
this class exists purely so Model 2's own auth.py can query it.

`CameraSource`, `AnprEvent`, `AnprJob` are new tables owned by Model 2.
`CameraSource.camera_id` and `AnprEvent.camera_id` are real foreign keys
into Model 1's `cameras` table (same database) — we reference it by table
name only ("cameras.id"), without importing Model 1's codebase, since the
table already exists physically in the shared database.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    String,
    Enum,
    DateTime,
    ForeignKey,
    Float,
    Boolean,
    Integer,
    Text,
    Index,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from database import Base


# ---------------------------------------------------------------------------
# Shared `users` table (owned/created by Model 1 — mapped here read-only-ish
# for auth checks; Model 2 does not create new users)
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


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"extend_existing": True}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    department = Column(
        Enum(DepartmentEnum, name="department_enum", values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        nullable=False,
        index=True,
    )
    role = Column(
        Enum(UserRoleEnum, name="user_role_enum", values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        nullable=False,
        default=UserRoleEnum.VIEWER,
    )
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ---------------------------------------------------------------------------
# Minimal read-only reference to Model 1's `cameras` table.
# Only maps the columns Model 2 actually needs (id, identifier, department).
# Model 2 never writes to this table or includes it in create_all's
# concern beyond the FK references above — it's owned by Model 1.
# ---------------------------------------------------------------------------

class CameraRef(Base):
    __tablename__ = "cameras"
    __table_args__ = {"extend_existing": True}

    id = Column(UUID(as_uuid=True), primary_key=True)
    camera_identifier = Column(String)
    camera_name = Column(String)
    department = Column(
        Enum(DepartmentEnum, name="department_enum", values_callable=lambda enum_cls: [e.value for e in enum_cls])
    )


# ---------------------------------------------------------------------------
# Sources registry
# ---------------------------------------------------------------------------

class SourceTypeEnum(str, enum.Enum):
    RTSP = "rtsp"
    HTTP = "http"
    FILE = "file"  # recorded clip, used for ANPR testing/demo without a live feed


class CameraSource(Base):
    """
    Links a Model 1 `cameras` row to an actual playable video source
    (RTSP URL, HTTP stream, or a local recorded file for demo/testing).
    A single camera could technically have more than one source registered
    over time (e.g. re-pointed to a new NVR), so this is its own table
    rather than a column on `cameras`.
    """
    __tablename__ = "camera_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_id = Column(UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False, index=True)
    source_name = Column(String, nullable=False)
    source_type = Column(
        Enum(SourceTypeEnum, name="source_type_enum", values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        nullable=False,
    )
    source_url = Column(String, nullable=False)  # rtsp://..., http://..., or local file path

    # OPTIONAL low-latency browser-preview URL (WHEP endpoint), e.g.
    # http://<host>:8889/stream/<id>/whep
    #
    # This is deliberately a SECOND url on the same source rather than its
    # own source_type. WebRTC cannot be ingested server-side: OpenCV/ffmpeg
    # can't consume a WHEP endpoint (it needs an SDP offer/answer exchange
    # then SRTP/ICE), so a webrtc-only source could never run a capture
    # worker or an ANPR job. Keeping it as an extra field means source_url
    # (rtsp/hls) still drives all server-side work — relay, ANPR, overlay —
    # while the browser can additionally play webrtc_url directly for
    # near-zero-latency viewing. The Sentinel catalogue publishes all three
    # URLs per camera, so both can be filled from one /api/ingest entry.
    webrtc_url = Column(String, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ---------------------------------------------------------------------------
# ANPR jobs (a "run the pipeline on this source/clip" unit of work)
# ---------------------------------------------------------------------------

class JobStatusEnum(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AnprJob(Base):
    __tablename__ = "anpr_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id = Column(UUID(as_uuid=True), ForeignKey("camera_sources.id", ondelete="SET NULL"), nullable=True)
    camera_id = Column(UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True)
    status = Column(
        Enum(JobStatusEnum, name="job_status_enum", values_callable=lambda enum_cls: [e.value for e in enum_cls]),
        nullable=False,
        default=JobStatusEnum.QUEUED,
        index=True,
    )
    processed_frames = Column(Integer, default=0)
    events_found = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# ANPR detections ("events")
# ---------------------------------------------------------------------------

class AnprEvent(Base):
    __tablename__ = "anpr_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("anpr_jobs.id", ondelete="CASCADE"), nullable=True, index=True)
    camera_id = Column(UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="SET NULL"), nullable=True, index=True)
    source_id = Column(UUID(as_uuid=True), ForeignKey("camera_sources.id", ondelete="SET NULL"), nullable=True)

    plate_text = Column(String, nullable=False, index=True)
    confidence = Column(Float, nullable=False)  # 0.0 - 1.0
    detected_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    frame_number = Column(Integer, nullable=True)
    snapshot_path = Column(String, nullable=True)  # cropped plate image, saved to disk

    is_flagged = Column(Boolean, default=False, nullable=False, index=True)  # "of interest"
    flagged_note = Column(Text, nullable=True)

    # -----------------------------------------------------------------
    # Vehicle recognition (vehicle_pipeline.py). All nullable — "not
    # determined" is a real, expected answer (see that module's
    # docstring on why make/model especially are often unknowable from
    # CCTV footage), not something to backfill with a guess.
    # -----------------------------------------------------------------
    vehicle_type = Column(String, nullable=True, index=True)
    vehicle_type_confidence = Column(Float, nullable=True)
    vehicle_colour = Column(String, nullable=True, index=True)
    vehicle_colour_confidence = Column(Float, nullable=True)
    vehicle_make = Column(String, nullable=True, index=True)
    vehicle_model = Column(String, nullable=True)
    vehicle_make_model_confidence = Column(Float, nullable=True)
    # 'vision' or 'registry' (VAHAN) — kept as plain text rather than a
    # Postgres enum on purpose: adding an enum value later needs its own
    # ALTER TYPE migration (see migrate_add_source_type_values.py), and
    # this field isn't worth that ceremony.
    vehicle_make_model_source = Column(String, nullable=True)
    # 'x1,y1,x2,y2' of the whole-vehicle box in the source frame, so a
    # snapshot can be re-cropped to the full vehicle later without
    # re-running detection.
    vehicle_bbox = Column(String, nullable=True)

    # -----------------------------------------------------------------
    # Multi-frame consensus (plate_consensus.py). This event represents
    # one TRACK (a vehicle's whole pass through frame), not one single
    # OCR read — confidence above is the mean of the reads that voted;
    # these two describe how much to trust that mean.
    # -----------------------------------------------------------------
    consensus_read_count = Column(Integer, nullable=True)
    # Weakest per-character agreement across the plate, 0-1. Two events
    # with identical `confidence` can have very different
    # `consensus_agreement` — e.g. "11 reads, every character
    # unanimous" vs "3 reads, one position split 55/45" — and this is
    # the field that tells them apart for review.
    consensus_agreement = Column(Float, nullable=True)
    # Other whole-string reads seen for this track, most common first,
    # comma-separated. A human reviewer flagging a doubtful plate wants
    # to see what else OCR considered, not just the winner.
    consensus_alternatives = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_anpr_events_camera_time", "camera_id", "detected_at"),
    )
