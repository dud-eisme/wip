# Live Feed Relay & ANPR — Frontend (Model 2)

Rebuilt to match the real backend your teammate delivered (FastAPI +
OpenCV feed relay + YOLO/EasyOCR ANPR), not the earlier placeholder API.

## Run it
```
npm install
npm run dev
```
Opens at http://localhost:5174. Runs entirely on mock data by default,
**including login** — no backend needs to be running to preview the UI.

## Real backend integration — what you need to know

**Model 2 has no login of its own.** It validates Model 1's JWT directly
(same `SECRET_KEY`, same `users` table). So this frontend logs in against
**Model 1's** `/api/v1/auth/login` and reuses that one token for every
Model 2 API call too — see `src/api/auth.js`.

**The real flow, matching the actual backend:**
1. Log in (against Model 1)
2. Register a source — links a Model 1 camera to a playable video URL
   (RTSP/HTTP/local file)
3. Start that source's worker — spins up the background frame-capture thread
4. View the live feed — an `<img>` tag pointed at the MJPEG stream
5. Optionally run an ANPR job against that source
6. Search/flag detected plates in the Events tab

## Important: the `<img>` tag auth problem — now fixed on both sides

Model 2's stream endpoint originally required a JWT in the Authorization
header, which a plain `<img src="...">` tag cannot send. **This has been
patched on the backend** (see `PATCH_README.md` in the backend patch
bundle — `get_current_user_flexible` accepts the token via `?token=...` as
well). This frontend's `buildStreamUrl()` in `src/api/viewer.js` builds
URLs using that query param. If your teammate hasn't applied the patch yet,
video tiles will show a 401 the moment you click Start.

## Connecting to the real backend

Two files, same seam pattern as Models 1 and 3:

1. **`src/api/viewer.js`**: set `USE_MOCK = false`, set `VITE_API_BASE` to
   Model 2's real URL (e.g. `http://localhost:8001/api/v2`), and
   `VITE_REGISTRY_API_BASE` to Model 1's (`http://localhost:8000/api/v1`).
2. **`src/api/auth.js`**: set `VITE_MODEL1_API_BASE` if Model 1 isn't on
   the default port.

Once `USE_MOCK = false`, the app will actually try to log in against Model
1 on load — make sure a real test account exists there.

## What's here
- **Video Wall**: one tile per registered source, each with Start/Stop
  controls for its capture worker and a "Run ANPR" button that polls job
  status (queued → running → completed/failed) every 2s until it finishes,
  showing a color-coded status chip
- **Register Source**: pulls the live camera list from Model 1's registry,
  lets you link one to a video URL
- **Events**: real ANPR detections (plate, camera, confidence, timestamp),
  searchable by plate, with flag + optional note

## Backend companion pieces (not in this folder — see backend/)
- The `<img>`-tag auth fix (`auth.py` / `routers/stream.py` patch) —
  required for the video wall to work at all; see `PATCH_README.md`
- `cleanup_snapshots.py` — standalone snapshot retention script, run
  manually or via cron, never auto-runs

## Known simplifications (flagged honestly, not hidden)
- Camera names in the Events table come from a one-time fetch of Model 1's
  registry on load — if a camera's name changes in Model 1 after that,
  this won't pick it up without a refresh.
- No UI yet for editing/deleting a registered source (backend supports
  `PATCH`/`DELETE /sources/{id}`, not wired up here).
