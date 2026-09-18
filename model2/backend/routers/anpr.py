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
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import cv2
import numpy as np
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
from overlay import latest_detections, OverlayDetection
from camera_worker import source_manager
import anpr_pipeline
import vehicle_pipeline
import plate_consensus
import vahan
import storage
from collections import deque

router = APIRouter(prefix="/api/v2/anpr", tags=["ANPR"])
logger = logging.getLogger("cctv_model2.anpr")

ANPR_FRAME_SKIP_DEFAULT = int(os.getenv("ANPR_FRAME_SKIP", "5"))

# How often an RTSP-sourced job polls the CameraWorker's frame buffer for
# a newly-published frame (see _iter_rtsp_worker_frames below). This is
# just a responsiveness knob, not a decode throttle — the worker itself
# controls actual capture rate.
ANPR_RTSP_POLL_INTERVAL_SECONDS = float(os.getenv("ANPR_RTSP_POLL_INTERVAL_SECONDS", "0.05"))

# SUPERSEDED by plate_consensus.PlateTracker below. This used to
# de-duplicate repeat single-frame reads of a dwelling vehicle by picking
# whichever read had the higher confidence and discarding the rest.
# PlateTracker does the same job better: it tracks the same vehicle
# across frames the same way, but VOTES across every read instead of
# keeping one and throwing the others away, which is what actually helps
# on low-resolution footage (see plate_consensus.py's module docstring).
# Left here, unused, only so ANPR_DEDUP_WINDOW_SECONDS in an existing
# .env doesn't become an unrecognised-var surprise; it does nothing now.
ANPR_DEDUP_WINDOW_SECONDS = float(os.getenv("ANPR_DEDUP_WINDOW_SECONDS", "15"))

# How many PAST PROCESSED frames to keep decoded in memory per job, so a
# track that closes can still reach back to its best-confidence frame for
# vehicle recognition + the snapshot crop. Must comfortably exceed
# CONSENSUS_TRACK_TIMEOUT_FRAMES (processed-frame units, same as it) or a
# track would close and find its own best frame already evicted.
ANPR_FRAME_BUFFER_SIZE = int(
    os.getenv("ANPR_FRAME_BUFFER_SIZE", str(plate_consensus.CONSENSUS_TRACK_TIMEOUT_FRAMES + 20))
)

# Registry of stop signals for currently-running jobs, keyed by job_id.
# Mirrors SourceManager's _workers dict in camera_worker.py — same need,
# same shape: a background thread has to be reachable from an HTTP
# request that didn't start it. Only continuous jobs are ever put here;
# a normal one-shot job just runs to max_frames/source-end on its own and
# is never looked up by this dict.
_job_stop_events: dict[str, threading.Event] = {}
_job_stop_events_lock = threading.Lock()

# source_id (str) -> job_id of its currently-live continuous job, if any.
# Guarded by the same lock as _job_stop_events since they're always
# updated together. This is what makes toggle-ON idempotent and lets the
# frontend recover toggle state after a refresh (see
# get_active_continuous_job below) without tracking job_id itself.
_source_continuous_jobs: dict[str, uuid.UUID] = {}


def _plates_match(a: str, b: str) -> bool:
    """LEGACY — no longer called from the job loop; PlateTracker's own
    _hamming-distance match (plate_consensus.py) replaced this. Left
    defined in case anything else in the codebase still imports it.

    True if two plate reads are the same plate. Exact match, or a
    single-character difference — OCR jitter on a dwelling vehicle
    typically flips one ambiguous glyph (8/B, 0/O, 1/I) between frames,
    and treating those as distinct plates defeats the whole point of
    de-duplicating."""
    if a == b:
        return True
    if len(a) != len(b):
        return False
    return sum(1 for ca, cb in zip(a, b) if ca != cb) == 1


# ---------------------------------------------------------------------------
# Frame sources
# ---------------------------------------------------------------------------
#
# RTSP is a live PUSH source (see camera_worker.py's own extensive comment
# on this). Whatever reads it has to drain it promptly or the network/NVR
# starts discarding packets, which corrupts subsequent decodes — the exact
# "mmco: unref short failure" / "co located POCs unavailable" / "error
# while decoding MB" errors this pipeline was producing. YOLO+EasyOCR
# between reads is not prompt, so a job that opened its OWN
# cv2.VideoCapture on an RTSP source would eventually fall behind no
# matter how it's tuned.
#
# The CameraWorker for this source already solves exactly this problem —
# it's the one thing draining the camera's RTSP socket at native rate for
# the live relay. So an ANPR job on an RTSP source never opens a second
# connection to the camera at all: it pulls whatever frame the worker most
# recently published. This also means every ANPR job (and the live view)
# for a camera shares ONE RTSP session to that camera, not one per
# consumer — the only design that scales past a handful of cameras.
#
# FILE/HTTP sources aren't push sources, so they keep the simple,
# unchanged cap.read()-per-frame approach below.


