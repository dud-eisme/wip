"""
overlay.py

Draws ANPR detection boxes onto frames. Two uses, both wired in by the
caller (see the two integration points documented below — neither is
done yet, since they touch camera_worker.py / routers/stream.py /
routers/anpr.py, which weren't available to write against directly):

  1. LIVE STREAM OVERLAY — routers/stream.py's MJPEG relay draws the
     most recent detections for that source on top of each frame it
     serves, using LatestDetectionsCache below so the (separate) ANPR
     job thread and the stream relay thread can share results safely.

  2. SNAPSHOT BURN-IN — routers/anpr.py's job loop (or storage.py's
     save step) calls draw_detections() on the full frame before
     saving it, instead of (or alongside) the existing cropped-plate-
     only snapshot.

Neither call site is wired up yet — see the bottom of this file for
exactly what each integration point needs.
"""
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

# How long a cached detection set stays "current" before the stream
# overlay stops drawing it (avoids showing stale boxes on a frozen/
# slow-moving feed once a job has finished or stalled).
DETECTION_TTL_SECONDS = 3.0

_BOX_COLOR = (0, 255, 0)  # BGR — green
_BOX_THICKNESS = 2
_LABEL_FONT = cv2.FONT_HERSHEY_SIMPLEX
_LABEL_SCALE = 0.5
_LABEL_THICKNESS = 1


@dataclass
class OverlayDetection:
    """Minimal shape draw_detections()/the cache need — build this from
    whatever anpr_pipeline.PlateDetection or your DB row actually is at
    the call site."""
    bbox: Tuple[int, int, int, int]  # (x1, y1, x2, y2)
    label: str = ""                   # e.g. plate_text, or "" to omit
    confidence: Optional[float] = None


def draw_detections(frame: "np.ndarray", detections: List[OverlayDetection]) -> "np.ndarray":
    """Returns a COPY of frame with boxes + labels drawn — never mutates
    the input, since the input may be a shared buffer (the "latest
    frame only" buffer camera_worker.py keeps, per the README) that
    other readers (or the next detection pass) also touch."""
    out = frame.copy()
    for det in detections:
        x1, y1, x2, y2 = det.bbox
        cv2.rectangle(out, (x1, y1), (x2, y2), _BOX_COLOR, _BOX_THICKNESS)

        label = det.label
        if det.confidence is not None:
            label = f"{label} {det.confidence:.2f}" if label else f"{det.confidence:.2f}"
        if label:
            (tw, th), _ = cv2.getTextSize(label, _LABEL_FONT, _LABEL_SCALE, _LABEL_THICKNESS)
            label_y = max(y1 - 6, th + 4)
            cv2.rectangle(out, (x1, label_y - th - 4), (x1 + tw + 4, label_y + 2), _BOX_COLOR, -1)
            cv2.putText(
                out, label, (x1 + 2, label_y - 2),
                _LABEL_FONT, _LABEL_SCALE, (0, 0, 0), _LABEL_THICKNESS, cv2.LINE_AA,
            )
    return out


def scale_detections(
    detections: List[OverlayDetection],
    from_size: Tuple[int, int],
    to_size: Tuple[int, int],
) -> List[OverlayDetection]:
    """Rescales bboxes from one frame size to another, both (width, height).

    Needed because the ANPR job and the MJPEG relay decode the source
    independently — the job opens its own cv2.VideoCapture, while the
    relay serves whatever the CameraWorker buffered. If those two ever
    differ in resolution, boxes drawn at the detector's coordinates land
    in the wrong place on the streamed frame.
    """
    fw, fh = from_size
    tw, th = to_size
    if fw <= 0 or fh <= 0 or (fw == tw and fh == th):
        return detections

    sx, sy = tw / fw, th / fh
    scaled = []
    for det in detections:
        x1, y1, x2, y2 = det.bbox
        scaled.append(
            OverlayDetection(
                bbox=(int(x1 * sx), int(y1 * sy), int(x2 * sx), int(y2 * sy)),
                label=det.label,
                confidence=det.confidence,
            )
        )
    return scaled


class LatestDetectionsCache:
    """Thread-safe: the ANPR job thread (running detect_plates_in_frame
    every ANPR_FRAME_SKIP-th frame) writes here; the MJPEG stream relay
    thread reads here on every frame it serves. Keyed by source_id so
    multiple concurrent sources (up to MAX_CONCURRENT_SOURCES) don't
    clobber each other. One RLock guards the whole dict — detection
    writes are infrequent (every Nth frame, one source at a time) so
    contention is a non-issue; don't reach for per-source locks here."""

    def __init__(self):
        self._lock = threading.RLock()
        self._by_source: Dict[
            str, Tuple[float, List[OverlayDetection], Optional[Tuple[int, int]]]
        ] = {}

    def set(
        self,
        source_id: str,
        detections: List[OverlayDetection],
        frame_size: Optional[Tuple[int, int]] = None,
    ) -> None:
        """frame_size is (width, height) of the frame these bboxes were
        computed against. Pass it whenever the writer and the reader may
        be decoding the source at different resolutions — the reader can
        then rescale via scale_detections(). Optional so an existing
        two-arg call site keeps working."""
        with self._lock:
            self._by_source[source_id] = (time.time(), detections, frame_size)

    def get(self, source_id: str) -> List[OverlayDetection]:
        """Returns [] if there's nothing cached yet, or if the cached
        entry is older than DETECTION_TTL_SECONDS (stream keeps
        flowing with no boxes rather than showing stale ones)."""
        detections, _ = self.get_entry(source_id)
        return detections

    def get_entry(
        self, source_id: str
    ) -> Tuple[List[OverlayDetection], Optional[Tuple[int, int]]]:
        """Like get(), but also returns the (width, height) the bboxes
        were computed in, or None if the writer didn't record one."""
        with self._lock:
            entry = self._by_source.get(source_id)
        if entry is None:
            return [], None
        ts, detections, frame_size = entry
        if time.time() - ts > DETECTION_TTL_SECONDS:
            return [], None
        return detections, frame_size

    def clear(self, source_id: str) -> None:
        with self._lock:
            self._by_source.pop(source_id, None)


# Process-wide singleton — import this same instance from both the job
# loop and the stream relay, the same way SourceManager is described as
# a process-wide registry in the README.
latest_detections = LatestDetectionsCache()
