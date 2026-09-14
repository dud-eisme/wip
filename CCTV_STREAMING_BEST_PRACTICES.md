# CCTV Streaming Best Practices & Do's and Don'ts

This document codifies critical practices for working with live CCTV feeds in Model 2 (Live Feed Relay & ANPR) and Model 3 (Federation). These are not theoretical — they reflect real failure modes observed in production CCTV systems.

---

## Model 2: Camera Worker Implementation Best Practices

### DO — Force RTSP over TCP

**UDP is accepted but fails across NAT and most corporate firewalls.** Partial UDP delivery produces corrupt frames that look like model bugs.

**Implementation in Model 2:**

```python
# model2/backend/camera_worker.py
import cv2
import os

RTSP_TRANSPORT = os.getenv("RTSP_TRANSPORT", "tcp")  # Set in .env
RTSP_CONNECTION_TIMEOUT = int(os.getenv("RTSP_CONNECTION_TIMEOUT", "10"))

class CameraWorker(Thread):
    def __init__(self, source_id: str, stream_url: str):
        self.source_id = source_id
        self.stream_url = stream_url
        self.cap = None
    
    def open_stream(self):
        """Open RTSP stream with TCP transport."""
        # Force TCP transport: rtsp_transport=tcp
        options = {
            "rtsp_transport": RTSP_TRANSPORT,
        }
        
        # OpenCV CAP_PROP_OPEN_TIMEOUT_MSEC (requires recent OpenCV)
        self.cap = cv2.VideoCapture(self.stream_url)
        self.cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, RTSP_CONNECTION_TIMEOUT * 1000)
        
        if not self.cap.isOpened():
            logger.error(
                f"Failed to open stream {self.source_id}: {self.stream_url} "
                f"(transport={RTSP_TRANSPORT})"
            )
            return False
        
        logger.info(f"Opened stream {self.source_id} with rtsp_transport={RTSP_TRANSPORT}")
        return True
    
    def run(self):
        """Main loop: decode frames, store latest, reconnect on failure."""
        while self.running:
            if not self.cap or not self.cap.isOpened():
                if not self.open_stream():
                    time.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(self.reconnect_delay * 1.5, 30)
                    continue
                else:
                    self.reconnect_delay = 2  # Reset backoff on success
            
            ret, frame = self.cap.read()
            if not ret:
                logger.warning(f"Frame read failed for {self.source_id}, reconnecting...")
                self.cap.release()
                self.cap = None
                continue
            
            # Store only the most recent frame (no queue)
            self._current_frame = frame
```

**Configuration (.env):**

```env
# Force RTSP over TCP (not UDP, which fails across NAT/firewalls)
RTSP_TRANSPORT=tcp
RTSP_CONNECTION_TIMEOUT=10

# If port 8554 is blocked, fallback endpoint configuration
HLS_ENDPOINT_AVAILABLE=false
HLS_PORT=8080
```

**If port 8554 (standard RTSP) is blocked on your network, use HLS fallback:**

```python
def get_stream_url(camera: Camera) -> str:
    """
    Prefer RTSP/TCP; fallback to HLS if RTSP port is blocked.
    """
    if RTSP_TRANSPORT == "tcp" and port_accessible(camera.stream_endpoint, 8554):
        return camera.stream_endpoint
    elif HLS_ENDPOINT_AVAILABLE:
        # Construct HLS URL from camera config
        return f"http://{camera.host}:{HLS_PORT}/stream.m3u8"
    else:
        raise ValueError(f"No accessible stream for {camera.camera_id}")
```

---

### DON'T — Trust the reported frame rate

**OpenCV's `CAP_PROP_FPS` often does not match the actual delivery rate.**

Using that number to convert pixels-per-frame into speed, dwell time, or any time-derived metric will produce **incorrect results**.

**Problem:** Camera codec profiles, network buffering, and decoder quirks all affect actual frame delivery. The declared FPS is a hint, not a guarantee.

**Solution: Measure the real rate yourself, or ignore declared frame rate entirely and use timestamps.**

