"""
model1_auth.py
Model 3 doesn't have its own end users for calling Model 1 — it authenticates
as a single service account, the same way any backend-to-backend integration
would. The token is cached in memory and only refreshed periodically or on
a 401, rather than logging in on every single request.
"""
import os
import time
import httpx

MODEL1_REGISTRY_BASE = os.getenv("MODEL1_REGISTRY_BASE", "http://localhost:8000/api/v1")
MODEL1_SERVICE_EMAIL = os.getenv("MODEL1_SERVICE_EMAIL", "")
MODEL1_SERVICE_PASSWORD = os.getenv("MODEL1_SERVICE_PASSWORD", "")
TOKEN_CACHE_SECONDS = int(os.getenv("MODEL1_TOKEN_CACHE_SECONDS", "1800"))

_cached_token: str | None = None
_cached_at: float = 0.0


class Model1AuthError(Exception):
    """Raised with a specific, actionable message — distinguishes 'Model 1
    is unreachable' from 'the service account credentials are wrong' from
    'Model 1 returned something unexpected', instead of surfacing a raw
    httpx exception that's hard to act on."""
    pass


def config_warnings() -> list[str]:
    """Returns a list of human-readable problems with the current config,
    checkable BEFORE making any network call — e.g. call this at app
    startup so a misconfigured .env fails loudly and immediately instead
    of only surfacing on the first real request."""
    warnings = []
    if not MODEL1_SERVICE_EMAIL:
        warnings.append("MODEL1_SERVICE_EMAIL is not set in .env")
    if not MODEL1_SERVICE_PASSWORD:
        warnings.append("MODEL1_SERVICE_PASSWORD is not set in .env")
    if MODEL1_SERVICE_EMAIL == "user@example.com":
        warnings.append(
            "MODEL1_SERVICE_EMAIL is still the template placeholder "
            "(user@example.com) — this is very unlikely to be a real "
            "registered account. Update .env with a real service account."
        )
    return warnings


async def get_model1_token() -> str:
    """Returns a cached token if still fresh, otherwise logs in again.
    Raises Model1AuthError with a specific, actionable message on failure
    rather than letting a raw httpx exception bubble up."""
    global _cached_token, _cached_at

    if _cached_token and (time.time() - _cached_at) < TOKEN_CACHE_SECONDS:
        return _cached_token

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            res = await client.post(
                f"{MODEL1_REGISTRY_BASE}/auth/login",
                data={"username": MODEL1_SERVICE_EMAIL, "password": MODEL1_SERVICE_PASSWORD},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.ConnectError as exc:
        raise Model1AuthError(
            f"Could not connect to Model 1 at {MODEL1_REGISTRY_BASE} — "
            f"is it running? (underlying error: {exc})"
        ) from exc
    except httpx.TimeoutException as exc:
        raise Model1AuthError(
            f"Model 1 at {MODEL1_REGISTRY_BASE} did not respond within the timeout. "
            f"It may be running but overloaded/stuck."
        ) from exc

    if res.status_code == 401:
        raise Model1AuthError(
            f"Model 1 rejected login for '{MODEL1_SERVICE_EMAIL}' — either this account "
            f"doesn't exist yet (register it via Model 1's /docs using "
            f"POST /api/v1/auth/register-as-admin), or MODEL1_SERVICE_PASSWORD in "
            f"Model 3's .env doesn't match what was actually set when the account "
            f"was created."
        )
    if res.status_code != 200:
        raise Model1AuthError(
            f"Model 1 login returned unexpected status {res.status_code}: {res.text[:300]}"
        )

    try:
        token = res.json()["access_token"]
    except (KeyError, ValueError) as exc:
        raise Model1AuthError(
            f"Model 1 login succeeded but the response didn't contain an access_token "
            f"as expected. Response body: {res.text[:300]}"
        ) from exc

    _cached_token = token
    _cached_at = time.time()
    return token


def invalidate_model1_token():
    """Call this if a request comes back 401 despite having a cached token —
    forces the next call to re-authenticate rather than retrying the same
    stale token forever."""
    global _cached_token
    _cached_token = None
