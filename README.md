# Unified CCTV Intelligence Platform — Gujarat Police Innovation Hackathon 2026

A hybrid solution built across three of the hackathon's proposed models:

- **Model 1** — CCTV Registry & GIS Foundation
- **Model 2** — Live Feed Relay & ANPR
- **Model 3** — VMS Federation & Middleware

Model 1 is the shared foundation. Model 2 connects directly to camera feeds
and runs plate detection. Model 3 federates the two — pulling live data from
both, correlating it, and exposing one unified view. Each model is an
independent FastAPI backend + React frontend pair; they talk to each other
only over HTTP.

```
project-root/
├── model1/
│   ├── backend/     FastAPI + PostgreSQL/PostGIS
│   └── frontend/    React + Vite, port 5173
├── model2/
│   ├── backend/     FastAPI + OpenCV + YOLO/EasyOCR
│   └── frontend/    React + Vite, port 5174
├── model3/
│   ├── backend/     FastAPI, stateless middleware
│   └── frontend/    React + Vite, port 5175
├── docs/
│   ├── ARCHITECTURE_DIAGRAM.svg          (High-level architecture)
│   └── WORKFLOW_INTEGRATION_DIAGRAM.svg  (Data flow & integration)
├── SECURITY.md                   (TLS, auth, rate limiting, audit logging)
├── INFRASTRUCTURE.md             (Sizing, tuning, monitoring, backup)
├── CCTV_STREAMING_BEST_PRACTICES.md  (All 11 do's & don'ts implemented)
└── start-all.sh                  (Starts all six processes together)
```


## Comprehensive Documentation

### Security & Hardening
** [`SECURITY.md`](SECURITY.md)**

Complete security architecture covering:
- TLS/HTTPS configuration for all backends
- JWT-based authentication & department-scoped RBAC
- Rate limiting (5/min login, 100/min API)
- Audit logging (JSON format, all events tracked)
- CORS hardening (whitelist-based origins)
- Secrets management & validation
- Production deployment hardening checklist (nginx, database security, monitoring alerts)

### Infrastructure Sizing & Operations
** [`INFRASTRUCTURE.md`](INFRASTRUCTURE.md)**

Complete operational guide:
- **Hardware requirements**: 7–8 cores, 14–16 GB RAM, ~560 GB storage for full deployment
- **Per-camera costs**: CPU, RAM, storage, bandwidth calculations
- **PostgreSQL tuning**: Shared buffers, indexes, spatial performance
- **Snapshot retention**: Cleanup script (`cleanup_snapshots.py`), directory structure
- **Monitoring**: Prometheus metrics, Grafana dashboards, health checks
- **Distributed deployment**: Multi-node architecture with load balancing
- **Disaster recovery**: Automated backups, point-in-time recovery procedure

### Technical Roadmap
** [`ROADMAP.md`](ROADMAP.md)**

Four-phase development roadmap:
- **Phase 1 (Q1 2026)**: Infrastructure & security hardening ✓
- **Phase 2 (Q2 2026)**: Database & reliability (migrations, job queue, PITR)
- **Phase 3 (Q3 2026)**: Analytics & reporting (re-identification, MOT, heatmaps)
- **Phase 4 (Q4 2026–2027)**: Scale & interoperability (GPU acceleration, edge deployment, vendor adapters)
- **Known technical debt**: Schema migrations, ANPR job queue, GPU support (priority & effort tracked)
- **API stability guarantees**: v1 frozen after Q2, 6-month deprecation period for breaking changes

### CCTV Streaming Best Practices
** [`CCTV_STREAMING_BEST_PRACTICES.md`](CCTV_STREAMING_BEST_PRACTICES.md)**