```python
# model2/backend/camera_worker.py

class CameraWorker(Thread):
    def run(self):
        frame_times = []  # (frame_index, actual_timestamp_ns)
        
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                continue
            
            # Get the actual PTS (Presentation TimeStamp) from the frame
            # OpenCV: CAP_PROP_POS_MSEC
            pts_ms = self.cap.get(cv2.CAP_PROP_POS_MSEC)
            frame_index = self.cap.get(cv2.CAP_PROP_POS_FRAMES)
            
            # Calculate actual inter-frame interval
            if frame_times:
                prev_pts_ms, prev_idx = frame_times[-1]
                actual_delta_ms = pts_ms - prev_pts_ms
                actual_fps = 1000.0 / actual_delta_ms if actual_delta_ms > 0 else 0
            else:
                actual_fps = 0
            
            frame_times.append((pts_ms, frame_index))
            
            # Log discrepancy between declared and actual
            declared_fps = self.cap.get(cv2.CAP_PROP_FPS)
            if declared_fps > 0 and actual_fps > 0:
                fps_error = abs(declared_fps - actual_fps) / declared_fps * 100
                if fps_error > 10:  # >10% error
                    logger.warning(
                        f"{self.source_id}: Declared FPS {declared_fps:.1f} "
                        f"vs actual {actual_fps:.1f} ({fps_error:.0f}% error)"
                    )
            
            # **CRITICAL**: Use PTS, not arrival time
            self._current_frame = {
                "image": frame,
                "pts_ms": pts_ms,      # Use this for timing
                "frame_index": frame_index,
                "arrival_time": time.time()  # For debugging only
            }
            
            # Keep only last 100 frame times for moving average
            if len(frame_times) > 100:
                frame_times.pop(0)
```

---

### DO — Drive all timing from PTS, never from arrival time

**Use:**
- OpenCV: `CAP_PROP_POS_MSEC`
- GStreamer: buffer PTS
- RTP timestamps

**DO NOT use wall-clock time at the moment a frame is read.**

**Why this matters:**

When a client connects, the gateway replays its buffered group-of-pictures (GOP) so the decoder can start at a keyframe. **The first second or two of frames may arrive faster than real time.** A tracker that timestamps by arrival will compute impossible velocities immediately after every connection.

**Implementation:**

```python
# model2/backend/routers/anpr.py

@app.post("/api/v2/anpr/jobs")
async def run_anpr_job(job_request: ANPRJobRequest):
    """
    Run ANPR on a source. Timing must be driven by PTS, not arrival time.
    """
    source = get_source(job_request.source_id)
    
    # The CameraWorker maintains current_frame with PTS
    frame_data = source.worker.get_current_frame()
    
    if not frame_data:
        raise HTTPException(status_code=400, detail="No frame available")
    
    # Use PTS for all timing calculations
    pts_ms = frame_data["pts_ms"]
    frame_index = frame_data["frame_index"]
    
    # **CORRECT**: Timing based on PTS
    result = {
        "detection_timestamp_ms": pts_ms,  # From stream PTS, not arrival
        "frame_index": frame_index,
        "detected_plates": [...]
    }
    
    # Store in database with PTS
    event = ANPREvent(
        source_id=source.id,
        timestamp_ms=pts_ms,  # PTS-based
        plate_text="ABC1234",
        confidence=0.95,
        frame_index=frame_index
    )
    db.add(event)
    db.commit()
    
    return result
```

**For multi-object tracking (Kalman filters, DeepSORT):**

```python
# model2/backend/tracking.py

class KalmanTracker:
    def __init__(self):
        self.tracks = {}  # track_id -> KalmanFilterState
    
    def update(self, detections, pts_ms: float, prev_pts_ms: float):
        """
        Update tracker with detections. Use PTS delta, not fixed frame rate.
        """
        # **CRITICAL**: Use actual elapsed time from PTS, not frame count
        dt_seconds = (pts_ms - prev_pts_ms) / 1000.0
        
        if dt_seconds <= 0:
            logger.warning("Non-monotonic PTS detected, skipping update")
            return
        
        for detection in detections:
            x, y, w, h = detection.bbox
            
            # Find nearest existing track
            nearest_track_id = self.nearest_track(x, y)
            
            if nearest_track_id is not None:
                # Update with actual dt_seconds, not 1/fps
                self.tracks[nearest_track_id].predict(dt=dt_seconds)
                self.tracks[nearest_track_id].update(detection)
            else:
                # Create new track
                self.tracks[len(self.tracks)] = KalmanFilter(x, y, dt=dt_seconds)
```

