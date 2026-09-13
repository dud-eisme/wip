"""
routers/sources.py
Register camera sources (RTSP/HTTP/file), and control/inspect the
background capture workers that back the live feed relay.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from database import get_db
from models import CameraSource, CameraRef, User
from schemas import (
    CameraSourceCreate,
    CameraSourceUpdate,
    CameraSourceOut,
    CameraSourceListResponse,
    WorkerStatus,
    WorkersStatusResponse,
)
from auth import get_current_user, department_scope
from camera_worker import source_manager

router = APIRouter(prefix="/api/v2/sources", tags=["Sources"])


def _get_camera_or_404(db: Session, camera_id: uuid.UUID) -> CameraRef:
    camera = db.query(CameraRef).filter(CameraRef.id == camera_id).first()
    if not camera:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No camera with id '{camera_id}' found in the Model 1 registry.",
        )
    return camera


@router.post("", response_model=CameraSourceOut, status_code=status.HTTP_201_CREATED)
def register_source(
    payload: CameraSourceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    camera = _get_camera_or_404(db, payload.camera_id)

    scoped_department = department_scope(current_user)
    if scoped_department is not None and camera.department != scoped_department:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Camera belongs to '{camera.department.value}', not your department.",
        )

    source = CameraSource(
        id=uuid.uuid4(),
        camera_id=payload.camera_id,
        source_name=payload.source_name,
        source_type=payload.source_type,
        source_url=payload.source_url,
        is_active=payload.is_active,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.get("", response_model=CameraSourceListResponse)
def list_sources(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    is_active: Optional[bool] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    scoped_department = department_scope(current_user)

    query = db.query(CameraSource)
    if scoped_department is not None:
        query = query.join(CameraRef, CameraRef.id == CameraSource.camera_id).filter(
            CameraRef.department == scoped_department
        )
    if is_active is not None:
        query = query.filter(CameraSource.is_active == is_active)

    total = query.count()
    rows = query.order_by(CameraSource.created_at.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": rows}


@router.get("/status/workers", response_model=WorkersStatusResponse)
def workers_status(current_user: User = Depends(get_current_user)):
    statuses = source_manager.list_status()
    return {
        "max_concurrent_sources": source_manager.max_concurrent_sources,
        "active_worker_count": source_manager.active_count(),
        "workers": statuses,
    }


@router.get("/{source_id}", response_model=CameraSourceOut)
def get_source(
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    source = db.query(CameraSource).filter(CameraSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")

    scoped_department = department_scope(current_user)
    if scoped_department is not None:
        camera = _get_camera_or_404(db, source.camera_id)
        if camera.department != scoped_department:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this source.")
    return source


@router.patch("/{source_id}", response_model=CameraSourceOut)
def update_source(
    source_id: uuid.UUID,
    payload: CameraSourceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    source = db.query(CameraSource).filter(CameraSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")

    scoped_department = department_scope(current_user)
    if scoped_department is not None:
        camera = _get_camera_or_404(db, source.camera_id)
        if camera.department != scoped_department:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to modify this source.")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(source, field, value)
    db.commit()
    db.refresh(source)
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    source = db.query(CameraSource).filter(CameraSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")

    scoped_department = department_scope(current_user)
    if scoped_department is not None:
        camera = _get_camera_or_404(db, source.camera_id)
        if camera.department != scoped_department:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete this source.")

    source_manager.stop(source_id)
    db.delete(source)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Worker control (start/stop the background decoder for a source)
# ---------------------------------------------------------------------------

@router.post("/{source_id}/start", response_model=WorkerStatus)
def start_source_worker(
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    source = db.query(CameraSource).filter(CameraSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")
    if not source.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Source is marked inactive.")

    try:
        worker = source_manager.start(source_id, source.source_url)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return worker.status()


@router.post("/{source_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
def stop_source_worker(
    source_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    source_manager.stop(source_id)
    return None

