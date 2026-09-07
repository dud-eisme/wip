# VMS Federation & Middleware — Frontend (Model 3)

## Run it
```
npm install
npm run dev
```
Opens at http://localhost:5175 (Models 1/2/3 all run on different ports —
5173/5174/5175 — so you can demo all three simultaneously).

Runs entirely on mock data by default.

## What's here
- **Connector status panel** — shows each adapter (Model 1's registry, Model
  2's viewer) and whether it's currently reachable, plus how many records
  it last synced. This is the "extensible connector framework" made visible.
- **Federated analytics summary** — a headline sentence plus key stats
  (total correlated events, how many are flagged low-reliability due to
  camera health issues, breakdown by department). This satisfies the
  "sample federated analytics report" deliverable.
- **Correlated events table** — Model 2's ANPR events, each enriched with
  Model 1's camera context (department, health status) and a computed
  reliability rating.

## The actual "federation" logic
Look at `src/data/mockFederatedEvents.js`'s `computeReliability()` function —
this is the concrete example of what "event correlation" means in this
model: an ANPR detection captured on a camera that Model 1's registry marks
as offline or needing maintenance is treated as lower-reliability than one
from a healthy camera. This is a real, demonstrable value-add of federating
the two systems rather than just a lookup join.

## Connecting to the real backend
Everything goes through **`src/api/federation.js`** — same seam pattern as
Models 1 and 2:

1. Set `USE_MOCK = false`
2. Set `VITE_API_BASE` to the real Model 3 backend URL

Unlike Models 1/2's `registry.js`/`viewer.js`, Model 3's backend doesn't own
its own source data — its whole job is calling Model 1's and Model 2's real
APIs, joining the results, and exposing one unified endpoint. See the
backend's adapter pattern (separate handoff) for how that join actually
happens server-side.

## Not yet built (next up)
- Department/reliability filter dropdowns on the events table (backend
  filters already support `department` and `reliability` in
  `getFederatedEvents()` — just needs dropdown UI, same pattern as Model 1's
  FilterPanel)
- Live/auto-refreshing connector status (currently fetched once on load)