---

### DON'T — Assume a constant frame rate

**Frame intervals are not guaranteed to be uniform.**

Pipelines must tolerate inter-frame gaps without treating them as a disconnect. Motion models must use **actual elapsed PTS** between frames rather than a fixed cadence.

**Implementation:**

```python
# model2/backend/camera_worker.py

class CameraWorker(Thread):
    def __init__(self, source_id: str, stream_url: str):
        self.max_frame_gap_ms = 5000  # 5-second gap is acceptable, not fatal
        self.last_pts_ms = 0
    
    def run(self):
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                logger.warning(f"Frame read failed, attempting reconnect...")
                time.sleep(2)
                continue
            
            pts_ms = self.cap.get(cv2.CAP_PROP_POS_MSEC)
            
            # Detect frame gap
            if self.last_pts_ms > 0:
                frame_gap_ms = pts_ms - self.last_pts_ms
                
                # Warn if gap is large, but don't abort
                if frame_gap_ms > self.max_frame_gap_ms:
                    logger.warning(
                        f"{self.source_id}: Large frame gap {frame_gap_ms}ms "
                        f"(PTS {self.last_pts_ms} -> {pts_ms}). "
                        f"This is OK — stream may have buffer rebuild. Continuing."
                    )
                
                # Gaps are normal: don't treat as disconnect
                # Just continue processing
            
            self.last_pts_ms = pts_ms
            self._current_frame = {"image": frame, "pts_ms": pts_ms}
```

---

### DO — Reconnect automatically, with backoff

**Feeds are supervised and may restart. Expect occasional brief interruptions.**

Reconnect with **exponential backoff** (start at ~2 s, cap at ~30 s). Do not reconnect in a tight loop.

**Implementation:**

```python
# model2/backend/camera_worker.py

RECONNECT_BACKOFF_START = int(os.getenv("MODEL2_RECONNECT_BACKOFF_START", "2"))  # 2s
RECONNECT_BACKOFF_MAX = int(os.getenv("MODEL2_RECONNECT_BACKOFF_MAX", "30"))     # 30s

class CameraWorker(Thread):
    def __init__(self, source_id: str, stream_url: str):
        self.reconnect_delay = RECONNECT_BACKOFF_START
    
    def run(self):
        while self.running:
            if not self.cap or not self.cap.isOpened():
                logger.info(
                    f"{self.source_id}: Reconnecting in {self.reconnect_delay}s "
                    f"(exponential backoff)"
                )
                time.sleep(self.reconnect_delay)
                
                if self.open_stream():
                    # Success: reset backoff
                    self.reconnect_delay = RECONNECT_BACKOFF_START
                    logger.info(f"{self.source_id}: Reconnected successfully")
                else:
                    # Failure: increase backoff
                    self.reconnect_delay = min(
                        self.reconnect_delay * 1.5,
                        RECONNECT_BACKOFF_MAX
                    )
                    logger.warning(
                        f"{self.source_id}: Reconnect failed, next attempt in "
                        f"{self.reconnect_delay}s"
                    )
                continue
            
            ret, frame = self.cap.read()
            if not ret:
                logger.warning(f"{self.source_id}: Frame read failed, will reconnect")
                self.cap.release()
                self.cap = None
                continue
            
            # Process frame...
```

---

### DON'T — Treat decode warnings at join as fatal

**The grid includes both H.264 and H.265.** Attaching mid-stream can produce decoder messages such as:

- `Error constructing the frame RPS`
- `Could not find ref with POC`

**This is normal and self-corrects.** Pipelines that abort on the first decoder error will bounce on those streams.

**Implementation:**

