"""
camera_worker.py
Background threads that keep RTSP/HLS/HTTP/file video sources open and
decoded, so the feed-relay endpoint always has a recent frame ready to
serve instead of opening a fresh connection per HTTP request (which
would be far too slow and would multiply load on the camera/NVR for
every viewer).

RTSP vs HLS: RTSP is a live PUSH source (UDP or TCP) with no application-
level retransmission for the media itself — a late/dropped packet under
network jitter corrupts every macroblock depending on it until the next
keyframe (see frame_quality.py for how that's detected/rejected). HLS
delivers the exact same H.264 content as segments over plain HTTP/TCP:
a slow/late segment just makes the GET take longer, it can never hand
the decoder a half-corrupted bitstream, so HLS sidesteps this failure
mode at the transport layer instead of needing to be detected after the
fact. The tradeoff is latency — typically several seconds of segment
buffering vs. RTSP's near-real-time delivery — which is a non-issue for
a human-viewed relay or for ANPR (neither needs sub-second latency).
Prefer HLS over RTSP for any source on a network link that isn't rock
solid; keep RTSP for sources where the extra latency genuinely matters.
Both are handled by the same cv2.VideoCapture(source_url) call below —
ffmpeg auto-detects the protocol from the URL scheme, so no source-type-
specific branching is needed to open either kind of stream. The one
place they DO diverge is the read loop's throttling strategy — see the
is_rtsp branch in CameraWorker.run() for why RTSP needs a tight,
unthrottled drain loop while HLS (like a local file) is safe to
throttle directly.

Design for "can this handle 50 cameras":
- One lightweight thread per active source. OpenCV's VideoCapture.read()
  is a blocking call, so each source needs its own thread regardless.
- Each thread keeps only the LATEST decoded frame in memory (as JPEG bytes)
  behind a lock — older frames are simply overwritten, never queued. This
  bounds memory use regardless of how slow a consumer is, and avoids
  unbounded backlogs if a viewer's connection is slow.
- Frame rate is throttled (STREAM_TARGET_FPS) for what gets PUBLISHED to
  viewers — we deliberately do NOT serve at the source's native FPS
  (often 15-30fps) if nobody needs that; 5-10fps is plenty for a
  human-viewed relay. For a local FILE source this also throttles how
  often we decode, roughly halving to a third CPU cost vs native rate.
  For a live RTSP source it does NOT reduce decode cost — cv2.VideoCapture
  decodes every frame it reads regardless, and reads happen at the
  source's native rate to keep the network buffer drained (throttling the
  read itself for a push source causes dropped/corrupted frames — see the
  comment in CameraWorker.run()). RTSP decode cost therefore scales with
  the source's own frame rate, not STREAM_TARGET_FPS.
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

import frame_quality

# OpenCV's ffmpeg backend has no per-call API for RTSP transport — it only
# reads this from the process environment, at the moment a VideoCapture is
# opened, so it has to be set once here before any capture is created.
# Without it, OpenCV defaults to UDP, which drops/corrupts packets under
# ordinary load (see the read-loop comment below for how that cascades).
# TCP trades a little latency for reliable delivery — worth it here.
#
# TCP alone only guarantees packets arrive intact, not that they arrive in
# time — during a brief network hiccup or a bitrate burst (a
# heavily-lit/detailed part of frame costs far more bits than a plain
# road), ffmpeg can still give up on a late packet rather than stall the
# whole read, and H.264 has no error concealment: that one skipped packet
# corrupts every macroblock depending on it until the next full keyframe.
# stimeout/max_delay/buffer_size give ffmpeg more room to absorb that kind
# of jitter before it resorts to dropping anything:
#   stimeout      - microseconds to wait on the socket before giving up
#                    (default is too eager for a flaky link)
#   max_delay     - microseconds of reordering/jitter buffer allowed
#                    before ffmpeg gives up on a late packet
#   buffer_size   - socket receive buffer, in bytes
# These are tuning knobs, not a fix for a link that's genuinely too slow
# for the camera's bitrate — see the README section on that. If glitches
# persist after this, the next lever is on the CAMERA/NVR side: a shorter
# GOP (I-frame) interval means any corruption self-heals within a fraction
# of a second instead of smearing until the next keyframe; a lower-bitrate
# substream reduces how much data any single burst needs to deliver on
# time in the first place.
# setdefault so an operator's own env setting isn't silently overridden.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|stimeout;20000000|max_delay;500000|buffer_size;1048576",
)

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
    # Frame-quality tracking (see frame_quality.py) — a candidate frame
    # that scores as corrupted is counted here instead of published, so
    # viewers/ANPR keep seeing the last GOOD frame rather than a smear.
    # last_blockiness_score is updated on every checked frame (corrupted
    # or not) specifically so it's visible for threshold tuning via the
    # status endpoint, not just when something gets rejected.
    corrupted_frames_skipped: int = 0
    last_blockiness_score: Optional[float] = None

    def set_frame(self, jpeg_bytes: bytes) -> None:
        with self.lock:
            self.jpeg_bytes = jpeg_bytes
            self.frames_captured += 1
            self.last_frame_at = datetime.now(timezone.utc)
            self.last_error = None

    def set_error(self, message: Optional[str]) -> None:
        with self.lock:
            self.last_error = message

    def record_frame_quality(self, score: float, corrupted: bool) -> None:
        with self.lock:
            self.last_blockiness_score = score
            if corrupted:
                self.corrupted_frames_skipped += 1

    def get_frame(self) -> Optional[bytes]:
        with self.lock:
            return self.jpeg_bytes

    def snapshot_status(self) -> dict:
        with self.lock:
            return {
                "frames_captured": self.frames_captured,
                "last_frame_at": self.last_frame_at,
                "last_error": self.last_error,
                "corrupted_frames_skipped": self.corrupted_frames_skipped,
                "last_blockiness_score": self.last_blockiness_score,
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
        # RTSP is a PUSH source: the server sends packets continuously
        # whether or not we call cap.read(). Throttling cap.read() itself
        # to STREAM_TARGET_FPS — sleeping between reads — starves how fast
        # we drain that stream, so packets queue up and the server starts
        # discarding them ("reader is too slow" in mediamtx's log), which
        # then corrupts subsequent decodes (missing reference pictures,
        # mmco failures) once frames actually go missing mid-GOP. So for
        # RTSP we call cap.read() in a tight loop with no sleep — always
        # draining at the source's native rate — and only gate how often
        # we JPEG-encode and publish to the viewer buffer at target_fps.
        # That decouples "keep the network buffer empty" from "how fast do
        # viewers need frames", which is what actually needs throttling.
        #
        # A local FILE source has no such risk (nothing is being pushed or
        # lost if we don't read promptly), so file sources keep throttling
        # the read itself — otherwise we'd decode the whole clip in a
        # fraction of a second instead of playing back at a realistic pace.
        # An HLS source (source_type='hls', a .m3u8 URL) falls through to
        # this same throttled branch, not the RTSP one below — it's PULLED
        # over HTTP, not pushed, so nothing overflows/drops on the server
        # side if we don't read at native rate. That's also exactly why HLS
        # doesn't get the frame_quality corruption check applied to it
        # below: that check exists specifically for RTSP's push-corruption
        # failure mode, which HLS's TCP segment delivery already precludes.
        is_rtsp = self.source_url.lower().startswith("rtsp://")
        frame_interval = 1.0 / self.target_fps

        while not self._stop_event.is_set():
            cap = cv2.VideoCapture(self.source_url)
            if not cap.isOpened():
                self.buffer.set_error(f"Could not open source: {self.source_url}")
                if self._stop_event.wait(RECONNECT_DELAY_SECONDS):
                    break
                continue

            self.buffer.set_error(None)
            last_publish = 0.0
            try:
                while not self._stop_event.is_set():
                    loop_start = time.monotonic()
                    ok, frame = cap.read()
                    if not ok:
                        self.buffer.set_error("Source dropped/ended — attempting reconnect.")
                        break

                    if is_rtsp:
                        if loop_start - last_publish >= frame_interval:
                            # Reject a garbled candidate frame instead of
                            # publishing it — the RTSP source itself can
                            # corrupt frames under packet loss/jitter that
                            # no amount of client-side connection tuning
                            # eliminates (see frame_quality.py). Rejecting
                            # here means viewers/ANPR keep the last GOOD
                            # frame instead of a smear; last_publish still
                            # advances either way so a sustained bad
                            # stretch costs a bounded, predictable amount
                            # of CPU rather than checking every frame at
                            # native rate.
                            corrupted = False
                            if frame_quality.FRAME_QUALITY_CHECK_ENABLED:
                                corrupted, score = frame_quality.is_frame_corrupted(frame)
                                self.buffer.record_frame_quality(score, corrupted)
                            if not corrupted:
                                ok, encoded = cv2.imencode(
                                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
                                )
                                if ok:
                                    self.buffer.set_frame(encoded.tobytes())
                            last_publish = loop_start
                        # Deliberately no sleep — loop straight back to
                        # cap.read() to keep draining the network buffer.
                        continue

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
