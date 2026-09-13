# Model 3 — VMS Federation & Middleware

## What this is, conceptually

Model 3 owns **no data of its own**. It's not a database-backed service —
it's a stateless middleware layer whose entire job is to call Model 1's
and Model 2's real, live APIs, join the results together, and expose one
unified view. Every response you get from Model 3 reflects the actual
current state of both upstream systems at request time, not a cached or
pre-synced copy.

This directly implements the "VMS Federation & Middleware" model from the
problem statement: an integration layer that lets independently-owned
systems (Model 1's registry, Model 2's viewer) be queried and correlated
through a single interface, without either of those systems needing to
know Model 3 exists.

---

## The adapter pattern

The core architectural idea, in `adapters.py`:

```python
class VMSAdapter(ABC):
    @abstractmethod
    async def get_cameras(self) -> AdapterResult: ...
    @abstractmethod
    async def get_events(self) -> AdapterResult: ...
```

Every federated system gets **one adapter class** implementing this same
interface. Right now there are two:

- **`RegistryAdapter`** — talks to Model 1, implements `get_cameras()`
  (returns camera metadata) and a no-op `get_events()` (Model 1 has no
  concept of events)
- **`ViewerAdapter`** — talks to Model 2, implements `get_events()`
  (returns ANPR detections) and a no-op `get_cameras()`

**Why this matters beyond being "clean code":** adding a fourth system
later — a real vendor VMS, a different department's platform — means
writing one new class with these same two methods. Nothing in
`main.py`'s endpoints or `correlation.py`'s join logic needs to know or
care how many adapters exist, or what protocol each one speaks
underneath. That's the actual "extensible connector framework" the
problem statement asks for, not just a description of one.

### `AdapterResult` — honesty by construction

Every adapter method returns an `AdapterResult`, not raw data:

```python
class AdapterResult:
    def __init__(self, ok: bool, data=None, error: str | None = None):
        self.ok = ok
        self.data = data if data is not None else []
        self.error = error
```

This forces every caller to handle "this adapter failed" as a real,
distinct case — not something that gets silently swallowed into an empty
list that looks identical to "this system genuinely has zero records."

---

## Authentication: how Model 3 talks to Model 1 and Model 2

### The service account

Model 3 doesn't authenticate as any real person. It logs into Model 1
using its **own dedicated account** — the same login flow (`POST
/api/v1/auth/login`) any user would use, but with credentials that live
only in Model 3's `.env`:

```
MODEL1_SERVICE_EMAIL=model3-service@example.com
MODEL1_SERVICE_PASSWORD=<whatever you set it to>
```

**This account must be created inside Model 1's database before Model 3
will work.** Model 3 cannot create it — it can only log into an account
that already exists. See "First-time setup" below.

The resulting JWT is cached in memory (`model1_auth.py`) and reused for
roughly 30 minutes (`MODEL1_TOKEN_CACHE_SECONDS`) rather than logging in
on every single request. If a cached token gets rejected (a 401
mid-lifetime — e.g. the account was deactivated), both adapters
invalidate the cache and re-authenticate once before giving up.

### Why Model 3 doesn't need a second login for Model 2

Model 2 has no login system of its own — it validates Model 1's JWTs
directly (same `SECRET_KEY`, same `users` table, confirmed in Model 2's
own `auth.py`). So the **exact same cached token** that authenticates
`RegistryAdapter` to Model 1 also authenticates `ViewerAdapter` to Model
2. There is deliberately no separate `model2_auth.py` — it would be
redundant.

### Clear, actionable failure messages (`model1_auth.py`)

Rather than letting a raw `httpx` exception bubble up when something goes
wrong, `get_model1_token()` raises a `Model1AuthError` with a message
specific to what actually happened:

| Situation | Message you'll see |
|---|---|
| Model 1 isn't running / wrong URL | "Could not connect to Model 1 at `<url>` — is it running?" |
| Model 1 is up but slow/stuck | "did not respond within the timeout" |
| Wrong credentials, or account doesn't exist yet | "Model 1 rejected login for `<email>` — either this account doesn't exist yet... or the password doesn't match" |
| Login succeeded but response was malformed | "response didn't contain an access_token as expected" |

### Startup config check

`main.py` calls `model1_auth.config_warnings()` at import time and logs
anything suspicious loudly, **before** the app even starts accepting
requests:
- Email or password not set at all
- Email still matches the literal template placeholder
  (`user@example.com`) — a strong signal you copied `.env.example`
  without actually updating it

This means a misconfigured service account is caught the moment you run
`uvicorn`, not silently discovered later as a mysterious 401 on your
first real request.

---

## First-time setup

```bash
python3.12 -m venv .testing
source .testing/bin/activate          # bash/zsh
# source .testing/bin/activate.fish   # fish shell

pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` — the URLs can stay as defaults if Model 1/2 run on their
usual ports, but you must set real values for `MODEL1_SERVICE_EMAIL` /
`MODEL1_SERVICE_PASSWORD`.

### Creating the service account (one-time, manual — Model 3 can't do this itself)

1. Make sure Model 1's backend is running.
2. Log into Model 1's `/docs` as an existing **Admin** account (the very
   first account ever registered in Model 1 automatically becomes Admin).
3. Use `POST /api/v1/auth/register-as-admin` with:
   ```json
   {
     "email": "model3-service@example.com",
     "password": "<match your .env>",
     "department": "Admin",
     "role": "viewer"
   }
   ```
   `department: "Admin"` matters — it's what gives this account
   unrestricted visibility across every department's cameras, which a
   federation service account needs (it shouldn't be artificially scoped
   to one department).

### Verifying it actually worked

