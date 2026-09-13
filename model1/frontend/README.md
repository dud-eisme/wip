# CCTV Registry & GIS Foundation — Frontend (Model 1)

## Run it
```
npm install
npm run dev
```
Opens at http://localhost:5173. Runs entirely on mock data — no backend needed to start building/demoing the UI.

## What's here
- **GIS map** (Leaflet) with status-colored markers (active / maintenance / offline) and popups showing full camera metadata
- **Filter sidebar**: department, camera type, status + live search
- **Gap analysis card**: offline / uncovered count + maintenance-flagged count
- **Camera list**: scrollable list synced with active filters
- **CSV export** of the currently filtered camera set

## Schema (matches what Person A's PostGIS backend should return)
See `src/data/mockCameras.js` — each camera object has:
`id, name, department, cameraType, ownership, connectivityStatus, healthStatus, storageDetails, streamEndpoint, lastUpdated, lat, lng`

`streamEndpoint` is included specifically so Model 2 (unified viewer) can consume this registry directly later — don't drop it from the real schema.

## Connecting to the real API
Everything goes through **`src/api/registry.js`** — that's the only file that needs to change once Person A's backend is live:
1. Set `USE_MOCK = false` in `src/api/registry.js`
2. Set `VITE_API_BASE` env var to the real API URL (or edit the default in the file)
3. Confirm the real `/api/cameras`, `/api/gap-analysis`, and `/api/cameras/bulk-import` responses match the mock shape above — if the backend uses different field names, adjust `applyFilters()` and the mock shape together, not the components.

No component (`MapView`, `FilterPanel`, `CameraList`, `GapAnalysisPanel`) needs to know or care whether data is mocked or real.

## Not yet built (next up)
- "Onboard camera" button is a placeholder — wire to bulk import (CSV upload) and manual entry form once the backend endpoint exists
- Role-based access control / auth gating (waiting on Person A's RBAC design)
- Layer toggles for department/type/status as map overlays (currently these are list filters only, not separate map layers)
