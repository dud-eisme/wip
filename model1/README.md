# Model 1 — CCTV Registry & GIS Foundation

## Run it
```
cd frontend
npm install
npm run dev
```
Opens at http://localhost:5173. Requires the backend running (see
`backend/README.md` or the SETUP.md at the project root) — this frontend
has a **real login screen**, not a mock/auto-login, so nothing loads until
you sign in with a real account.

## Logging in
On load you'll see a login form (`LoginScreen.jsx`), calling the
backend's real `POST /api/v1/auth/login`. You need an account that
already exists in the backend's database — see the root SETUP.md's
"First user" section if you don't have one yet, or ask whoever set up the
backend.

## What's here
- **GIS map** (Leaflet + marker clustering) with status-colored pins,
  live search, and filters (department, camera type, health status)
- **Camera list** synced with the map, click a row to fly the map to it
- **Edit / delete** — pencil and trash icons on each row, edit opens a
  pre-filled form (PATCH), delete confirms then removes (DELETE)
- **Onboarding** — manual entry modal and bulk CSV/Excel upload modal
  (with a per-row success/failure breakdown, not all-or-nothing)
- **Gap analysis panel** — dead-zone grid cell count, ageing/maintenance
  count, plus the backend's real plain-language headline and recommended
  actions
- **CSV export** of the current filtered camera list

## Schema notes
Every camera object in the frontend carries both:
- `id` — the human-readable `camera_identifier` (e.g. `CAM-0001`), used
  for display
- `dbId` — the real UUID primary key, required for the edit/delete PATCH
  and DELETE calls (the backend's routes are keyed by UUID, not the
  human-readable identifier)

## Connecting to the real API
Everything goes through **`src/api/registry.js`** — the seam between
this frontend and the backend. `src/api/auth.js` handles login
separately. Both default to `http://localhost:8000/api/v1` — override
with `VITE_API_BASE` if your backend runs elsewhere.

## Known limitations
- No password reset / "forgot password" flow
- Editing a camera's location (lat/lng) isn't exposed in the edit modal
  yet, even though the backend supports it — only non-geometry fields are
  editable from the UI right now