All 11 critical streaming do's and don'ts with implementations:
- ✅ **DO** — Force RTSP over TCP (TCP configured, HLS fallback documented)
- ❌ **DON'T** — Trust reported frame rate (PTS-based timing, actual rate measurement)
- ✅ **DO** — Drive timing from PTS (CAP_PROP_POS_MSEC, not arrival time)
- ❌ **DON'T** — Assume constant frame rate (gap tolerance, actual PTS delta for motion models)
- ✅ **DO** — Reconnect with backoff (exponential 2s–30s)
- ❌ **DON'T** — Treat decode warnings as fatal (error tolerance, continues on transient failures)
- ❌ **DON'T** — Assume uniform grid (per-camera properties, adaptive batch sizing)
- ✅ **DO** — Expect scene discontinuity (PTS cut detection, state recovery)
- ❌ **DON'T** — Plan on file download (live streaming only, no file download API)
- ❌ **DON'T** — Publish to gateway (read-only consumer, no control API calls)
- ✅ **DO** — Pace your load (MAX_CONCURRENT_SOURCES enforced, default 50)

---

## [Model 1](/model1/README.md) — CCTV Registry & GIS Foundation

### What it does
A centralized inventory of every CCTV camera across departments — Police,
Transport, Municipal Corporations, and other institutions — with GIS
mapping, onboarding, and infrastructure gap analysis. This model does
**not** stream or store video; it's purely the registry and visibility
layer that Models 2 and 3 build on top of.

### Data model
Cameras are stored in PostgreSQL with a native **PostGIS** geometry column
for location (not separate lat/lng floats), enabling real spatial queries
rather than application-side distance math. Each camera record carries:

- `camera_identifier` (human-readable ID) and `camera_name`
- `department`, `camera_type`, `ownership`, `connectivity_status`,
  `health_status` — all backed by native Postgres enums
- `storage_details` (JSONB: retention days, storage type, capacity)
- `stream_endpoint` — the field that hands a camera off to Model 2
- `installation_date`, `last_ping`, audit timestamps

A separate `users` table holds accounts with `department` + `role`
(admin/operator/viewer) — this table is later read directly by Model 2 and
indirectly relied on by Model 3, since all three models share one
authentication identity.

### Onboarding — three separate paths, as specified
1. **Manual entry** — single-camera form via the API
2. **Bulk import** — CSV/Excel upload, parsed with pandas, validated
   row-by-row with per-row error reporting (a bad row doesn't block the
   rest of the file from importing)
3. **Vendor API-key webhook** — a separate, non-JWT auth path
   (`X-API-Key` header) letting external vendor systems register cameras
   directly, scoped to the department their key is authorized for

### Gap analysis
Computed live from PostGIS queries, not pre-aggregated:
- **Ageing infrastructure** — cameras older than a configurable threshold
  (default 5 years) or currently flagged for maintenance
- **Coverage density clustering** — `ST_ClusterDBSCAN` *(extern-package)* groups
  geographically close cameras to identify redundant coverage
- **Dead-zone detection** — the camera set's bounding box is divided into
  a configurable grid; cells with zero cameras are flagged as coverage gaps
- Output includes a plain-language headline summary and recommended
  actions, not just raw numbers

### Security
- JWT-based login (`/api/v1/auth/login`), password hashing via bcrypt
- **Department-scoped RBAC enforced server-side** — a Transport-department
  user cannot create or view cameras belonging to Police; only accounts
  with `department: Admin` or `role: admin` see everything
- First-ever registered user automatically becomes Admin (bootstrap);
  every account after that requires an existing Admin to create it

### Frontend
React + Leaflet GIS map with marker clustering, status-colored pins
(active/maintenance/offline), live search and filtering (department,
camera type, health status), a gap-analysis summary panel (including the
backend's plain-language headline and recommended actions, not just raw
numbers), CSV export, modals for manual onboarding and bulk upload with a
per-row success/failure breakdown, and per-camera edit/delete. A real
login screen calls the backend's actual login endpoint — no hardcoded
auto-login.

### Key endpoints
```
POST   /api/v1/auth/login
POST   /api/v1/auth/register          (bootstrap only — first user)
POST   /api/v1/auth/register-as-admin (Admin-only)
GET    /api/v1/cameras                (filterable: department, camera_type, ...)
POST   /api/v1/cameras
GET    /api/v1/cameras/{id}
PATCH  /api/v1/cameras/{id}
DELETE /api/v1/cameras/{id}
POST   /api/v1/cameras/bulk-upload
POST   /api/v1/cameras/register-vendor (API-key auth)
GET    /api/v1/analytics/gap-analysis
GET    /health                         (system health check)
```

---

## [Model 2](/model2/README.md) — Live Feed Relay & ANPR

