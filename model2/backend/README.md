# Model 2 — Live Feed Relay & ANPR Backend

FastAPI backend that sits on top of Model 1's camera registry: registers
playable video sources, relays live feeds as MJPEG, and runs a plate
detection + OCR pipeline against recorded clips or sources, logging
results to a searchable events table.

**This connects to the SAME PostgreSQL database as Model 1** and reuses
its `users` table for login — there is no separate registration system
here. Log in via Model 1's `/api/v1/auth/login`, use that token here.

---

## 1. Prerequisites

- Model 1 already running at least once (so `users` and `cameras` tables
  exist and have data — Model 2's tables have foreign keys into `cameras`).
- Python 3.11+ (also tested against 3.13).
- The same PostgreSQL database Model 1 uses.

## 2. Setup

```cmd
cd C:\cctv_model2
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
notepad .env
```

In `.env`, set:
- `DATABASE_URL` → **same** value as Model 1's `.env`
- `SECRET_KEY` → **exact same value** as Model 1's `.env` (tokens are
  signed with this — if it doesn't match, Model 1's login token will be
  rejected here with a 401)

## 3. Run (alongside Model 1, on a different port)

```cmd
uvicorn main:app --reload --port 8001
```

Model 1 typically runs on `:8000` — use `:8001` here so both can run at
once on your machine. Docs: `http://127.0.0.1:8001/docs`

## 4. Typical flow

1. **Log in via Model 1** (`POST http://127.0.0.1:8000/api/v1/auth/login`) → get a token.
2. **Register a source** for an existing camera:
   ```
   POST /api/v2/sources
   { "camera_id": "<a cameras.id from Model 1>",
     "source_name": "Front Gate Feed",
     "source_type": "rtsp",
     "source_url": "rtsp://192.168.1.50:554/stream1" }
   ```
   (For local testing without real hardware, use `source_type: "file"` and
   point `source_url` at a local `.mp4` path.)
3. **Start the worker**: `POST /api/v2/sources/{source_id}/start`
4. **View it live**: open `http://127.0.0.1:8001/api/v2/sources/{source_id}/stream`
   directly in a browser, or embed it in an `<img>` tag on a dashboard —
   it's a standard MJPEG multipart stream.
5. **Run ANPR on it**: `POST /api/v2/anpr/jobs` with `{"source_id": "..."}`
6. **Check job status**: `GET /api/v2/anpr/jobs/{job_id}`
7. **Search detected plates**: `GET /api/v2/anpr/events?plate=MH12&from=2026-01-01T00:00:00`
8. **Flag one as "of interest"**: `PATCH /api/v2/anpr/events/{event_id}/flag`

---

## 5. Can it really handle 50 cameras?

Short answer: **yes for the registry/relay architecture — actually
tested, not just claimed** — with an honest caveat about hardware.

### What was tested (not simulated — a real stress test was run)

- Started **50 concurrent `CameraWorker` threads**, each opening its own
  video source and decoding frames independently.
- All 50 stayed alive, captured frames continuously, and none crashed
  or leaked into each other's memory.
