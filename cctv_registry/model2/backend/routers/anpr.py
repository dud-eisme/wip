"""
routers/anpr.py
Runs the ANPR pipeline (YOLO + EasyOCR) against a registered source or
recorded clip as a background job, and exposes search/tagging over the
resulting detection events.

Jobs run in a plain background thread (not Celery/RQ) to keep this
demo/spec-scale project dependency-light. For real production volume,
swap `threading.Thread` here for a proper task queue — the DB writes and
job-status contract stay identical either way.
"""
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

import cv2
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from database import get_db, SessionLocal
from models import AnprJob, AnprEvent, CameraSource, CameraRef, JobStatusEnum, User
from schemas import (
    AnprJobCreate,
    AnprJobOut,
    AnprEventOut,
    AnprEventListResponse,
    EventFlagUpdate,
)
from auth import get_current_user, department_scope
import anpr_pipeline
import storage

router = APIRouter(prefix="/api/v2/anpr", tags=["ANPR"])

ANPR_FRAME_SKIP_DEFAULT = int(os.getenv("ANPR_FRAME_SKIP", "5"))


# ---------------------------------------------------------------------------
# Job execution (runs in a background thread)
# ---------------------------------------------------------------------------

def _run_anpr_job(job_id: uuid.UUID, source_url: str, camera_id: Optional[uuid.UUID], frame_skip: int, max_frames: int):
    db = SessionLocal()
    try:
        job = db.query(AnprJob).filter(AnprJob.id == job_id).first()
        if not job:
            return
        job.status = JobStatusEnum.RUNNING
        job.started_at = datetime.now(timezone.utc)
        db.commit()

        cap = cv2.VideoCapture(source_url)
        if not cap.isOpened():
            job.status = JobStatusEnum.FAILED
            job.error_message = f"Could not open source: {source_url}"
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            return

        frame_number = 0
        processed = 0
        events_found = 0

        try:
            while processed < max_frames:
                ok, frame = cap.read()
                if not ok:
                    break  # end of clip / stream ended
                frame_number += 1

                if frame_number % frame_skip != 0:
                    continue

                detections = anpr_pipeline.detect_plates_in_frame(frame)
                processed += 1

                for det in detections:
                    event_id = uuid.uuid4()
                    snapshot_path = None
                    x1, y1, x2, y2 = det.bbox
                    crop = frame[y1:y2, x1:x2]
                    if crop.size > 0:
                        relpath = storage.build_snapshot_relpath(camera_id, event_id)
                        try:
                            snapshot_path = storage.save_snapshot(relpath, crop)
                        except Exception:
                            snapshot_path = None  # detection still gets logged even if the disk write fails

                    event = AnprEvent(
                        id=event_id,
                        job_id=job_id,
                        camera_id=camera_id,
                        source_id=job.source_id,
                        plate_text=det.plate_text,
                        confidence=det.confidence,
                        frame_number=frame_number,
                        snapshot_path=snapshot_path,
                    )
                    db.add(event)
                    events_found += 1

                job.processed_frames = processed
                job.events_found = events_found
                db.commit()
        finally:
            cap.release()

        job.status = JobStatusEnum.COMPLETED
        job.completed_at = datetime.now(timezone.utc)
        db.commit()

    except Exception as exc:  # noqa: BLE001 — surfacing any pipeline error onto the job row
        db.rollback()
        job = db.query(AnprJob).filter(AnprJob.id == job_id).first()
        if job:
            job.status = JobStatusEnum.FAILED
            job.error_message = str(exc)
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Job endpoints
# ---------------------------------------------------------------------------

@router.post("/jobs", response_model=AnprJobOut, status_code=status.HTTP_202_ACCEPTED)
def create_anpr_job(
    payload: AnprJobCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    available, reason = anpr_pipeline.is_available()
    if not available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "ANPR pipeline dependencies are not installed. Run: "
                "pip install ultralytics==8.3.0 easyocr==1.7.2  "
                f"(underlying error: {reason})"
            ),
        )

    source = db.query(CameraSource).filter(CameraSource.id == payload.source_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")

    scoped_department = department_scope(current_user)
    if scoped_department is not None:
        camera = db.query(CameraRef).filter(CameraRef.id == source.camera_id).first()
        if camera and camera.department != scoped_department:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to process this source.")

    job = AnprJob(
        id=uuid.uuid4(),
        source_id=source.id,
        camera_id=source.camera_id,
        status=JobStatusEnum.QUEUED,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    frame_skip = payload.frame_skip or ANPR_FRAME_SKIP_DEFAULT
    thread = threading.Thread(
        target=_run_anpr_job,
        args=(job.id, source.source_url, source.camera_id, frame_skip, payload.max_frames),
        daemon=True,
    )
    thread.start()

    return job


@router.get("/jobs/{job_id}", response_model=AnprJobOut)
def get_anpr_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = db.query(AnprJob).filter(AnprJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return job


# ---------------------------------------------------------------------------
# Events search & tagging
# ---------------------------------------------------------------------------

@router.get("/events", response_model=AnprEventListResponse)
def search_events(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    plate: Optional[str] = Query(None, description="Partial or full plate text (case-insensitive)."),
    camera_id: Optional[uuid.UUID] = Query(None),
    from_time: Optional[datetime] = Query(None, alias="from"),
    to_time: Optional[datetime] = Query(None, alias="to"),
    is_flagged: Optional[bool] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    scoped_department = department_scope(current_user)

    query = db.query(AnprEvent)
    if scoped_department is not None:
        query = query.join(CameraRef, CameraRef.id == AnprEvent.camera_id).filter(
            CameraRef.department == scoped_department
        )
    if plate:
        query = query.filter(AnprEvent.plate_text.ilike(f"%{plate.upper()}%"))
    if camera_id:
        query = query.filter(AnprEvent.camera_id == camera_id)
    if from_time:
        query = query.filter(AnprEvent.detected_at >= from_time)
    if to_time:
        query = query.filter(AnprEvent.detected_at <= to_time)
    if is_flagged is not None:
        query = query.filter(AnprEvent.is_flagged == is_flagged)

    total = query.count()
    rows = query.order_by(AnprEvent.detected_at.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": rows}


@router.get("/events/{event_id}/snapshot")
def get_event_snapshot(
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns the saved cropped-plate image for a detection event, if one
    was captured. Access follows the same department scoping as the rest
    of the events API — served through this endpoint (not a raw static
    file mount) specifically so that scoping applies."""
    event = db.query(AnprEvent).filter(AnprEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found.")

    scoped_department = department_scope(current_user)
    if scoped_department is not None:
        camera = db.query(CameraRef).filter(CameraRef.id == event.camera_id).first()
        if camera and camera.department != scoped_department:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this snapshot.")

    if not event.snapshot_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No snapshot was saved for this event.")

    file_path = storage.resolve_snapshot_path(event.snapshot_path)
    if file_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot file is missing from disk.")

    return FileResponse(path=str(file_path), media_type="image/jpeg")


@router.patch("/events/{event_id}/flag", response_model=AnprEventOut)
def flag_event(
    event_id: uuid.UUID,
    payload: EventFlagUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event = db.query(AnprEvent).filter(AnprEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found.")

    scoped_department = department_scope(current_user)
    if scoped_department is not None:
        camera = db.query(CameraRef).filter(CameraRef.id == event.camera_id).first()
        if camera and camera.department != scoped_department:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to tag this event.")

    event.is_flagged = payload.is_flagged
    event.flagged_note = payload.flagged_note
    db.commit()
    db.refresh(event)
    return event