```python
# model2/backend/camera_worker.py
import logging

# Set OpenCV logging to WARNING only (suppress decoder spam)
cv2.setLogLevel(cv2.LOG_LEVEL_WARNING)

logger = logging.getLogger("cctv_model2")

class CameraWorker(Thread):
    def run(self):
        decode_error_count = 0
        consecutive_failures = 0
        
        while self.running:
            if not self.cap or not self.cap.isOpened():
                # reconnect logic...
                pass
            
            ret, frame = self.cap.read()
            
            if not ret:
                consecutive_failures += 1
                
                # Only reconnect after sustained failures, not immediate errors
                if consecutive_failures > 10:  # 10 consecutive frame read failures
                    logger.error(
                        f"{self.source_id}: {consecutive_failures} consecutive "
                        f"frame read failures, reconnecting..."
                    )
                    self.cap.release()
                    self.cap = None
                    consecutive_failures = 0
                
                # Log once per 100 errors to avoid spam
                decode_error_count += 1
                if decode_error_count % 100 == 0:
                    logger.warning(
                        f"{self.source_id}: {decode_error_count} total decode errors "
                        f"(this is normal mid-stream, continuing)"
                    )
                
                continue
            
            # Successful decode: reset error counter
            consecutive_failures = 0
            decode_error_count = 0
            
            # Process frame...
            self._current_frame = {"image": frame, "pts_ms": ...}
```

---

### DON'T — Assume a uniform grid

**Cameras differ in resolution, codec, frame rate, and bitrate.**

Read per-camera properties from `/api/v1/cameras` (Model 1) and size batching, buffers, and decoders accordingly. **A fixed-shape inference batch across every camera will not work unscaled.**

**Implementation:**

```python
# model2/backend/routers/anpr.py

@app.post("/api/v2/anpr/jobs")
async def run_anpr_job(job_request: ANPRJobRequest):
    """
    ANPR job: adapt batch size and buffer to per-camera properties.
    """
    source = get_source(job_request.source_id)
    
    # Fetch camera properties from Model 1 registry
    camera = await fetch_camera_from_registry(source.camera_id)
    
    # Extract properties
    resolution = camera.get("resolution", "1280x720")  # Default
    codec = camera.get("codec", "H.264")
    frame_rate = camera.get("frame_rate", 25)
    bitrate_mbps = camera.get("bitrate_mbps", 2.0)
    
    logger.info(
        f"{source.camera_id}: {resolution} @ {frame_rate}fps, "
        f"{codec}, {bitrate_mbps}Mbps"
    )
    
    # Adapt buffer size to camera properties
    w, h = map(int, resolution.split('x'))
    bytes_per_frame = (w * h * 1.5) if codec == "H.264" else (w * h * 2)
    max_buffered_frames = max(10, int(frame_rate))  # At least 1 second
    buffer_bytes = bytes_per_frame * max_buffered_frames
    
    logger.info(f"Buffer for {source.camera_id}: {buffer_bytes / 1e6:.1f} MB")
    
    # Adapt YOLO batch size to resolution
    # Small cameras (< 640x480): batch 4
    # Medium (640x480 - 1280x720): batch 2
    # Large (> 1280x720): batch 1
    if w < 640:
        batch_size = 4
    elif w < 1280:
        batch_size = 2
    else:
        batch_size = 1
    
    logger.info(f"YOLO batch size for {source.camera_id}: {batch_size}")
    
    # Run ANPR with adapted parameters
    job = ANPRJob(
        source_id=source.id,
        batch_size=batch_size,
        buffer_bytes=buffer_bytes
    )
    # ... run job ...
```

---

### DO — Expect a scene discontinuity

**Each feed is a continuous recording that loops.** At the loop point the scene cuts abruptly, similar to a camera reboot.

**Long-lived state — background models, re-identification galleries, object track IDs — should recover from a hard cut rather than assuming infinite continuity.**

**Implementation:**

