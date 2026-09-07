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


async def get_model1_token() -> str:
    """Returns a cached token if still fresh, otherwise logs in again."""
    global _cached_token, _cached_at

    if _cached_token and (time.time() - _cached_at) < TOKEN_CACHE_SECONDS:
        return _cached_token

    async with httpx.AsyncClient(timeout=5) as client:
        res = await client.post(
            f"{MODEL1_REGISTRY_BASE}/auth/login",
            data={"username": MODEL1_SERVICE_EMAIL, "password": MODEL1_SERVICE_PASSWORD},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        res.raise_for_status()
        token = res.json()["access_token"]

    _cached_token = token
    _cached_at = time.time()
    return token


def invalidate_model1_token():
    """Call this if a request comes back 401 despite having a cached token —
    forces the next call to re-authenticate rather than retrying the same
    stale token forever."""
    global _cached_token
    _cached_token = None
