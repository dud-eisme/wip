"""
routers/stream.py
Serves the live decoded frames from a running CameraWorker as an MJPEG
stream over HTTP — viewable directly in a browser <img> tag or a
dashboard's video widget, no special video player needed.
"""
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from models import CameraSource, CameraRef, User
from auth import get_current_user, department_scope
from camera_worker import source_manager, STREAM_TARGET_FPS

router = APIRouter(prefix="/api/v2/sources", tags=["Feed Relay"])

_BOUNDARY = "frame"


def _mjpeg_generator(source_id: uuid.UUID):
    frame_interval = 1.0 / STREAM_TARGET_FPS
    while True:
        worker = source_manager.get(source_id)
        if worker is None or not worker.is_running:
            # Worker was stopped externally — end the stream cleanly.
            break
        frame = worker.buffer.get_frame()
        if frame is not None:
            yield (
                b"--" + _BOUNDARY.encode() + b"\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n"
            )
        time.sleep(frame_interval)


@router.get("/{source_id}/stream")
def stream_source(
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    source = db.query(CameraSource).filter(CameraSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")

    scoped_department = department_scope(current_user)
    if scoped_department is not None:
        camera = db.query(CameraRef).filter(CameraRef.id == source.camera_id).first()
        if camera and camera.department != scoped_department:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this feed.")

    worker = source_manager.get(source_id)
    if worker is None or not worker.is_running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Source worker is not running. Start it first: POST /api/v2/sources/{source_id}/start",
        )

    return StreamingResponse(
        _mjpeg_generator(source_id),
        media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY}",
    )


@router.get("/{source_id}/snapshot")
def snapshot_source(
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns a single current JPEG frame — cheaper than opening a full
    MJPEG stream when a dashboard just needs a thumbnail."""
    from fastapi import Response

    source = db.query(CameraSource).filter(CameraSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")

    scoped_department = department_scope(current_user)
    if scoped_department is not None:
        camera = db.query(CameraRef).filter(CameraRef.id == source.camera_id).first()
        if camera and camera.department != scoped_department:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this feed.")

    worker = source_manager.get(source_id)
    if worker is None or not worker.is_running:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Source worker is not running.")

    frame = worker.buffer.get_frame()
    if frame is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No frame captured yet.")

    return Response(content=frame, media_type="image/jpeg")