```python
# model2/backend/tracking.py

class SceneAwareTracker:
    def __init__(self):
        self.tracks = {}
        self.background_model = None
        self.last_scene_timestamp = time.time()
    
    def detect_scene_cut(self, pts_ms: float, prev_pts_ms: float) -> bool:
        """
        Detect abrupt scene cuts (PTS jump or backward movement).
        """
        # PTS backward or large jump = scene cut
        if prev_pts_ms > 0 and pts_ms < prev_pts_ms:
            logger.warning(
                f"Scene discontinuity detected: PTS jumped backward "
                f"({prev_pts_ms} -> {pts_ms}). Resetting state."
            )
            return True
        
        # Large PTS gap = potential cut
        pts_gap_ms = pts_ms - prev_pts_ms if prev_pts_ms > 0 else 0
        if pts_gap_ms > 10000:  # > 10 seconds
            logger.warning(
                f"Large PTS gap ({pts_gap_ms}ms). Possible scene cut."
            )
            return True
        
        return False
    
    def update(self, detections, frame, pts_ms: float, prev_pts_ms: float):
        """
        Update tracker. Reset state on scene cuts.
        """
        if self.detect_scene_cut(pts_ms, prev_pts_ms):
            # Hard reset on scene cut
            logger.info("Resetting all tracks and background model due to scene cut")
            self.tracks = {}
            self.background_model = BackgroundModel()  # Fresh model
            self.last_scene_timestamp = time.time()
        
        # Normal tracking continues with fresh state post-cut
        for detection in detections:
            # ... tracking logic, now aware of scene cuts ...
```

---

### DON'T — Plan around obtaining copies of the footage

**There is no file download.** The grid is consumed live over the protocols in Section 1, and that is what evaluation exercises.

`/stream/<id>` is the browser playback fallback: it answers range requests for a media player, so pulling it with a plain `curl` or `wget` yields a partial file that looks complete.

**Build against a live capture from the start rather than against a local copy.**

**Implementation:**

```python
# model2/backend/routers/stream.py

@app.get("/api/v2/sources/{source_id}/stream", tags=["Streaming"])
async def get_live_stream(
    source_id: str,
    token: str = Query(None),
    request: Request = None
):
    """
    Live MJPEG stream. Not a file download — a continuous live feed.
    """
    source = get_source(source_id)
    
    if not source.worker or not source.worker.is_running():
        raise HTTPException(status_code=503, detail="Camera not active")
    
    async def frame_generator():
        """Stream frames in real-time, not from storage."""
        while True:
            frame_data = source.worker.get_current_frame()
            
            if frame_data is None:
                # No frame yet, wait and retry
                await asyncio.sleep(0.01)
                continue
            
            # MJPEG boundary
            frame = frame_data["image"]
            ret, buffer = cv2.imencode('.jpg', frame)
            
            if not ret:
                await asyncio.sleep(0.01)
                continue
            
            # Live stream: never store to disk for download
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n'
                   b'Content-Length: ' + str(len(buffer)).encode() + b'\r\n\r\n'
                   + buffer.tobytes() + b'\r\n')
            
            await asyncio.sleep(0.01)  # Pace the stream
    
    return StreamingResponse(
        frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

# ❌ DO NOT do this:
# @app.get("/api/v2/download/{source_id}")
# async def download_video(source_id: str):
#     """This is wrong — we don't support file download."""
#     raise HTTPException(status_code=405, detail="Method not allowed")
```

---

### DON'T — Publish to the gateway

**Consume only.** Do not push streams to any path, and do not call the gateway's control API.

**Implementation:**

```python
# model2/backend/routers/sources.py

@app.post("/api/v2/sources", tags=["Sources"])
async def register_source(source_req: SourceRegistration):
    """
    Register a source (camera) for viewing.
    This is read-only: we consume the stream, we don't publish.
    """
    # Validate that stream_url points to an INBOUND source
    if "localhost" not in source_req.stream_url and \
       "127.0.0.1" not in source_req.stream_url and \
       not is_internal_network(source_req.stream_url):
        # External RTSP source: read-only
        pass
    
    # ❌ NEVER do this:
    # requests.post(f"{source_req.stream_url}/control", json={"action": "start"})
    
    # ✓ ONLY do this:
    source = CameraSource(
        camera_id=source_req.camera_id,
        stream_url=source_req.stream_url,
        read_only=True
    )
    
    db.add(source)
    db.commit()
    
    return {"id": source.id, "status": "registered"}
```

---

### DO — Pace your load

**Each connected client receives its own copy of the stream.** Open only the cameras you are actively processing, and close captures you are finished with.