Don't just start the app and hope — run the standalone connection test:

```bash
.testing/bin/python test_model1_connection.py
```

This checks your config, attempts the login, and separately verifies the
resulting token actually works against Model 1's `/auth/me` — printing
exactly who you're logged in as and warning you if the account isn't
Admin-scoped. This is faster to debug than starting the full app and
reading through connector-status JSON.

### Run it

```bash
uvicorn main:app --reload --port 8002
```

Port `8002` — deliberately different from Model 1 (`8000`) and Model 2
(`8001`). Check `http://localhost:8002/health` and
`http://localhost:8002/docs`.

---

## The correlation logic — the actual point of this model

`correlation.py` has no dependency on either upstream API and is the
piece worth understanding in detail, since it's the concrete value
federation adds beyond two separate dashboards.

### Step 1: build a lookup from Model 1's camera list

```python
def build_camera_context_map(cameras: list[dict]) -> dict:
```

Keys the lookup by each camera's **UUID `id`** — not the human-readable
`camera_identifier`. This matters: Model 2's `AnprEvent.camera_id` is a
foreign key into Model 1's `cameras.id` (the UUID), so joining on
anything else would silently produce zero matches. (This was a real bug
caught and fixed during development — worth knowing if you ever touch
this function.)

### Step 2: join each event against that context

```python
def correlate_events(raw_events: list[dict], camera_context: dict) -> list[dict]:
```

For each Model 2 event, looks up its camera's `department` and
`health_status` from Model 1. An event referencing a camera Model 1
doesn't recognize still passes through — marked `department: None`,
`healthStatus: "unknown"` — rather than being silently dropped.
Federation should degrade gracefully on missing context, not lose data.

### Step 3: compute a reliability rating

```python
def compute_reliability(health_status: str) -> str:
```

| Model 1's camera health | Reliability assigned |
|---|---|
| Operational | `high` |
| Maintenance Required | `medium` |
| Defective | `low` |
| Unknown / unrecognized camera | `medium` — **not** `high` |

That last row is deliberate: having *no* health data shouldn't be treated
as confidently positive. An earlier version of this logic defaulted
unrecognized cameras to `high`, which was wrong and has since been fixed.

### Worked example

```
Model 2 raw event:        camera_id=<uuid-A>, plate_text="GJ05CD5678"
Model 1 camera <uuid-A>:  department="Police", health_status="Defective"
                                    ↓
Model 3 correlated output: plateNumber="GJ05CD5678", department="Police",
                            cameraHealthStatus="inactive", reliability="low"
```

An ANPR hit from a camera Model 1 already knows is defective gets flagged
low-confidence automatically — no manual review step required. This is
the answer to "why federate instead of just looking at two dashboards."

### Step 4: the analytics summary

```python
def build_analytics_summary(correlated_events: list[dict]) -> dict:
```

Produces total event count, a low-reliability count, a department
breakdown, and a plain-language headline sentence — same shape as Model
1's own gap-analysis summary, for a consistent feel across all three
models' demos.

### Testing this in isolation

No server, no upstream systems needed:

```bash
.testing/bin/python -c "
from correlation import build_camera_context_map, correlate_events

cameras = [{'id': '11111111-1111-1111-1111-111111111111', 'camera_identifier': 'CAM-001', 'department': 'Police', 'health_status': 'Defective'}]
events = [{'id': 'E1', 'plate_text': 'GJ01AB1234', 'camera_id': '11111111-1111-1111-1111-111111111111', 'detected_at': '2026-01-01T00:00:00Z'}]
print(correlate_events(events, build_camera_context_map(cameras)))
"
```

---

## Graceful degradation

If Model 2 is unreachable, `ViewerAdapter.get_events()` returns
`AdapterResult(ok=False, error="Model 2 unreachable: ...")`. The
connector status endpoint honestly reports `status: "error"` for that
adapter, and `/federated/events` still returns whatever it can (an empty
list, since there are no events to correlate) rather than crashing the
whole request. **This means Model 3 is fully testable against Model 1
alone**, before Model 2 even exists or is running.

---

## Endpoints

```
GET /health
GET /api/v1/adapters/status
    Live connectivity check — actually calls both Model 1 and Model 2
    right now, not a cached assumption.

GET /api/v1/federated/events
    Query params: department, reliability (high/medium/low), search
    Returns Model 2's events, enriched with Model 1's camera context and
    a computed reliability rating each.

GET /api/v1/federated/analytics
    The summary report: totals, low-reliability count, department
    breakdown, headline sentence.
```

---

## Project layout

```
main.py                    FastAPI app, endpoints, startup config check
adapters.py                 VMSAdapter interface + RegistryAdapter + ViewerAdapter
model1_auth.py                Service-account login, token caching, clear error messages
correlation.py                 The join/reliability/summary logic — no external deps
test_model1_connection.py       Standalone script to debug the Model 1 login independently
.env.example                     Config template
```

---

## Known limitations (stated honestly)

- **No background sync or caching** — every request to
  `/federated/events` or `/adapters/status` calls both upstream systems
  fresh, in real time. Fine at hackathon scale; a production version
  would want a scheduled sync job so a slow upstream doesn't slow down
  every request.
- **In-memory state only** — `_adapter_sync_state` in `main.py` and the
  cached Model 1 token in `model1_auth.py` both reset on restart and
  wouldn't survive across multiple worker processes in a real multi-process
  deployment. Not an issue for a single-process demo.
- **No retry/backoff on transient upstream failures** beyond the single
  401-triggered token refresh — a one-off network blip on Model 1 or
  Model 2 surfaces as an error immediately rather than being retried.