def _iter_rtsp_worker_frames(source_id: uuid.UUID, stop_event: Optional[threading.Event]):
    """Yields decoded frames pulled from the source's already-running
    CameraWorker buffer — never opens its own connection to the camera.

    Effectively takes over frame_skip's role for RTSP sources: a new
    frame only becomes available here as often as the worker publishes
    one (STREAM_TARGET_FPS, 8fps by default), not at the source's native
    rate. frame_skip is still applied on top by the caller if an even
    coarser sample is wanted.

    Raises RuntimeError if the worker isn't running (never started, or
    stopped externally mid-job) so the caller's existing failure handling
    can surface a clear reason instead of polling forever for frames that
    will never arrive.
    """
    last_seen_count = -1
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        worker = source_manager.get(source_id)
        if worker is None or not worker.is_running:
            raise RuntimeError(
                f"Feed-relay worker for source {source_id} is not running "
                f"(stopped externally, or never started)."
            )
        frame_count = worker.buffer.snapshot_status()["frames_captured"]
        if frame_count and frame_count != last_seen_count:
            last_seen_count = frame_count
            jpeg_bytes = worker.buffer.get_frame()
            if jpeg_bytes is not None:
                buf = np.frombuffer(jpeg_bytes, dtype=np.uint8)
                frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                if frame is not None:
                    yield frame
                    continue
        if stop_event is not None:
            if stop_event.wait(ANPR_RTSP_POLL_INTERVAL_SECONDS):
                return
        else:
            time.sleep(ANPR_RTSP_POLL_INTERVAL_SECONDS)


def _write_consensus_event(
    db: Session,
    job_id: uuid.UUID,
    camera_id: Optional[uuid.UUID],
    source_id: uuid.UUID,
    frame_buffer: dict,
    result: "plate_consensus.ConsensusPlate",
) -> "AnprEvent":
    """
    Turns one CLOSED track (a vehicle's whole pass through frame, voted
    across every read — see plate_consensus.py) into one AnprEvent row.

    Vehicle detection is run ONCE here, on the single best frame of the
    track, rather than every processed frame — the frame with the
    highest-confidence plate read is also usually the frame where the
    vehicle itself is most cleanly in view, and running it once per
    track instead of once per frame is most of the reason this is
    affordable at all.

    Never raises: vehicle recognition and the registry hook are both
    enrichments layered on top of a plate read that's already decided.
    A crash in either must not cost the plate event itself.
    """
    snapshot_frame = frame_buffer.get(result.best_frame)

    vehicle_type = vehicle_type_conf = None
    vehicle_colour = vehicle_colour_conf = None
    vehicle_make = vehicle_model = vehicle_mm_conf = vehicle_mm_source = None
    vehicle_bbox_str = None
    crop_source_frame = snapshot_frame
    crop_bbox = result.best_bbox

    if snapshot_frame is not None:
        try:
            vehicles = vehicle_pipeline.detect_vehicles_in_frame(snapshot_frame)
            plate_stub = anpr_pipeline.PlateDetection(
                plate_text=result.plate_text, confidence=result.confidence, bbox=result.best_bbox,
            )
            vehicles = vehicle_pipeline.attach_plates(vehicles, [plate_stub])
            matched = next((v for v in vehicles if v.plate_text == result.plate_text), None)
            if matched is not None:
                vehicle_type, vehicle_type_conf = matched.vehicle_type, matched.type_confidence
                vehicle_colour, vehicle_colour_conf = matched.colour, matched.colour_confidence
                vehicle_make, vehicle_model = matched.make, matched.model
                vehicle_mm_conf, vehicle_mm_source = matched.make_model_confidence, matched.make_model_source
                vehicle_bbox_str = ",".join(str(v) for v in matched.bbox)
                # Prefer cropping the SNAPSHOT around the whole vehicle
                # rather than just the plate — much more useful for a
                # human reviewing a flagged event later.
                crop_bbox = matched.bbox
        except Exception:  # noqa: BLE001
            logger.exception(
                "Vehicle recognition failed for track closing on plate %s — event still recorded",
                result.plate_text,
            )

    snapshot_path = None
    if crop_source_frame is not None:
        x1, y1, x2, y2 = crop_bbox
        crop = crop_source_frame[y1:y2, x1:x2]
        if crop.size > 0:
            event_id = uuid.uuid4()
            relpath = storage.build_snapshot_relpath(camera_id, event_id)
            try:
                snapshot_path = storage.save_snapshot(relpath, crop)
            except Exception:
                snapshot_path = None
        else:
            event_id = uuid.uuid4()
    else:
        event_id = uuid.uuid4()

    event = AnprEvent(
        id=event_id,
        job_id=job_id,
        camera_id=camera_id,
        source_id=source_id,
        plate_text=result.plate_text,
        confidence=result.confidence,
        frame_number=result.best_frame,
        snapshot_path=snapshot_path,
        vehicle_type=vehicle_type,
        vehicle_type_confidence=vehicle_type_conf,
        vehicle_colour=vehicle_colour,
        vehicle_colour_confidence=vehicle_colour_conf,
        vehicle_make=vehicle_make,
        vehicle_model=vehicle_model,
        vehicle_make_model_confidence=vehicle_mm_conf,
        vehicle_make_model_source=vehicle_mm_source,
        vehicle_bbox=vehicle_bbox_str,
        consensus_read_count=result.read_count,
        consensus_agreement=result.agreement,
        consensus_alternatives=",".join(result.alternatives) if result.alternatives else None,
    )
    db.add(event)
    return event


