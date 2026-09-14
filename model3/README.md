# Model 3 — VMS Federation & Middleware

## What this is, conceptually

Model 3 owns **no data of its own**. It's a stateless middleware layer whose entire job is to call Model 1's and Model 2's real, live APIs, join the results together, and expose one unified view. Every response reflects the actual current state of both upstream systems at request time, not a cached or pre-synced copy.

This implements the "VMS Federation & Middleware" model from the problem statement: an integration layer that lets independently-owned systems (Model 1's registry, Model 2's viewer) be queried and correlated through a single interface, without either system needing to know Model 3 exists.

## The adapter pattern

Core idea, in `adapters.py`:

```python
class VMSAdapter(ABC):
    @abstractmethod
    async def get_cameras(self) -> AdapterResult: ...
    @abstractmethod
    async def get_events(self) -> AdapterResult: ...
```

Every federated system gets one adapter class implementing this interface:
- **`RegistryAdapter`** — talks to Model 1, implements `get_cameras()` (returns camera metadata), no-op `get_events()`
- **`ViewerAdapter`** — talks to Model 2, implements `get_events()` (returns ANPR detections), no-op `get_cameras()`

Adding a fourth system later means writing one new class with these same two methods — nothing in `main.py`'s endpoints or `correlation.py`'s join logic needs to change.

**`AdapterResult`** forces every call site to treat "this adapter failed" as a distinct case:
```python
class AdapterResult:
    def __init__(self, ok: bool, data=None, error: str | None = None):
        self.ok = ok
        self.data = data if data is not None else []
        self.error = error
```

## Authentication

**The service account:** Model 3 logs into Model 1 using its own dedicated account (not a real person), via the standard login flow. Credentials live in `.env`:
```
MODEL1_SERVICE_EMAIL=model3-service@example.com
MODEL1_SERVICE_PASSWORD=<whatever you set>
```
This account must already exist in Model 1's database — Model 3 can only log into it, not create it.

The resulting JWT is cached in memory (`model1_auth.py`) for ~30 minutes (`MODEL1_TOKEN_CACHE_SECONDS`), refreshed on expiry or a 401.

**Why no second login for Model 2:** Model 2 validates Model 1's JWTs directly (same `SECRET_KEY`, same `users` table). So the same cached token authenticates to both — no separate `model2_auth.py`.

**Clear failure messages** (`Model1AuthError`), instead of raw exceptions:

| Situation | Message |
|---|---|
| Model 1 not running / wrong URL | "Could not connect to Model 1 at `<url>` — is it running?" |
| Model 1 slow/stuck | "did not respond within the timeout" |
| Wrong credentials / account doesn't exist | "Model 1 rejected login for `<email>` — either this account doesn't exist yet... or the password doesn't match" |
| Malformed response | "response didn't contain an access_token as expected" |

**Startup config check:** `main.py` calls `config_warnings()` at import time — catches missing credentials or a still-placeholder email (`user@example.com`) and logs it loudly before the app even starts serving requests.

## The correlation logic — the actual point of this model

`correlation.py`, no dependency on either upstream API:

**Step 1 — build a lookup from Model 1's cameras**, keyed by UUID `id` (not `camera_identifier` — Model 2's `AnprEvent.camera_id` is a foreign key to the UUID; this was a real bug caught and fixed during development).

**Step 2 — join each event** against that lookup. An event referencing an unrecognized camera still passes through (`department: None`, `healthStatus: "unknown"`) rather than being dropped.

**Step 3 — compute reliability:**

| Model 1 camera health | Reliability |
|---|---|
| Operational | `high` |
| Maintenance Required | `medium` |
| Defective | `low` |
| Unknown / unrecognized | `medium` (not `high` — no evidence shouldn't imply confidence) |

**Worked example:**
```
Model 2 event:      camera_id=<uuid-A>, plate_text="GJ05CD5678"
Model 1 camera A:   department="Police", health_status="Defective"
                              ↓
Model 3 output:     department="Police", cameraHealthStatus="inactive", reliability="low"
```

**Step 4 — analytics summary:** total events, low-reliability count, department breakdown, plain-language headline — same shape as Model 1's own gap-analysis summary.

## Graceful degradation

If Model 2 is unreachable, `ViewerAdapter` returns `ok=False` with a clear error. The connector status endpoint honestly reports `"error"` for that adapter; `/federated/events` still returns what it can rather than crashing. Model 3 is fully testable against Model 1 alone.

## Endpoints

```
GET /health
GET /api/v1/adapters/status        — live check, not cached
GET /api/v1/federated/events       — ?department, ?reliability, ?search
GET /api/v1/federated/analytics
```

## Setup essentials

1. `python3.12 -m venv .testing`, `pip install -r requirements.txt`, `cp .env.example .env`
2. Register the service account in Model 1 (`POST /api/v1/auth/register-as-admin`, `department: "Admin"` for unrestricted visibility)
3. Verify with `test_model1_connection.py` before starting the full app
4. `uvicorn main:app --reload --port 8002`

## Known limitations

- No background sync/caching — every request hits both upstream systems fresh
- In-memory state only (adapter sync state, cached token) — resets on restart, doesn't survive multi-process deployment
- No retry/backoff beyond the single 401-triggered token refresh
