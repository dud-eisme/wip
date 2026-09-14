# Patch: fix video streaming for <img>-tag frontends

## The problem
`GET /api/v2/sources/{id}/stream` and `.../snapshot` require a JWT in the
`Authorization` header. A plain `<img src="...">` tag — the normal way to
display an MJPEG stream in a browser, and what the frontend uses — **cannot
send custom headers at all**. As originally written, no `<img>` tag can
ever successfully load these two endpoints; every request gets a 401.

## The fix
Two files changed: `auth.py` and `routers/stream.py`.

`auth.py` gets a new dependency, `get_current_user_flexible`, alongside the
existing `get_current_user` (which is untouched and still used everywhere
else). The new one accepts the token from **either** the Authorization
header **or** a `?token=...` query parameter — whichever is present. If
neither is present, it still correctly rejects with 401.

`routers/stream.py`'s two endpoints (`/stream` and `/snapshot`) now use
`get_current_user_flexible` instead of the strict `get_current_user`. Every
other endpoint in the service is unchanged and still requires the header —
this fix is scoped narrowly to just the two endpoints that actually need to
be loadable from an `<img>` tag.

## What the frontend does differently now
Instead of:
```
<img src="http://localhost:8001/api/v2/sources/{id}/stream">
```
it builds:
```
<img src="http://localhost:8001/api/v2/sources/{id}/stream?token=<jwt>">
```

## Verified before handing back
- Full app still imports cleanly with the patch applied; all 12 routes
  still register correctly (confirmed via the OpenAPI schema, not just
  reading the code).
- Tested `get_current_user_flexible` directly against three cases: token
  in header only, token in query param only (the actual `<img>`-tag
  scenario), and neither present — all three behave correctly, including
  still rejecting unauthenticated requests with 401.

## Nothing else changed
No other files, no schema changes, no new dependencies. Safe to drop these
two files in over the existing ones.
