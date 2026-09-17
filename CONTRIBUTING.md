# Contributing

Notes for anyone working on this repo — conventions we've settled on,
and the traps that have already cost us time once.

## Repo layout

```
cctv_registry/
├── model1/{backend,frontend}    Registry & GIS          :8000 / :5173
├── model2/{backend,frontend}    Feed relay & ANPR       :8001 / :5174
├── model3/{backend,frontend}    Federation middleware   :8002 / :5175
├── start-all.sh                 Starts all six processes
└── logs/                        start-all.sh output (gitignored)
```

Each model is an independent service. They talk over HTTP only — never
import across model folders.

## Getting set up

See the root `SETUP.md`. Two things that are easy to get wrong and will
cost you an hour if you do:

- **Python 3.12**, not the newest release. 3.13/3.14 lack prebuilt wheels
  for `pydantic-core`, forcing a source build that fails.
- **Models 1 and 2 must share the exact same `DATABASE_URL` and JWT
  signing secret.** Model 2 reads Model 1's `users` and `cameras` tables
  directly and validates Model 1's tokens. A mismatch shows up as a
  confusing 401 on every Model 2 request, not as an obvious config error.

## Conventions

### Backend (FastAPI)

- **Route prefixes are versioned per model**: Model 1 `/api/v1`, Model 2
  `/api/v2`, Model 3 `/api/v1`, Model 4 `/api/v3`. These are service
  namespaces, not API versions — don't bump them for a schema change.
- **Only Model 1 issues logins.** Models 2/3/4 validate Model 1's JWTs
  and have no registration endpoints. Don't add one.
- **Department scoping is enforced server-side, every time.** Any endpoint
  touching camera-linked data calls `department_scope(current_user)` and
  filters on it. Never rely on the frontend to scope a query.
- **Heavy ML deps (`ultralytics`, `easyocr`, `torch`) are lazy-imported**,
  never at module top level. The app must start and every non-ML endpoint
  must work without them installed; ML endpoints return a 503 with install
  instructions instead. Keep it that way — it's what lets people run the
  stack without a multi-GB download.
- **Enum columns need `values_callable`.** Every `Column(Enum(...))` must
  pass `values_callable=lambda enum_cls: [e.value for e in enum_cls]`.
  Without it SQLAlchemy stores Python member *names* (`MAINTENANCE_REQUIRED`)
  while our raw SQL compares against *values* (`"Maintenance Required"`),
  and Postgres rejects it at runtime. This has bitten us once already.
- **Cast numpy scalars to plain Python types before they touch the DB.**
  `float(np_value)`, not the numpy float. psycopg's adaptation of numpy
  scalars is unreliable across versions and has produced nonsense errors
  like `schema "np" does not exist`.

### Frontend (React + Vite)

- **All network calls go through the `src/api/` seam** (`registry.js`,
  `viewer.js`, `federation.js`). Components never call `fetch` directly.
  Every API function has a `USE_MOCK` branch so the UI is fully
  developable with no backend running.
- **Mock data must match the real API's field names and casing exactly.**
  The backend speaks snake_case; if a component reads `plate_text`, the
  mock must provide `plate_text`, not `plateNumber`. Mocks that drift
  from reality are worse than no mocks.
- **Ports are fixed per model** (5173/5174/5175/5176) so all frontends can
  run simultaneously. Don't change them.
- Styling uses shared design tokens (`styles/tokens.css`) — the same
  palette across all four models. Use `var(--...)`, not hex literals.

### Comments

Comment the *why*, not the *what*. Specifically, when something is
non-obvious or was the result of debugging, say so — several of the
comments in this codebase exist because someone lost an afternoon to
that exact thing. Don't delete those.

## Testing before you push

There is no CI. Run these yourself:

```bash
# Backend: does it still import and register every route?
cd modelN/backend && .testing/bin/python -c "from main import app; print(len(app.openapi()['paths']))"

# Frontend: does it still build?
cd modelN/frontend && npm run build
```

Standalone debug tools worth knowing about:

- `model2/backend/test_anpr_clip.py` — runs the ANPR pipeline against one
  clip with full debug output, no FastAPI/Postgres/threads involved. Use
  this to iterate on detection accuracy, not the full job endpoint round-trip.
- `model3/backend/test_model1_connection.py` — verifies Model 3's service
  account can actually authenticate to Model 1, independently of starting
  the app.

## Things not to commit

`.env` files are ok for now as they won't be taken into production and do not
really contain information that should be problematic if shared.

`package.json`, `package-lock.json`, and `vite.config.js` **are** source
files and must be committed.

## Working with the Sentinel sandbox

The live camera grid has protocol requirements documented in the
hackathon's integration guide. The ones that affect our code:

- **RTSP must be forced over TCP.** Already handled in
  `camera_worker.py` via `OPENCV_FFMPEG_CAPTURE_OPTIONS` — that env var
  is read by ffmpeg at capture-open time, so it must be set before any
  `VideoCapture` is constructed. Don't move it.
- **Never derive timing from `CAP_PROP_FPS` or frame arrival time.** The
  reported FPS is unreliable and the gateway replays a buffered GOP on
  connect, so the first second of frames arrives faster than real time.
  Use PTS (`CAP_PROP_POS_MSEC`) for anything time-derived.
- **Decoder warnings on join are normal**, not fatal. Attaching
  mid-stream logs reference-frame errors until the first IDR arrives.
- **Feeds loop.** At the loop point the scene cuts hard. Anything holding
  long-lived state (background models, track IDs) must survive that.
- **Prefer HLS over RTSP on an unreliable link.** Same content, TCP
  segment delivery, can't hand the decoder a corrupt bitstream. Costs a
  few seconds of latency, which doesn't matter for ANPR.

## Honesty in docs

Every README here states what is genuinely built versus what is
documented-but-not-implemented (stock YOLO weights not being plate-trained,
Model 4's scoped subset, etc.). Keep doing that. If you stub something,
say it's a stub — an overstated README is worse than a missing feature,
especially for a government submission.