def _iter_file_frames(cap: "cv2.VideoCapture"):
    """Plain cap.read()-per-frame wrapper for FILE/HTTP sources. Not a
    push source, so a failed read here is a genuine end-of-clip — nothing
    to reconnect, unlike the RTSP path above."""
    while True:
        ok, frame = cap.read()
        if not ok:
            return
        yield frame


# ---------------------------------------------------------------------------
# Job execution (runs in a background thread)
# ---------------------------------------------------------------------------

def _run_anpr_job(
    job_id: uuid.UUID,
    source_id: uuid.UUID,
    source_url: str,
    camera_id: Optional[uuid.UUID],
    frame_skip: int,
    max_frames: int,
    stop_event: Optional[threading.Event] = None,
):
    db = SessionLocal()
    # Cache key for the live-stream overlay. Set once the job row is
    # loaded; used again in the outer `finally` to evict this source's
    # boxes however the job ends (completed, failed, or worker stopped).
    overlay_key: Optional[str] = None
    try:
        job = db.query(AnprJob).filter(AnprJob.id == job_id).first()
        if not job:
            return
        overlay_key = str(job.source_id)
        job.status = JobStatusEnum.RUNNING
        job.started_at = datetime.now(timezone.utc)
        db.commit()

        is_rtsp_source = source_url.lower().startswith("rtsp://")
        cap = None  # only opened for the FILE/HTTP path below

        if is_rtsp_source:
            # Ensure the shared worker is up — idempotent if it's already
            # running (e.g. someone's viewing the live feed). This is the
            # ONLY connection this job will ever make to the camera.
            try:
                source_manager.start(source_id, source_url)
            except RuntimeError as exc:
                job.status = JobStatusEnum.FAILED
                job.error_message = str(exc)
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
                return
            frame_source = _iter_rtsp_worker_frames(source_id, stop_event)
        else:
            cap = cv2.VideoCapture(source_url)
            if not cap.isOpened():
                job.status = JobStatusEnum.FAILED
                job.error_message = f"Could not open source: {source_url}"
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
                return
            frame_source = _iter_file_frames(cap)

        frame_number = 0
        processed = 0
        events_found = 0
        stopped_by_request = False

        # Tracks plates across frames and votes per character instead of
        # trusting any single frame's OCR read — see plate_consensus.py.
        # One tracker per job: two jobs on different cameras must never
        # share track state.
        tracker = plate_consensus.PlateTracker()

        # frame_number -> decoded frame, bounded to the last
        # ANPR_FRAME_BUFFER_SIZE PROCESSED frames. A closing track needs
        # to reach back to its best-confidence frame for the snapshot
        # crop and vehicle recognition; without this bound, a continuous
        # job (which can run for hours) would hold every frame it ever
        # decoded in memory.
        frame_buffer: "deque" = deque(maxlen=ANPR_FRAME_BUFFER_SIZE)
        frame_buffer_index: dict = {}

        try:
            # stop_event is only set for continuous jobs (see AnprJobCreate.
            # continuous) — a normal batch job passes None here and the
            # `or stop_event.is_set()` short-circuits away, so behavior for
            # existing one-shot jobs is unchanged. max_frames is still
            # respected even in continuous mode as an outer safety cap —
            # continuous just means "don't stop at max_frames the way a
            # batch job would", not "no cap ever".
            while processed < max_frames:
                if stop_event is not None and stop_event.is_set():
                    stopped_by_request = True
                    break

                frame = next(frame_source, None)
                if frame is None:
                    # RTSP: only returns None if stop_event fired while
                    # waiting for the next published frame — reconnects
                    # for the underlying camera socket are the worker's
                    # job, not this loop's (see _iter_rtsp_worker_frames).
                    # FILE/HTTP: a real end-of-clip.
                    if is_rtsp_source:
                        stopped_by_request = True
                    break
                frame_number += 1

                if frame_number % frame_skip != 0:
                    continue

                detections = anpr_pipeline.detect_plates_in_frame(frame)
                processed += 1

                # Keep this frame reachable for whichever track ends up
                # closing on it. The deque evicts the oldest entry once
                # full; mirror that eviction into the lookup dict so the
                # two never drift apart and frame_buffer_index doesn't
                # grow unbounded over a long continuous job.
                if len(frame_buffer) == frame_buffer.maxlen and frame_buffer:
                    evicted_frame_number, _ = frame_buffer[0]
                    frame_buffer_index.pop(evicted_frame_number, None)
                frame_buffer.append((frame_number, frame))
                frame_buffer_index[frame_number] = frame

                # Hand the results to the MJPEG relay (routers/stream.py).
                # Published unconditionally, including the empty list: an
                # empty set is what makes boxes disappear the moment a
                # plate leaves frame, instead of lingering until the
                # DETECTION_TTL_SECONDS window expires.
                frame_h, frame_w = frame.shape[:2]
                latest_detections.set(
                    overlay_key,
                    [
                        OverlayDetection(
                            bbox=d.bbox,
                            label=d.plate_text,
                            confidence=d.confidence,
                        )
                        for d in detections
                    ],
                    frame_size=(frame_w, frame_h),
                )

                # Feed this frame's raw reads into the tracker. Nothing
                # is written to the DB yet — a track only becomes an
                # event once it CLOSES (the vehicle leaves frame, or the
                # track goes stale), because voting across every read
                # needs to see all of them first. See plate_consensus.py.
                for closed_track in tracker.update(frame_number, detections):
                    _write_consensus_event(
                        db, job_id, camera_id, job.source_id, frame_buffer_index, closed_track,
                    )
                    events_found += 1

                job.processed_frames = processed
                job.events_found = events_found
                db.commit()
        finally:
            # Any vehicle still mid-frame when the loop ends (source
            # exhausted, stop requested, max_frames hit) would otherwise
            # never produce an event — flush() closes every open track
            # on whatever it's seen so far. This runs whether the loop
            # exited cleanly or via the outer except below, same as the
            # cap.release() it sits alongside.
            for closed_track in tracker.flush():
                _write_consensus_event(
                    db, job_id, camera_id, job.source_id, frame_buffer_index, closed_track,
                )
                events_found += 1
            job.processed_frames = processed
            job.events_found = events_found
            db.commit()
            if cap is not None:
                cap.release()

        job.status = JobStatusEnum.COMPLETED
        job.completed_at = datetime.now(timezone.utc)
        if stopped_by_request:
            # Reusing COMPLETED (not adding a new enum value/migration) —
            # this note is what actually distinguishes "you stopped this"
            # from "it reached max_frames or the source ended on its own".
            job.error_message = "Stopped by request."
        db.commit()

    except Exception as exc:  # noqa: BLE001 — surfacing any pipeline error onto the job row
        # Full traceback goes to the server console — str(exc) alone can be
        # empty for some exception types (bare AssertionError, some cv2
        # errors), which previously left error_message blank with no way
        # to tell what actually broke.
        logger.exception("ANPR job %s failed", job_id)
        db.rollback()
        job = db.query(AnprJob).filter(AnprJob.id == job_id).first()
        if job:
            job.status = JobStatusEnum.FAILED
            if str(exc):
                job.error_message = f"{type(exc).__name__}: {exc}"
            else:
                job.error_message = (
                    f"{type(exc).__name__} (no message — see server console for full traceback)"
                )
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        # Stop the relay drawing this job's last detections over a feed
        # that nothing is analysing any more. The TTL would expire them
        # within DETECTION_TTL_SECONDS anyway; this makes it immediate.
        if overlay_key is not None:
            latest_detections.clear(overlay_key)
        with _job_stop_events_lock:
            _job_stop_events.pop(str(job_id), None)
            # Only remove the source's active-job pointer if it's still
            # pointing at THIS job — a stale stop_event.pop is always
            # safe, but a fast toggle-off/on could have already
            # registered a new job for this source under the same key by
            # the time this thread's cleanup runs, and we must not evict
            # that newer job's pointer.
            if overlay_key is not None and _source_continuous_jobs.get(overlay_key) == job_id:
                _source_continuous_jobs.pop(overlay_key, None)
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
    # Vehicle recognition is NOT gated here the same way: it's an
    # enrichment on top of an already-accepted plate read (see
    # _write_consensus_event's try/except), so a missing torch/CLIP
    # install degrades to plate-only events instead of blocking ANPR
    # entirely. Check vehicle_pipeline.dependency_status() /
    # vahan.is_configured() via GET /health if vehicle fields are coming
    # back empty and that's unexpected.

    source = db.query(CameraSource).filter(CameraSource.id == payload.source_id).first()
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")

    scoped_department = department_scope(current_user)
    if scoped_department is not None:
        camera = db.query(CameraRef).filter(CameraRef.id == source.camera_id).first()
        if camera and camera.department != scoped_department:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to process this source.")

    source_key = str(source.id)

    if payload.continuous:
        # Toggle-ON should be idempotent: if this source already has a
        # live continuous job, hand back that same job instead of
        # spawning a second one — two jobs racing on one source would
        # double CPU cost and stomp on each other's overlay-cache writes.
        with _job_stop_events_lock:
            existing_job_id = _source_continuous_jobs.get(source_key)
        if existing_job_id is not None:
            existing = db.query(AnprJob).filter(AnprJob.id == existing_job_id).first()
            if existing and existing.status == JobStatusEnum.RUNNING:
                return existing
            # Row says RUNNING died without cleaning up its own entry
            # (process crash mid-job) — fall through and start a fresh one.
            with _job_stop_events_lock:
                _source_continuous_jobs.pop(source_key, None)

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

    stop_event: Optional[threading.Event] = None
    if payload.continuous:
        stop_event = threading.Event()
        with _job_stop_events_lock:
            _job_stop_events[str(job.id)] = stop_event
            _source_continuous_jobs[source_key] = job.id

    # Continuous jobs still carry the safety cap the person requested (or
    # the default), just not as the intended stop condition — stop_event
    # is what's meant to end the job. If someone genuinely wants
    # "unbounded", they can pass a large max_frames alongside continuous.
    thread = threading.Thread(
        target=_run_anpr_job,
        args=(job.id, source.id, source.source_url, source.camera_id, frame_skip, payload.max_frames, stop_event),
        daemon=True,
    )
    thread.start()

    return job