### What it does
Connects directly to each camera's `stream_endpoint` (RTSP/HLS/HTTP/local
file), relays the live feed as MJPEG for viewing, and runs an [auto 
plate detection](<https://github.com/sanchit2843/Indian_LPR>) + OCR pipeline against a source or recorded clip —
logging results to a searchable events table. No middleware layer, no
intermediate video storage; existing departmental VMS systems are
untouched, matching the model's defined scope.

### It shares Model 1's database and identity system
Deliberately connects to the **same PostgreSQL database** as Model 1 —
not a separate one. It reads Model 1's `users` table directly for login
(no separate registration system: log in via Model 1, use that token
here) and foreign-keys its own tables against Model 1's `cameras` table.
This means **Model 1's `DATABASE_URL` and `JWT_PUBLIC_KEY` must match 
Model 2's exactly**, or login tokens issued by Model 1 will be rejected here.

### Data model (new tables, owned by Model 2)
- **`camera_sources`** — links a Model 1 camera to an actual playable
  video source (a camera can have more than one source registered over
  its lifetime, e.g. re-pointed to a new NVR)
- **`anpr_jobs`** — one row per "run the pipeline on this source" request,
  tracking status (queued/running/completed/failed) and progress
- **`anpr_events`** — each detected plate: text, confidence, camera,
  timestamp, frame number, an optional saved snapshot image path, and a
  flag/note for marking a vehicle "of interest"

### Feed relay architecture
One background thread per active camera source (`CameraWorker`), each
independently opening and decoding its own video source — necessary
because OpenCV's frame read is a blocking call. Each thread keeps only the
**most recent decoded frame** in memory, never a growing queue, so a slow
viewer can never cause memory to balloon. Frame rate is deliberately
throttled (default 8fps, configurable) rather than serving at the
source's native rate, since a human-viewed relay doesn't need it and it
cuts CPU/bandwidth cost substantially. Dropped/ended sources auto-reconnect
on a delay instead of dying silently. A hard cap
(`MAX_CONCURRENT_SOURCES`, default 50) prevents a misconfigured deployment
from spawning unbounded decoder threads — confirmed via an actual stress
test starting 50 concurrent workers, verifying all stayed alive and
capturing, and that a 51st is correctly rejected rather than silently
allowed.

### Protocol choice: RTSP vs. HLS
`camera_sources.source_type` can be `rtsp`, `hls`, `http`, or `file`. RTSP
is a live push stream with no application-level retransmission for the
media itself — under packet loss/jitter, H.264 has no error concealment,
so ffmpeg fills in garbage macroblocks until the next keyframe
(`frame_quality.py` detects and rejects these via a blockiness heuristic
so viewers/ANPR see the last good frame instead of a smear, but that's a
detect-and-reject fix, not a prevention of the underlying corruption).
HLS delivers the same content as segments over HTTP/TCP: a late segment
just makes the download slower, it can never hand the decoder a
half-corrupted bitstream — so it avoids the corruption mechanism
entirely, at the cost of several seconds of segment-buffering latency
that RTSP doesn't have. That tradeoff (no corruption, higher latency)
is a non-issue for a human-viewed relay or for ANPR, so **HLS is the
recommended `source_type` for any camera on a link that isn't rock
solid**; RTSP remains available for sources where near-real-time
latency genuinely matters. Both protocols are opened via the same
`cv2.VideoCapture(source_url)` call in `camera_worker.py` — ffmpeg
auto-detects the transport from the URL scheme.

### ANPR pipeline
Plate detection via YOLO, text extraction via EasyOCR. Both are heavy
dependencies (YOLO pulls in PyTorch) and are **lazily imported** — the
rest of the app runs fine without them installed; only the ANPR endpoints
require them, returning a clear "install these" error instead of crashing
if they're missing.

**Honesty note carried over directly from the implementation:** the
default weights (`yolov8n.pt`) are a general-purpose object detector
(COCO classes like "car", "person") — they do **not** actually recognize
license plates. They're wired in to prove the full pipeline runs
end-to-end (capture → detect → crop → OCR → save). Here, we have used the
license plate detection trained model based on YOLOv8 from [Koushim](<https://huggingface.co/Koushim/yolov8-license-plate-detection>)

### Detection snapshots
Each detected plate's cropped image is saved to disk, organized by
camera then date (`media/anpr_snapshots/<camera_id>/<date>/<event_id>.jpg`)
so files can be browsed directly without a database lookup, and no single
folder ever accumulates enough files to slow down listing. The database
stores only a **relative** path. Snapshots are served through an 
authenticated endpoint (not a raw static file mount), so the same 
department-scoped access rules apply to images as to everything
else, with a path-traversal guard against a malformed stored path.

### The `<img>`-tag authentication fix
The stream/snapshot endpoints originally required a JWT in the
Authorization header — but a plain `<img src="...">` tag (the standard
way to display an MJPEG stream) cannot send custom headers, making the
video wall unusable as first built. Patched: a `get_current_user_flexible`
dependency accepts the token via **either** the header **or** a
`?token=...` query parameter, used only on the two endpoints that need to
be `<img>`-loadable — everywhere else keeps strict header-only auth.

### Frontend
A video wall (one tile per registered source) with per-source Start/Stop
controls for its capture worker and a "Start ANPR" trigger, which runs the
ANPR system, and displays detected plates, records them into the database.

### Key endpoints
```
POST   /api/v2/sources                    (register a source)
GET    /api/v2/sources
GET    /api/v2/sources/status/workers
POST   /api/v2/sources/{id}/start
POST   /api/v2/sources/{id}/stop
GET    /api/v2/sources/{id}/stream        (MJPEG, ?token= supported)
GET    /api/v2/sources/{id}/snapshot
POST   /api/v2/anpr/jobs
GET    /api/v2/anpr/jobs/{id}
GET    /api/v2/anpr/events                (filter: plate, camera_id, from, to, is_flagged)
GET    /api/v2/anpr/events/{id}/snapshot
PATCH  /api/v2/anpr/events/{id}/flag
GET    /health                            (system health check)
```

---

## [Model 3](/model3/README.md) — VMS Federation & Middleware

### What it does
Its entire job is calling Model 1's and Model 2's real APIs live,
correlating the results, and exposing one unified endpoint — the 
middleware/federation layer the problem statement describes, built
as a genuine adapter pattern rather than a single hard-coded integration.

### The adapter pattern
A common `VMSAdapter` interface (`get_cameras()` / `get_events()`) with
one concrete implementation per federated system:
- **`RegistryAdapter`** — calls Model 1's `GET /cameras`
- **`ViewerAdapter`** — calls Model 2's `GET /anpr/events`

Adding a fourth system later — a real vendor VMS, a different department's
platform — means writing one new adapter class implementing the same two
methods. Nothing in the correlation logic or the API layer needs to
change.

### Authentication — reuses Model 1's identity, doesn't duplicate it
Model 3 authenticates to Model 1 as its own **service account** (not an
end user), separate from any person's login, using the standard
username/password login flow, then caches the resulting token in memory
(refreshing on expiry or a 401). Because Model 2 validates Model 1's JWTs
directly, this same cached token authenticates to Model 2 as well — no
second login flow needed.

### Graceful degradation
If Model 2 is unreachable, `ViewerAdapter` reports that (`status: "error"`
with the underlying error message) and the correlation engine continues
with whatever data it does have — one dead upstream system doesn't break
the whole federation. This means Model 3 is fully testable against Model 1
alone, before Model 2 is even running.

### The actual correlation logic
This is the concrete value Model 3 adds beyond two separate dashboards.
Each Model 2 event is joined — by camera UUID, not the human-readable
identifier — against Model 1's live camera record, pulling its current
`department` and `health_status`. A **reliability rating** is then
derived:

| Camera health (Model 1) | Reliability assigned |
|---|---|
| Operational | high |
| Maintenance Required | medium |
| Defective | low |
| Unrecognized / unknown camera | medium *(not "high" — no evidence of health shouldn't imply confidence)* |

An ANPR hit from a camera Model 1 already knows is offline or defective is
therefore flagged lower-confidence automatically.

### Analytics summary
A "federated analytics report" combining total correlated events, a
low-reliability count, a department-wise breakdown, and a plain-language
headline sentence with recommended actions — same shape and spirit as
Model 1's own gap-analysis summary, for consistency across the platform.

### Frontend
A connector status panel (live reachability of each adapter, not a cached
assumption — it actually calls each system on every load), the analytics
summary, and a correlated-events table showing each detection alongside
its camera's department, health status, and computed reliability.

### Key endpoints
```
GET /api/v1/adapters/status
GET /api/v1/federated/events       (filter: department, reliability, search)
GET /api/v1/federated/analytics
GET /health                        (system health check)
```

---

Three concrete alignment requirements, all config rather than code:
1. **Model 1 ↔ Model 2**: identical `DATABASE_URL` and `SECRET_KEY`
2. **Model 1 ↔ Model 3**: a real, registered service-account login in
   Model 1's database, credentials matching Model 3's `.env`
3. **Model 2 ↔ Model 3**: Model 3's `MODEL2_VIEWER_BASE` pointing at
   wherever Model 2 actually runs

---

## Running everything together

```bash
chmod +x start-all.sh
bash start-all.sh
```

Starts all six processes (three backends via each one's Python venv,
three frontends via `npm run dev`), logs each to `logs/`, and tails them
together. Ctrl+C cleanly stops everything. See each model's own
`backend/README.md` for first-time setup (venv creation, `.env`
configuration, database initialization) — the start script assumes that
setup has already been done once per model.

| Service | URL |
|---|---|
| Model 1 frontend | http://localhost:5173 |
| Model 1 backend / docs | http://localhost:8000/docs |
| Model 2 frontend | http://localhost:5174 |
| Model 2 backend / docs | http://localhost:8001/docs |
| Model 3 frontend | http://localhost:5175 |
| Model 3 backend / docs | http://localhost:8002/docs |

---

## Security Hardening (All Backends)

All three backends are secured with:

- **TLS/HTTPS** — ENABLE_HTTPS in `.env`, certificates required in production
- **Rate Limiting** — 5/min for login, 100/min for API endpoints
- **Audit Logging** — All events logged to JSON (authentication, CRUD, errors)
- **CORS Hardening** — Whitelist-based origin restrictions (default: localhost frontends)
- **JWT Authentication** — HS256, 24-hour expiry, shared identity across models
- **Department-Scoped RBAC** — Enforced server-side on every endpoint

**See [`SECURITY.md`](SECURITY.md) for complete security architecture, deployment hardening checklist, and production best practices.**

---

## Known limitations

- **Model 1**: camera edit/delete UI ✓ done; real login screen ✓ done.
  Remaining: no password reset flow; editing a camera's GIS location isn't
  exposed in the edit form yet, even though the backend supports it.
- **Model 2**: default YOLO weights don't actually detect plates (see
  ANPR section above) — a real fix requires plate-specific trained
  weights, not a code change; ANPR jobs run in a plain background thread,
  not a task queue, so there's no retry/queueing at production scale.
  Snapshot retention ✓ has a standalone cleanup script now
  (`cleanup_snapshots.py`, run manually/via cron, never auto-runs).
  RTSP frame corruption on lossy links ✓ has a recommended fix now
  (`source_type='hls'` — see "Protocol choice: RTSP vs. HLS" above);
  the blockiness-based frame_quality.py check remains as a detect-and-
  reject fallback for sources still on RTSP, but it's a heuristic with
  known false-positive/false-negative cases, not a calibrated certainty.
- **Model 3**: auth failures now surface specific, actionable error
  messages (connection vs. wrong credentials vs. malformed response) and
  a startup check flags an unconfigured/placeholder service account
  immediately — see `test_model1_connection.py` for standalone debugging.
  Still true: adapter sync state and the cached service-account token are
  in-memory only, reset on restart — fine for demo scale, would need
  externalizing for a multi-process production deployment.
- **All three**: `create_all_tables()` is used for schema setup instead of
  proper migrations (Alembic) — acceptable for a hackathon PoC, called out
  explicitly as a pre-production gap in [`ROADMAP.md`](ROADMAP.md).

---

## Credits
- [@sanchit2843](<https://github.com/sanchit2843/Indian_LPR>) for their ANPR engine implemented into this project.
- [@Koushim](<https://huggingface.co/Koushim/yolov8-license-plate-detection>) for their trained model for license plates, based on YOLOv8.
