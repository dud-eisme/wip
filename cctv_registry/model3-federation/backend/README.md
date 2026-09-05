# VMS Federation & Middleware — Backend (Model 3)

## What this is
This backend doesn't own any data of its own. Its entire job is calling
Model 1's and Model 2's real APIs live, correlating the results, and
exposing one unified endpoint — the adapter pattern in `adapters.py` is the
actual architecture the problem statement describes.

## Setup
```bash
python3.12 -m venv .testing
source .testing/bin/activate        # bash/zsh
# source .testing/bin/activate.fish # fish shell

pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` — most values can stay as defaults if Model 1/2 run on their
usual ports, but you MUST set real values for:
```
MODEL1_SERVICE_EMAIL=...
MODEL1_SERVICE_PASSWORD=...
```

### One manual step: create a service account in Model 1

Model 3 authenticates to Model 1 as its own account — not a real end user —
same as any backend-to-backend integration would. This account needs to
already exist in Model 1's database before Model 3 will work:

1. Make sure Model 1's backend is running
2. Go to Model 1's `/docs`, use `POST /api/v1/auth/register-as-admin` (if
   Model 1 already has an Admin account) or coordinate with whoever set up
   Model 1's first user, since `/register` only works once
3. Register with the email/password you put in Model 3's `.env`
4. Pick whichever department makes sense (`Admin` gets unrestricted access
   to all departments' cameras, which is probably what you want for a
   federation service account)

## Run it
```bash
uvicorn main:app --reload --port 8002
```
Port `8002` — deliberately different from Model 1 (`8000`) and whatever
Model 2 ends up using (`8001`, per the frontend's default assumption).

Check `http://localhost:8002/health` → `{"status": "ok"}`, and
`http://localhost:8002/docs` for interactive testing.

## Endpoints
- `GET /api/v1/adapters/status` — live connectivity check against both
  Model 1 and Model 2, right now (not cached/assumed)
- `GET /api/v1/federated/events` — the correlated events list; supports
  `department`, `reliability`, `search` query params
- `GET /api/v1/federated/analytics` — the summary report

## Important: this depends on Model 2's backend existing

`ViewerAdapter.get_events()` calls `MODEL2_VIEWER_BASE/events`. If Model 2's
backend isn't running yet, this adapter will correctly report `status:
"error"` in the connector panel, and `/federated/events` will return an
empty list (not a crash) — the correlation logic is written to degrade
gracefully rather than fail outright when one upstream system is
unreachable. This means Model 3's frontend/backend are fully testable
right now using just Model 1, even before Model 2 exists — you'll just see
an empty events table and one connector showing "Error" until Model 2 comes
online.

## Testing the correlation logic in isolation

The core join/reliability logic (`correlation.py`) has no dependency on
either upstream API and can be tested standalone:
```bash
.testing/bin/python -c "
from correlation import build_camera_context_map, correlate_events

cameras = [{'camera_identifier': 'CAM-001', 'department': 'Police', 'health_status': 'Operational'}]
events = [{'id': 'E1', 'plateNumber': 'GJ01AB1234', 'cameraId': 'CAM-001', 'timestamp': '2026-01-01T00:00:00Z'}]
print(correlate_events(events, build_camera_context_map(cameras)))
"
```

## Known limitations
- Adapter status and federated data are computed fresh on every request —
  no background polling/caching. Fine for hackathon scale, would need a
  scheduled sync job for production.
- `_adapter_sync_state` in `main.py` is in-memory only — resets on server
  restart. Not an issue for a demo.
- Model 1's service-account token is cached in a module-level variable —
  fine for a single-process demo, wouldn't survive across multiple worker
  processes in a real deployment.