@router.post("/jobs/{job_id}/stop", response_model=AnprJobOut)
def stop_anpr_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Signals a running CONTINUOUS job to stop. Only continuous jobs are
    stoppable this way — a one-shot batch job has no registered stop
    event (it's not in _job_stop_events) and just runs to completion on
    its own, so this 404s for those rather than silently doing nothing."""
    job = db.query(AnprJob).filter(AnprJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")

    scoped_department = department_scope(current_user)
    if scoped_department is not None:
        camera = db.query(CameraRef).filter(CameraRef.id == job.camera_id).first()
        if camera and camera.department != scoped_department:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to stop this job.")

    with _job_stop_events_lock:
        stop_event = _job_stop_events.get(str(job_id))

    if stop_event is None:
        if job.status == JobStatusEnum.RUNNING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This job isn't stoppable — it's a one-shot batch job, not a continuous one.",
            )
        # Already finished one way or another — stopping it is a no-op,
        # not an error (toggling "off" twice shouldn't fail).
        return job

    stop_event.set()
    db.refresh(job)
    return job


@router.get("/sources/{source_id}/active-job", response_model=Optional[AnprJobOut])
def get_active_continuous_job(
    source_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lets the frontend recover 'is the live overlay currently on for
    this source' after a page refresh, without having to remember a
    job_id client-side — the toggle button's on/off state can just be
    derived from whether this returns a job or null."""
    with _job_stop_events_lock:
        job_id = _source_continuous_jobs.get(str(source_id))
    if job_id is None:
        return None
    job = db.query(AnprJob).filter(AnprJob.id == job_id).first()
    if job is None or job.status != JobStatusEnum.RUNNING:
        return None
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
    vehicle_type: Optional[str] = Query(None, description="e.g. car, motorcycle, truck, bus."),
    vehicle_colour: Optional[str] = Query(None),
    vehicle_make: Optional[str] = Query(None, description="Partial match, case-insensitive."),
    min_consensus_agreement: Optional[float] = Query(
        None, ge=0, le=1,
        description="Only events whose weakest per-character agreement is at least this — filters out doubtful reads.",
    ),
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
    if vehicle_type:
        query = query.filter(AnprEvent.vehicle_type == vehicle_type)
    if vehicle_colour:
        query = query.filter(AnprEvent.vehicle_colour == vehicle_colour)
    if vehicle_make:
        query = query.filter(AnprEvent.vehicle_make.ilike(f"%{vehicle_make}%"))
    if min_consensus_agreement is not None:
        query = query.filter(AnprEvent.consensus_agreement >= min_consensus_agreement)

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

    # Registry lookups are deliberately NOT run on every plate the
    # cameras see (see vahan.py's module docstring on cost and PII) —
    # flagging an event as "of interest" is the moment a human has
    # decided this specific vehicle is worth the call.
    if payload.is_flagged and vahan.should_look_up(is_flagged=True):
        _apply_registry_lookup(event)

    db.commit()
    db.refresh(event)
    return event


def _apply_registry_lookup(event: "AnprEvent", force: bool = False) -> bool:
    """Looks event.plate_text up against the registry and, if found,
    overwrites the vehicle_make/model/colour fields with it — a
    registry answer is ground truth, a vision answer is a guess, and
    vehicle_make_model_source records which one ended up on the row.
    Logs (does not block on) any disagreement between what vision saw
    and what's registered, since that mismatch is itself useful.
    Returns True if a registry record was found and applied."""
    try:
        record = vahan.lookup(event.plate_text, force=force)
    except Exception:  # noqa: BLE001 — the event must be saveable either way
        logger.exception("Registry lookup failed for plate %s", event.plate_text)
        return False
    if record is None:
        return False

    if event.vehicle_make and record.make and event.vehicle_make.lower() != record.make.lower():
        logger.warning(
            "Registry/vision mismatch for plate %s: vision saw %s %s, registry says %s %s",
            event.plate_text, event.vehicle_make, event.vehicle_model or "",
            record.make, record.model or "",
        )

    if record.make:
        event.vehicle_make = record.make
        event.vehicle_model = record.model or event.vehicle_model
        event.vehicle_make_model_confidence = 1.0
        event.vehicle_make_model_source = "registry"
    if record.colour and not event.vehicle_colour:
        event.vehicle_colour = record.colour
    return True


@router.post("/events/{event_id}/lookup-registry", response_model=AnprEventOut)
def lookup_registry_for_event(
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Explicit 'look this one up' action, independent of
    VAHAN_LOOKUP_POLICY — for a reviewer who wants the registry answer
    for a specific event regardless of the automatic flagged-only
    default. Still respects VAHAN_LOOKUP_POLICY='none' (registry
    disabled outright) since that's a deployment-level switch, not a
    per-event one."""
    event = db.query(AnprEvent).filter(AnprEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found.")

    scoped_department = department_scope(current_user)
    if scoped_department is not None:
        camera = db.query(CameraRef).filter(CameraRef.id == event.camera_id).first()
        if camera and camera.department != scoped_department:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to look up this event.")

    configured, reason = vahan.is_configured()
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Vehicle registry is not configured: {reason}",
        )

    found = _apply_registry_lookup(event, force=True)
    db.commit()
    db.refresh(event)
    if not found:
        # Not an error — the plate may genuinely not be registered, or
        # OCR's read may not be a real plate. 200 with unchanged vehicle
        # fields tells the reviewer "we checked and nothing came back",
        # distinct from a 404/503 which would mean we didn't check at all.
        logger.info("Registry lookup for %s returned no record", event.plate_text)
    return event
