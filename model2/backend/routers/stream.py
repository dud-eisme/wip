"""
routers/stream.py
Serves the live decoded frames from a running CameraWorker as an MJPEG
stream over HTTP — viewable directly in a browser <img> tag or a
dashboard's video widget, no special video player needed.

PATCH NOTE: these two endpoints use get_current_user_flexible instead of
the strict get_current_user, specifically because a plain <img src="...">
tag cannot send a custom Authorization header — the frontend instead
passes the token as ?token=... on the URL. Every other endpoint in this
service keeps using strict header-only auth.
"""
import logging
import os
import time
import uuid

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from models import CameraSource, CameraRef, User
from auth import get_current_user_flexible, department_scope
from camera_worker import source_manager, STREAM_TARGET_FPS
from overlay import latest_detections, draw_detections, scale_detections

router = APIRouter(prefix="/api/v2/sources", tags=["Feed Relay"])
logger = logging.getLogger("cctv_model2.stream")

_BOUNDARY = "frame"

# Quality used when re-encoding a frame we had to decode in order to draw
# on it. Only applies to frames that actually carry detections; frames
# with no boxes are passed through as the worker encoded them, untouched.
OVERLAY_JPEG_QUALITY = int(os.getenv("STREAM_OVERLAY_JPEG_QUALITY", "80"))


def _with_overlay(frame_bytes: bytes, source_id: uuid.UUID) -> bytes:
    """Draws any currently-cached ANPR detections for this source onto an
    already-JPEG-encoded frame, returning new JPEG bytes.

    The worker's buffer hands us encoded JPEG, not an ndarray, so drawing
    costs a decode + re-encode. That is why the no-detections case returns
    the original bytes immediately: a source with no ANPR job attached
    (the common case) keeps the old zero-copy passthrough behaviour and
    pays nothing for this feature.

    Any failure here falls back to the un-overlaid frame — a feed that
    keeps flowing without boxes beats a feed that dies over a bad decode.
    """
    detections, detect_size = latest_detections.get_entry(str(source_id))
    if not detections:
        return frame_bytes

    try:
        buf = np.frombuffer(frame_bytes, dtype=np.uint8)
        frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if frame is None:
            return frame_bytes

        # The ANPR job decodes the source on its own VideoCapture, so its
        # frames need not match what the worker buffered. Rescale rather
        # than trusting the two to agree.
        height, width = frame.shape[:2]
        if detect_size is not None:
            detections = scale_detections(detections, detect_size, (width, height))

        # draw_detections() returns a copy; the decoded array is ours
        # alone anyway, but the contract is what lets the same helper be
        # pointed at the worker's shared latest-frame buffer unchanged.
        frame = draw_detections(frame, detections)

        ok, encoded = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), OVERLAY_JPEG_QUALITY]
        )
        if not ok:
            return frame_bytes
        return encoded.tobytes()
    except Exception:  # noqa: BLE001 — never let overlay drawing kill a stream
        logger.exception("Overlay draw failed for source %s; serving raw frame", source_id)
        return frame_bytes


def _mjpeg_generator(source_id: uuid.UUID):
    frame_interval = 1.0 / STREAM_TARGET_FPS
    while True:
        worker = source_manager.get(source_id)
        if worker is None or not worker.is_running:
            # Worker was stopped externally — end the stream cleanly.
            break
        frame = worker.buffer.get_frame()
        if frame is not None:
            frame = _with_overlay(frame, source_id)
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
    current_user: User = Depends(get_current_user_flexible),
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
    current_user: User = Depends(get_current_user_flexible),
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

    # Same treatment as the MJPEG relay, so a dashboard thumbnail and the
    # live view don't disagree about what was detected.
    return Response(content=_with_overlay(frame, source_id), media_type="image/jpeg")
