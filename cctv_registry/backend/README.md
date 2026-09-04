# Centralised CCTV Registry & GIS Mapping Model (Model 1)

FastAPI + PostgreSQL/PostGIS backend for a state-wide CCTV camera registry:
department-scoped RBAC, manual entry, bulk import, vendor onboarding webhook,
GIS bounding-box search, and infrastructure gap-analysis reporting.

## 1. Prerequisites

- Python 3.11
- PostgreSQL 14+ with the **PostGIS** extension available
  (`postgis` package installed on the DB server; the app runs
  `CREATE EXTENSION IF NOT EXISTS postgis` at startup — the DB role needs
  privilege to do so, or a superuser must run it once beforehand).

```bash
createdb cctv_registry
psql cctv_registry -c "CREATE EXTENSION IF NOT EXISTS postgis;"  # if your role lacks privilege
```

## 2. Setup

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: DATABASE_URL, SECRET_KEY, VENDOR_API_KEYS
```

## 3. Run

```bash
uvicorn main:app --reload
```

- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc
- Health check: http://127.0.0.1:8000/health

Tables and the PostGIS extension are created automatically on startup
(`database.create_all_tables`). **This is a quick-start convenience —
for production, replace it with Alembic migrations** so schema changes are
versioned and reviewable.

## 4. First login (bootstrap)

The very first account created via `POST /api/v1/auth/register` is
automatically granted the `admin` role (since the table starts empty).
After that, registration is closed to the public — an Admin must create
further users via `POST /api/v1/auth/register-as-admin`.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@state.gov","password":"changeme123","department":"Admin","role":"admin"}'

curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -d "username=admin@state.gov&password=changeme123"
# -> {"access_token": "...", "token_type": "bearer"}
```

Use the token as `Authorization: Bearer <token>` on subsequent requests.

## 5. RBAC model

- **Admin** (role=`admin`, or department=`Admin`): full access across all
  departments.
- **Operator / Viewer**: scoped to their own `department`. Reads are
  auto-filtered to that department; writes (`POST`/`PATCH`/`DELETE`,
  bulk-upload rows) to another department return `403`.
- Roles are enforced via the `department_scope` and `enforce_department_write`
  dependencies in `auth.py` — reuse those in any new endpoint rather than
  reimplementing the check.

## 6. Vendor onboarding webhook

External vendor systems authenticate with an `X-API-Key` header (not JWT).
Configure keys in `.env`:

```
VENDOR_API_KEYS=vendor-key-police-001:Police,vendor-key-transport-001:Transport
```

Each key is bound to exactly one department; a vendor pushing a camera
for a different department gets `403`.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/cameras/register-vendor \
  -H "X-API-Key: vendor-key-police-001" \
  -H "Content-Type: application/json" \
  -d '{
    "camera_identifier": "PLC-VEND-0001",
    "camera_name": "MG Road Junction Cam",
    "latitude": 21.7051, "longitude": 72.9959,
    "department": "Police", "camera_type": "PTZ", "ownership": "Government",
    "connectivity_status": "Online",
    "storage_details": {"retention_days": 30, "storage_type": "cloud", "capacity_tb": 2.0},
    "health_status": "Operational",
    "installation_date": "2024-01-15"
  }'
```

## 7. Bulk upload

`POST /api/v1/cameras/bulk-upload` — multipart file upload, CSV or Excel.
Required flat columns (nested `storage_details` fields are flattened):

```
camera_identifier, camera_name, latitude, longitude, department,
camera_type, ownership, connectivity_status, retention_days,
storage_type, capacity_tb, health_status, installation_date,
last_ping (optional), stream_endpoint (optional)
```

Each row is validated independently; valid rows are inserted in a single
DB transaction, invalid rows are reported by row number with error detail.
Duplicate `camera_identifier` values (within the file, or already in the DB)
are rejected as row errors rather than failing the whole batch.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/cameras/bulk-upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@cameras.csv"
```

## 8. GIS search

`GET /api/v1/cameras?bbox=min_lon,min_lat,max_lon,max_lat` returns cameras
whose location intersects the given bounding box (PostGIS `ST_Intersects`
against a `ST_MakeEnvelope`). Combine with `department`, `camera_type`,
`connectivity_status`, `health_status`.

## 9. Gap-analysis report

`GET /api/v1/analytics/gap-analysis` returns:

- **Ageing infrastructure** — cameras installed >5 years ago or flagged
  `Maintenance Required`, with computed age in years.
- **Coverage density** — `ST_ClusterDBSCAN` high-density clusters
  (tune `dbscan_eps_meters` / `dbscan_min_points`), plus a grid-count
  dead-zone scan (`grid_cells_per_side`) over the scoped bounding box.
- **Summary** — a plain-language headline and recommended actions, meant
  to be pasted directly into a leadership briefing.

Non-admins get this auto-scoped to their own department; Admins can pass
`?department=Police` or omit it for a state-wide view.

## 10. Project layout

```
database.py          SessionLocal, engine, PostGIS bootstrap
models.py             SQLAlchemy + GeoAlchemy2 ORM models
schemas.py            Pydantic request/response schemas
auth.py                Password hashing, JWT, RBAC + vendor API-key dependencies
geo_utils.py           lat/lon <-> PostGIS geometry helpers
routers/auth_routes.py Login, registration
routers/cameras.py      CRUD, bulk upload, vendor webhook, GIS search
routers/analytics.py    Gap-analysis report
main.py                 App wiring, CORS, global error handlers, lifespan startup
```

## 11. Known simplifications (flag before production rollout)

- `create_all_tables()` is used instead of Alembic migrations — fine for a
  fresh dev DB, not for iterating on a live schema.
- DBSCAN `eps` is converted from meters to degrees with a flat approximation
  (~111.32 km/degree). Accurate at state scale; for precise metro-level
  clustering, transform geometries to a local projected CRS instead.
- CORS is wide open (`allow_origins=["*"]`) — restrict to your actual
  dashboard origin(s) before deployment.
- No rate limiting / audit logging middleware is included — add these at
  the infra layer (API gateway / reverse proxy) for a state deployment.
