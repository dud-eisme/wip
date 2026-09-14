"""
camera_worker.py
Background threads that keep RTSP/HTTP/file video sources open and decoded,
so the feed-relay endpoint always has a recent frame ready to serve instead
of opening a fresh connection per HTTP request (which would be far too slow
and would multiply load on the camera/NVR for every viewer).

Design for "can this handle 50 cameras":
- One lightweight thread per active source. OpenCV's VideoCapture.read()
  is a blocking call, so each source needs its own thread regardless.
- Each thread keeps only the LATEST decoded frame in memory (as JPEG bytes)
  behind a lock — older frames are simply overwritten, never queued. This
  bounds memory use regardless of how slow a consumer is, and avoids
  unbounded backlogs if a viewer's connection is slow.
- Frame rate is throttled (STREAM_TARGET_FPS) — we deliberately do NOT try
  to decode/serve at the source's native FPS (often 15-30fps) if nobody
  needs that; 5-10fps is plenty for a human-viewed relay and cuts CPU cost
  roughly in half to a third versus native rate.
- MAX_CONCURRENT_SOURCES caps how many worker threads can run at once, so
  a misconfigured deployment can't silently spawn hundreds of decoders and
  starve the box. 50 is the configured default matching the requirement.

Honest limitation: CPU and network bandwidth for 50 *simultaneous* live
decodes depends entirely on source resolution/codec and the host machine.
This code is written so it CAN run 50 workers without crashing or leaking
memory, but "smoothly" is a hardware/tuning question, not a code one — see
README section "Can it really handle 50 cameras?".
"""
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

import cv2

STREAM_TARGET_FPS = float(os.getenv("STREAM_TARGET_FPS", "8"))
MAX_CONCURRENT_SOURCES = int(os.getenv("MAX_CONCURRENT_SOURCES", "50"))

RECONNECT_DELAY_SECONDS = 5.0
JPEG_QUALITY = 80


@dataclass
class _FrameBuffer:
    lock: threading.Lock = field(default_factory=threading.Lock)
    jpeg_bytes: Optional[bytes] = None
    frames_captured: int = 0
    last_frame_at: Optional[datetime] = None
    last_error: Optional[str] = None

    def set_frame(self, jpeg_bytes: bytes) -> None:
        with self.lock:
            self.jpeg_bytes = jpeg_bytes
            self.frames_captured += 1
            self.last_frame_at = datetime.now(timezone.utc)
            self.last_error = None

    def set_error(self, message: Optional[str]) -> None:
        with self.lock:
            self.last_error = message

    def get_frame(self) -> Optional[bytes]:
        with self.lock:
            return self.jpeg_bytes

    def snapshot_status(self) -> dict:
        with self.lock:
            return {
                "frames_captured": self.frames_captured,
                "last_frame_at": self.last_frame_at,
                "last_error": self.last_error,
            }


class CameraWorker(threading.Thread):
    """One background thread per active video source."""

    def __init__(self, source_id: uuid.UUID, source_url: str, target_fps: float = STREAM_TARGET_FPS):
        super().__init__(daemon=True)
        self.source_id = source_id
        self.source_url = source_url
        self.target_fps = max(target_fps, 0.1)
        self.buffer = _FrameBuffer()
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    @property
    def is_running(self) -> bool:
        return self.is_alive() and not self._stop_event.is_set()

    def run(self) -> None:
        frame_interval = 1.0 / self.target_fps
        while not self._stop_event.is_set():
            cap = cv2.VideoCapture(self.source_url)
            if not cap.isOpened():
                self.buffer.set_error(f"Could not open source: {self.source_url}")
                if self._stop_event.wait(RECONNECT_DELAY_SECONDS):
                    break
                continue

            self.buffer.set_error(None)
            try:
                while not self._stop_event.is_set():
                    loop_start = time.monotonic()
                    ok, frame = cap.read()
                    if not ok:
                        self.buffer.set_error("Source dropped/ended — attempting reconnect.")
                        break

                    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
                    if ok:
                        self.buffer.set_frame(encoded.tobytes())

                    elapsed = time.monotonic() - loop_start
                    sleep_for = frame_interval - elapsed
                    if sleep_for > 0:
                        self._stop_event.wait(sleep_for)
            finally:
                cap.release()

            if not self._stop_event.is_set():
                # Loop back around and retry the connection after a short delay.
                self._stop_event.wait(RECONNECT_DELAY_SECONDS)

    def status(self) -> dict:
        return {
            "source_id": self.source_id,
            "is_running": self.is_running,
            **self.buffer.snapshot_status(),
        }


class SourceManager:
    """Process-wide registry of active CameraWorker threads."""

    def __init__(self, max_concurrent_sources: int = MAX_CONCURRENT_SOURCES):
        self._workers: Dict[str, CameraWorker] = {}
        self._lock = threading.Lock()
        self.max_concurrent_sources = max_concurrent_sources

    def start(self, source_id: uuid.UUID, source_url: str) -> CameraWorker:
        key = str(source_id)
        with self._lock:
            existing = self._workers.get(key)
            if existing and existing.is_running:
                return existing
            if len(self._workers) >= self.max_concurrent_sources and key not in self._workers:
                raise RuntimeError(
                    f"MAX_CONCURRENT_SOURCES ({self.max_concurrent_sources}) reached. "
                    f"Stop another source before starting a new one."
                )
            worker = CameraWorker(source_id=source_id, source_url=source_url)
            worker.start()
            self._workers[key] = worker
            return worker

    def stop(self, source_id: uuid.UUID) -> bool:
        key = str(source_id)
        with self._lock:
            worker = self._workers.pop(key, None)
        if worker:
            worker.stop()
            return True
        return False

    def get(self, source_id: uuid.UUID) -> Optional[CameraWorker]:
        with self._lock:
            return self._workers.get(str(source_id))

    def list_status(self) -> list:
        with self._lock:
            workers = list(self._workers.values())
        return [w.status() for w in workers]

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for w in self._workers.values() if w.is_running)

    def shutdown_all(self, join_timeout: float = 3.0) -> None:
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for w in workers:
            w.stop()
        for w in workers:
            w.join(timeout=join_timeout)


# Single process-wide instance, imported by routers.
source_manager = SourceManager()