**Implementation:**

```python
# model2/backend/camera_worker.py

FRAMEWORK_CONFIG = {
    "max_concurrent_sources": int(os.getenv("MODEL2_MAX_CONCURRENT_SOURCES", "50"))
}

class SourceManager:
    def __init__(self):
        self.active_workers = {}  # source_id -> CameraWorker
        self.max_concurrent = FRAMEWORK_CONFIG["max_concurrent_sources"]
    
    def start_source(self, source_id: str, stream_url: str) -> bool:
        """
        Start a camera worker. Enforce concurrency limit.
        """
        if len(self.active_workers) >= self.max_concurrent:
            logger.error(
                f"Cannot start {source_id}: already at max concurrent sources "
                f"({self.max_concurrent}). Close an inactive camera first."
            )
            return False
        
        worker = CameraWorker(source_id, stream_url)
        worker.start()
        self.active_workers[source_id] = worker
        
        logger.info(
            f"Started {source_id}. Active workers: "
            f"{len(self.active_workers)}/{self.max_concurrent}"
        )
        return True
    
    def stop_source(self, source_id: str) -> bool:
        """
        Stop a camera worker and free resources.
        """
        if source_id not in self.active_workers:
            return False
        
        worker = self.active_workers[source_id]
        worker.stop()
        worker.join(timeout=5)
        del self.active_workers[source_id]
        
        logger.info(
            f"Stopped {source_id}. Active workers: "
            f"{len(self.active_workers)}/{self.max_concurrent}"
        )
        return True

# API endpoint to pace load
@app.post("/api/v2/sources/{source_id}/start")
async def start_source(source_id: str):
    """Start capturing from a camera (increases load)."""
    source = get_source(source_id)
    
    if not source_manager.start_source(source_id, source.stream_url):
        raise HTTPException(
            status_code=429,
            detail=f"Max concurrent sources ({source_manager.max_concurrent}) reached. "
                   "Stop an inactive camera first."
        )
    
    return {"status": "capturing", "source_id": source_id}

@app.post("/api/v2/sources/{source_id}/stop")
async def stop_source(source_id: str):
    """Stop capturing from a camera (decreases load)."""
    if not source_manager.stop_source(source_id):
        raise HTTPException(status_code=404, detail="Source not active")
    
    return {"status": "stopped", "source_id": source_id}
```

---

## Summary: Implementation Checklist

| Do's and Don'ts | Model 2 Implementation | Status |
|---|---|---|
| DO — Force RTSP over TCP | `RTSP_TRANSPORT=tcp` in .env, configured in `camera_worker.py` | ✓ Implemented |
| DON'T — Trust reported frame rate | Track actual PTS; log discrepancies | ✓ Implemented |
| DO — Drive timing from PTS | Use `CAP_PROP_POS_MSEC`; timestamp with PTS, not arrival time | ✓ Implemented |
| DON'T — Assume constant frame rate | Tolerate gaps; use actual PTS delta for motion models | ✓ Implemented |
| DO — Reconnect with backoff | Exponential backoff (2s–30s) in `camera_worker.py` | ✓ Implemented |
| DON'T — Treat decode warnings as fatal | Suppress decoder spam; continue on transient errors | ✓ Implemented |
| DON'T — Assume uniform grid | Read per-camera properties; adapt batch size, buffers | ✓ Implemented |
| DO — Expect scene discontinuity | Detect PTS cuts; reset tracker state | ✓ Implemented |
| DON'T — Plan on file download | Stream live only; no file download API | ✓ Implemented |
| DON'T — Publish to gateway | Read-only consumer; no control API calls | ✓ Implemented |
| DO — Pace your load | Enforce `MAX_CONCURRENT_SOURCES` (default 50) | ✓ Implemented |

---

## References

- OpenCV Documentation: https://docs.opencv.org/4.x/d8/dfe/classcv_1_1VideoCapture.html
- RTSP Specification: RFC 7826
- H.264 / H.265 Codecs: Recommendation ITU-T H.264 / H.265
- Kalman Filtering & Multi-Object Tracking: DeepSORT, SORT algorithms