- Confirmed the `MAX_CONCURRENT_SOURCES` cap actually blocks a 51st
  worker from starting (returns HTTP 409, doesn't silently degrade).
- Confirmed clean shutdown: stopping all 50 workers and joining their
  threads completed without hanging or forcing a process kill.

### The part that depends on YOUR hardware, not the code

- Each worker thread does real video decoding (OpenCV). CPU cost per
  camera scales with **resolution and codec**, not with this code.
  50× 1080p H.264 decodes will need a genuinely capable multi-core
  machine; 50× low-res test clips (like the one used above) barely
  register.
- Bandwidth: if 50 cameras are real RTSP feeds over a network, that's
  50 concurrent network streams landing on whatever machine runs this
  — that's a network/hardware planning question, not something the
  code can fix.
- The MJPEG relay re-encodes at `STREAM_TARGET_FPS` (default 8fps, not
  the source's native 15-30fps) specifically to cut this cost — tune it
  down further (e.g. 3-5fps) if 50 feeds strain a small machine.

### What's already built in to keep it safe at 50+

- Each camera's frame buffer holds only the **latest frame**, never a
  growing queue — a slow viewer can never cause memory to balloon.
- Auto-reconnect on a dropped/ended source (5s backoff) instead of a
  dead thread silently doing nothing forever.
- `MAX_CONCURRENT_SOURCES` (default 50, matching your requirement) is a
  hard gate, not a suggestion.

**Bottom line for your PPT/viva:** the architecture is proven to run 50
concurrent camera workers correctly. Whether it runs them *smoothly* at
full HD on real hardware is a deployment/sizing question — same as it
would be for literally any video relay system, commercial or custom.

---

## 6. ANPR pipeline setup (separate, heavier install)

The core app runs fine without this — only `/api/v2/anpr/jobs` needs it,
and it returns a clear `503` with install instructions if missing rather
than crashing.

```cmd
pip install ultralytics==8.3.0 easyocr==1.7.2
```

This pulls in PyTorch automatically (large download, several hundred MB).

**Important honesty note:** `ANPR_YOLO_WEIGHTS` defaults to `yolov8n.pt`,
a general-purpose object detector (trained on COCO classes like "car",
"person") — it does NOT know what a license plate looks like. It's
wired in so you can prove the pipeline runs end-to-end (video → detect →
crop → OCR → save to DB) without any extra setup. For actual plate
detection accuracy, download or train YOLO weights on a license-plate
dataset and point `ANPR_YOLO_WEIGHTS` at that `.pt` file — no code
change needed, just swap the file.

---

## 7. File storage structure (ANPR detection snapshots)

Every detected plate crop is saved to disk, organized like this:

```
media/                              <- STORAGE_ROOT (configurable in .env)
  anpr_snapshots/
    <camera_id>/
      2026-09-05/
        <event_id>.jpg
      2026-09-06/
        <event_id>.jpg
    <another_camera_id>/
      2026-09-05/
        <event_id>.jpg
```

- Grouped by **camera, then date** — so you can find "what did Camera X
  see on date Y" by browsing folders directly, without touching the
  database. It also avoids dumping tens of thousands of files into one
  folder, which slows down file listings on every OS.
- The database only stores a **relative path** (e.g.
  `anpr_snapshots/<camera_id>/2026-09-05/<event_id>.jpg`), never an
  absolute one — so moving the deployment to a different machine or
  drive doesn't silently break every existing record.
- Snapshots are served through an **authenticated endpoint**
  (`GET /api/v2/anpr/events/{event_id}/snapshot`), not a raw static file
  mount — so the same department-scoped access rules apply to images as
  to everything else. A path-traversal guard also blocks a malformed
  `snapshot_path` from ever resolving outside `STORAGE_ROOT`.
- Check total usage anytime via `GET /health` → `snapshot_storage`
  (file count + total MB).

**Retention/cleanup is not automated yet** — snapshots accumulate
indefinitely. For a real deployment, add a scheduled job (cron / APScheduler)
that deletes snapshot folders older than N days, matching the
`retention_days` concept Model 1 already stores per camera.

## 8. Project layout

```
database.py          Engine/session — points at Model 1's database
models.py              User (mirrors Model 1's table), CameraRef (read-only
                         reference to Model 1's cameras table), CameraSource,
                         AnprJob, AnprEvent
schemas.py              Pydantic request/response schemas
auth.py                  JWT validation only (no login/register — reuses
                          Model 1's)
camera_worker.py          CameraWorker (per-source thread) + SourceManager
                           (process-wide registry, start/stop/status)
storage.py                  Snapshot file storage — path layout, save/resolve,
                             path-traversal guard, disk usage stats
anpr_pipeline.py             Lazy-loaded YOLO + EasyOCR, frame-level detection
routers/sources.py            Register/list/update/delete sources, start/stop
                             workers, worker status
routers/stream.py            MJPEG live relay + single-frame snapshot
routers/anpr.py               ANPR job creation/status, event search/tagging,
                                per-event snapshot image retrieval
main.py                        App wiring, lifespan (clean worker shutdown)
```

## 8. Testing performed before delivery

- All modules import cleanly and the full OpenAPI schema builds (12
  routes, matching the deliverable table).
- `CameraWorker` actually run against a real generated test video —
  frames captured, buffer populated, clean stop confirmed.
- 50 concurrent workers started, verified all running and capturing,
  cleanly shut down (see section 5 above for exact results).
- `MAX_CONCURRENT_SOURCES` limit confirmed to actually reject a 51st
  worker rather than silently allowing it.
- ANPR module confirmed to degrade gracefully (clear 503-style error,
  not a crash) when `ultralytics`/`easyocr` aren't installed — this was
  the actual state of the test environment, not a simulated case.

## 9. Known simplifications (flag before production rollout)

- ANPR jobs run in a plain background thread, not a task queue — fine
  for spec/demo scale, swap for Celery/RQ if you need queueing, retries,
  or to run jobs on a separate worker machine.
- `create_all_tables()` again used instead of Alembic — same caveat as
  Model 1.
- Snapshot storage has no automated retention/cleanup yet — see section 7.