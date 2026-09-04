"""
routers/cameras.py
Manual entry, bulk import, vendor onboarding webhook, and GIS/search APIs.
"""
import io
import uuid
from datetime import date, datetime
from typing import Optional, List

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query
from pydantic import ValidationError
from sqlalchemy import func, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from models import Camera, User, DepartmentEnum, CameraTypeEnum, ConnectivityStatusEnum, HealthStatusEnum
from schemas import (
    CameraCreate,
    CameraUpdate,
    CameraOut,
    CameraListResponse,
    BulkUploadResult,
    RowError,
    VendorCameraRegister,
)
from auth import get_current_user, department_scope, enforce_department_write, get_vendor_identity, VendorIdentity
from geo_utils import make_point, camera_to_dict

router = APIRouter(prefix="/api/v1/cameras", tags=["Cameras"])

MAX_BULK_ROWS = 20000
ALLOWED_UPLOAD_EXTENSIONS = (".csv", ".xlsx", ".xls")


# ---------------------------------------------------------------------------
# Manual entry
# ---------------------------------------------------------------------------

@router.post("", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
def create_camera(
    payload: CameraCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    enforce_department_write(payload.department, current_user)

    existing = db.query(Camera).filter(Camera.camera_identifier == payload.camera_identifier).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"camera_identifier '{payload.camera_identifier}' already exists.",
        )

    camera = Camera(
        id=uuid.uuid4(),
        camera_identifier=payload.camera_identifier,
        camera_name=payload.camera_name,
        location=make_point(payload.latitude, payload.longitude),
        department=payload.department,
        camera_type=payload.camera_type,
        ownership=payload.ownership,
        connectivity_status=payload.connectivity_status,
        storage_details=payload.storage_details.model_dump(mode="json"),
        health_status=payload.health_status,
        installation_date=payload.installation_date,
        last_ping=payload.last_ping,
        stream_endpoint=payload.stream_endpoint,
    )
    db.add(camera)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Camera could not be created (constraint violation).")
    db.refresh(camera)
    return camera_to_dict(camera)


# ---------------------------------------------------------------------------
# GIS & search
# ---------------------------------------------------------------------------

@router.get("", response_model=CameraListResponse)
def list_cameras(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    department: Optional[DepartmentEnum] = Query(None),
    camera_type: Optional[CameraTypeEnum] = Query(None),
    connectivity_status: Optional[ConnectivityStatusEnum] = Query(None, description="Filter by connectivity status"),
    health_status: Optional[HealthStatusEnum] = Query(None, description="Filter by health status"),
    bbox: Optional[str] = Query(
        None,
        description="Bounding box spatial search as 'min_lon,min_lat,max_lon,max_lat'",
    ),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    scoped_department = department_scope(current_user)

    query = db.query(Camera)

    if scoped_department is not None:
        query = query.filter(Camera.department == scoped_department)
    elif department is not None:
        query = query.filter(Camera.department == department)

    if camera_type is not None:
        query = query.filter(Camera.camera_type == camera_type)
    if connectivity_status is not None:
        query = query.filter(Camera.connectivity_status == connectivity_status)
    if health_status is not None:
        query = query.filter(Camera.health_status == health_status)

    if bbox is not None:
        try:
            min_lon, min_lat, max_lon, max_lat = (float(x) for x in bbox.split(","))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="bbox must be 'min_lon,min_lat,max_lon,max_lat'",
            )
        if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180 and -90 <= min_lat <= 90 and -90 <= max_lat <= 90):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bbox coordinates out of range.")
        envelope = func.ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326)
        query = query.filter(func.ST_Intersects(Camera.location, envelope))

    total = query.count()
    rows = query.order_by(Camera.created_at.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": [camera_to_dict(c) for c in rows]}


@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(
    camera_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found.")

    scoped_department = department_scope(current_user)
    if scoped_department is not None and camera.department != scoped_department:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this camera.")
    return camera_to_dict(camera)


@router.patch("/{camera_id}", response_model=CameraOut)
def update_camera(
    camera_id: uuid.UUID,
    payload: CameraUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found.")

    enforce_department_write(camera.department, current_user)

    data = payload.model_dump(exclude_unset=True)

    if "latitude" in data or "longitude" in data:
        lat = data.pop("latitude", None)
        lon = data.pop("longitude", None)
        from geoalchemy2.shape import to_shape
        current_point = to_shape(camera.location)
        new_lat = lat if lat is not None else current_point.y
        new_lon = lon if lon is not None else current_point.x
        camera.location = make_point(new_lat, new_lon)

    if "storage_details" in data and data["storage_details"] is not None:
        data["storage_details"] = data["storage_details"] if isinstance(data["storage_details"], dict) else data["storage_details"].model_dump(mode="json")

    for field, value in data.items():
        setattr(camera, field, value)

    db.commit()
    db.refresh(camera)
    return camera_to_dict(camera)


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_camera(
    camera_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found.")
    enforce_department_write(camera.department, current_user)
    db.delete(camera)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Bulk import pipeline (CSV / Excel)
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = {
    "camera_identifier",
    "camera_name",
    "latitude",
    "longitude",
    "department",
    "camera_type",
    "ownership",
    "connectivity_status",
    "retention_days",
    "storage_type",
    "capacity_tb",
    "health_status",
    "installation_date",
}


def _row_to_camera_create(row: dict) -> CameraCreate:
    """Maps a flat spreadsheet row into the nested CameraCreate schema."""
    storage_details = {
        "retention_days": row.get("retention_days"),
        "storage_type": row.get("storage_type"),
        "capacity_tb": row.get("capacity_tb"),
    }
    payload = {
        "camera_identifier": row.get("camera_identifier"),
        "camera_name": row.get("camera_name"),
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "department": row.get("department"),
        "camera_type": row.get("camera_type"),
        "ownership": row.get("ownership"),
        "connectivity_status": row.get("connectivity_status") or "Offline",
        "storage_details": storage_details,
        "health_status": row.get("health_status") or "Operational",
        "installation_date": row.get("installation_date"),
        "last_ping": row.get("last_ping") or None,
        "stream_endpoint": row.get("stream_endpoint") or None,
    }
    return CameraCreate(**payload)


@router.post("/bulk-upload", response_model=BulkUploadResult)
def bulk_upload_cameras(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    filename = (file.filename or "").lower()
    if not filename.endswith(ALLOWED_UPLOAD_EXTENSIONS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type. Allowed: {ALLOWED_UPLOAD_EXTENSIONS}",
        )

    raw_bytes = file.file.read()
    try:
        if filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(raw_bytes), dtype=str)
        else:
            df = pd.read_excel(io.BytesIO(raw_bytes), dtype=str)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Could not parse file: {exc}")

    if df.empty:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file has no data rows.")
    if len(df) > MAX_BULK_ROWS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"File exceeds max of {MAX_BULK_ROWS} rows.")

    missing_columns = REQUIRED_COLUMNS - set(c.strip() for c in df.columns)
    if missing_columns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Missing required columns: {sorted(missing_columns)}",
        )

    df = df.where(pd.notnull(df), None)

    scoped_department = department_scope(current_user)

    errors: List[RowError] = []
    validated: List[CameraCreate] = []
    seen_identifiers_in_file = set()

    for idx, raw_row in enumerate(df.to_dict(orient="records")):
        row_number = idx + 2  # header is row 1
        row_errors: List[str] = []

        # type coercion for numeric fields (everything is read as str)
        row = dict(raw_row)
        for numeric_field in ("latitude", "longitude", "retention_days", "capacity_tb"):
            if row.get(numeric_field) is not None:
                try:
                    row[numeric_field] = float(row[numeric_field])
                except (TypeError, ValueError):
                    row_errors.append(f"{numeric_field} is not numeric: {row.get(numeric_field)!r}")

        identifier = row.get("camera_identifier")
        if identifier in seen_identifiers_in_file:
            row_errors.append(f"Duplicate camera_identifier within file: {identifier!r}")
        elif identifier:
            seen_identifiers_in_file.add(identifier)

        if row_errors:
            errors.append(RowError(row_number=row_number, errors=row_errors, raw_data=raw_row))
            continue

        try:
            camera_create = _row_to_camera_create(row)
        except ValidationError as ve:
            row_errors = [f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in ve.errors()]
            errors.append(RowError(row_number=row_number, errors=row_errors, raw_data=raw_row))
            continue

        if scoped_department is not None and camera_create.department != scoped_department:
            errors.append(
                RowError(
                    row_number=row_number,
                    errors=[f"User not authorized to upload cameras for department '{camera_create.department.value}'."],
                    raw_data=raw_row,
                )
            )
            continue

        validated.append(camera_create)

    # Check duplicates against existing DB records in one query
    if validated:
        identifiers = [c.camera_identifier for c in validated]
        existing = {
            row[0]
            for row in db.query(Camera.camera_identifier).filter(Camera.camera_identifier.in_(identifiers)).all()
        }
        if existing:
            still_valid = []
            for c in validated:
                if c.camera_identifier in existing:
                    errors.append(
                        RowError(
                            row_number=-1,
                            errors=[f"camera_identifier '{c.camera_identifier}' already exists in database."],
                            raw_data=c.model_dump(mode="json"),
                        )
                    )
                else:
                    still_valid.append(c)
            validated = still_valid

    inserted_count = 0
    if validated:
        try:
            camera_objs = [
                Camera(
                    id=uuid.uuid4(),
                    camera_identifier=c.camera_identifier,
                    camera_name=c.camera_name,
                    location=make_point(c.latitude, c.longitude),
                    department=c.department,
                    camera_type=c.camera_type,
                    ownership=c.ownership,
                    connectivity_status=c.connectivity_status,
                    storage_details=c.storage_details.model_dump(mode="json"),
                    health_status=c.health_status,
                    installation_date=c.installation_date,
                    last_ping=c.last_ping,
                    stream_endpoint=c.stream_endpoint,
                )
                for c in validated
            ]
            db.bulk_save_objects(camera_objs)
            db.commit()
            inserted_count = len(camera_objs)
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Bulk insert transaction failed and was rolled back: {exc.orig}",
            )

    return BulkUploadResult(
        total_rows=len(df),
        inserted_count=inserted_count,
        failed_count=len(errors),
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Vendor onboarding webhook (API-key authenticated)
# ---------------------------------------------------------------------------

@router.post("/register-vendor", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
def register_vendor_camera(
    payload: VendorCameraRegister,
    db: Session = Depends(get_db),
    vendor: VendorIdentity = Depends(get_vendor_identity),
):
    if payload.department.value != vendor.department:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"This API key is authorized to register cameras for '{vendor.department}' only, "
                f"got department='{payload.department.value}'."
            ),
        )

    existing = db.query(Camera).filter(Camera.camera_identifier == payload.camera_identifier).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"camera_identifier '{payload.camera_identifier}' already exists.",
        )

    camera = Camera(
        id=uuid.uuid4(),
        camera_identifier=payload.camera_identifier,
        camera_name=payload.camera_name,
        location=make_point(payload.latitude, payload.longitude),
        department=payload.department,
        camera_type=payload.camera_type,
        ownership=payload.ownership,
        connectivity_status=payload.connectivity_status,
        storage_details=payload.storage_details.model_dump(mode="json"),
        health_status=payload.health_status,
        installation_date=payload.installation_date,
        last_ping=payload.last_ping,
        stream_endpoint=payload.stream_endpoint,
        registered_by_vendor_key=vendor.api_key,
    )
    db.add(camera)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Camera could not be registered (constraint violation).")
    db.refresh(camera)
    return camera_to_dict(camera)
