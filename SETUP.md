# Model 1 — CCTV Registry & GIS Foundation: Setup Guide

This covers everything needed to get both the backend (FastAPI) and frontend
(React/Vite) running locally. Read the **Known Gotchas** section before you
start — several of these cost real time during initial setup and are easy to
hit again on a fresh machine.

---

## Prerequisites

- **Python 3.12** (not 3.13/3.14 — see Gotchas)
- **Node.js 20.19+ or 22.12+** (required by Vite 7)
- **PostgreSQL** with **PostGIS** available
- **Git**

---

## Backend Setup

```bash
cd backend

# Create a venv with Python 3.12 specifically — not `python3`, which may
# point to a newer, incompatible version on your system.
python3.12 -m venv .venv
source .venv/bin/activate        # bash/zsh
# source .venv/bin/activate.fish # fish shell

pip install --upgrade pip
pip install -r requirements.txt
```

### Environment variables

Copy the template and fill in real values:
```bash
cp .env.example .env
```

Required variables:
```
DATABASE_URL=postgresql+psycopg://<user>:<password>@localhost:5432/cctv_registry
SECRET_KEY=<any-random-string-for-local-dev>
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
VENDOR_API_KEYS=<key>:<Department>,<key2>:<Department2>   # optional, only for vendor webhook testing
```

**Never commit your real `.env` file.** Only `.env.example` (with placeholder
values) belongs in the repo.

### Database setup

PostGIS must be installed system-wide, and Postgres must already be running
with the database created before the app will start.

```bash
# Install PostGIS via your OS package manager if not already present, e.g.:
#   Arch:          sudo pacman -S postgis
#   Debian/Ubuntu: sudo apt install postgis

# If Postgres was never initialized on this machine:
sudo -iu postgres initdb --locale=C.UTF-8 --encoding=UTF8 -D /var/lib/postgres/data   # Arch path
sudo systemctl enable --now postgresql

# Create the database (name/user/password must match your .env):
sudo -iu postgres createdb cctv_registry
sudo -iu postgres psql -c "ALTER USER postgres PASSWORD '<your-password>';"
```

### Run the server

```bash
uvicorn main:app --reload
```

On success you should see:
```
INFO:cctv_registry:Database ready: PostGIS extension confirmed, tables ensured.
INFO:     Application startup complete.
```

This auto-creates the PostGIS extension and all tables on first run
(`init_postgis()` + `create_all_tables()` in `main.py`'s startup lifecycle).

### First user

The very first `POST /api/v1/auth/register` call becomes an Admin account —
this only works once, on an empty `users` table. Do this via `/docs`
(`http://localhost:8000/docs`) before anything else:

1. Go to `POST /api/v1/auth/register`, "Try it out"
2. Submit an email/password/department
3. All subsequent registrations require an authenticated Admin calling
   `/api/v1/auth/register-as-admin` instead

### Verify it's working

- `http://localhost:8000/health` → `{"status": "ok"}`
- `http://localhost:8000/docs` → interactive API docs; use "Authorize" (enter
  your email as `username` + your password) to test authenticated endpoints
  directly from the browser

---

## Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Opens at `http://localhost:5173`.

### Environment variables

Create `.env` in `frontend/` if the backend isn't on the default:
```
VITE_API_BASE=http://localhost:8000/api/v1
```

### Connecting to the real backend

In `src/api/registry.js`, confirm:
```js
const USE_MOCK = false
```
With this set to `true`, the app runs entirely on generated mock data with
no backend required — useful for isolated frontend work.

### Test login

`src/api/auth.js` and the login call in `App.jsx` currently use a hardcoded
test account. Update the email/password there to match whatever account you
registered above, or wire up a real login form before demo day (currently a
placeholder — see Known Gaps below).

---

## Known Gotchas (things that will likely bite you on a fresh machine)

| Symptom | Cause | Fix |
|---|---|---|
| `pydantic-core` fails to build during `pip install` | Python 3.13/3.14 lacks prebuilt wheels, forces a source compile that fails | Use Python 3.12 for the venv |
| `ModuleNotFoundError: No module named 'psycopg'` | `requirements.txt` doesn't pin a Postgres driver | `pip install "psycopg[binary]"` (matches `.env`'s `postgresql+psycopg://` scheme) |
| `AttributeError: module 'bcrypt' has no attribute '__about__'` / password hashing crashes | `passlib==1.7.4` is incompatible with newer `bcrypt` releases | `pip install "bcrypt==4.0.1"` |
| `extension "postgis" is not available` | PostGIS package not installed system-wide | Install via OS package manager (see Database Setup) |
| `invalid input value for enum health_status_enum: "Maintenance Required"` (or similar for other enums) | SQLAlchemy `Enum()` columns default to storing Python member **names**, not `.value` strings, unless told otherwise | Already fixed in current `models.py` via `values_callable=lambda x: [e.value for e in x]` on every enum column — if you see this error, you're on a stale copy of `models.py` |
| Postgres service fails to start, log says data directory "is missing or empty" | Postgres was never initialized on this machine | Run the `initdb` command in Database Setup |
| `fish: Unknown command: pip` / venv seems inactive | `source .venv/bin/activate` doesn't work correctly in fish shell | Use `source .venv/bin/activate.fish` instead, or call `.venv/bin/python -m pip ...` directly |
| CORS errors in browser console when backend isn't running | Misleading — "CORS request did not succeed, Status: (null)" usually means the backend is unreachable, not a CORS config problem | Confirm `uvicorn` is actually running and reachable at `/health` first |

---

## Known Gaps / Not Yet Built

- No real login UI — frontend currently auto-logs in with a hardcoded test
  account on mount
- Camera edit/delete exist as backend endpoints (`PATCH`/`DELETE
  /cameras/{id}`) but have no frontend UI yet
- Vendor API-key onboarding webhook (`POST /cameras/register-vendor`) is
  untested end-to-end
- Only tested so far with a single `Police`/`viewer` test account —
  cross-department and Admin-role behavior should get at least one smoke
  test before the live demo

---

## API Reference

Full endpoint documentation, request/response schemas, and a live
"try it out" console: `http://localhost:8000/docs` (Swagger UI) or
`http://localhost:8000/redoc` (ReDoc) whenever the backend is running.
