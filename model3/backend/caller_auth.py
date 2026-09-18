"""
caller_auth.py
Authenticates the PERSON calling Model 3's own API — distinct from
model1_auth.py, which authenticates Model 3 ITSELF to Model 1 as a
service account.

Why this exists: /api/v1/federated/* had no authentication at all. Model 3
authenticates to Model 1/2 with one shared Admin-scoped service account,
then returned that Admin-level, all-departments data to WHOEVER called
Model 3 — no login required. That's a straightforward bypass of the
department-RBAC that Model 1 and Model 2 both enforce server-side: a
Transport-department user (or literally anyone with network access to
port 8002) could see Police events through Model 3 that they're blocked
from seeing directly through Model 1 or Model 2.

Why forward-to-/auth/me instead of decoding the JWT locally: Model 2
decodes the token itself (auth.py there) because it shares Model 1's
database and can look the user up by `sub`. Model 3 is deliberately
stateless — no database at all — so it has no `users` table to check
`is_active` against, and duplicating SECRET_KEY handling here would be a
second place that secret has to be kept in sync (Model 2 already reuses
Model 1's token instead of reinventing its own auth for exactly this
reason). Calling Model 1's own /auth/me with the CALLER's token — not the
service account's — gets identity verification and department/role in
one call, using the source of truth instead of a local copy of it. The
cost is one extra HTTP round-trip per request; that's a fair trade for
not maintaining a second auth implementation.

Result is NOT cached — an authorization check has to be current every
request. Compare model1_auth.py's SERVICE token, which is fine to cache
because it identifies Model 3 itself, not the human on the other end of
this request.
"""
import os
from typing import Optional

import httpx
from fastapi import HTTPException, Request, status

MODEL1_REGISTRY_BASE = os.getenv("MODEL1_REGISTRY_BASE", "http://localhost:8000/api/v1")
CALLER_AUTH_TIMEOUT = float(os.getenv("CALLER_AUTH_TIMEOUT_SECONDS", "5"))


class Caller:
    """The identity of whoever is calling Model 3's API, as reported by
    Model 1 — the same shape department_scope() in Model 2's auth.py
    reasons about, so filtering logic here mirrors what Model 1/2 already
    do rather than inventing a third convention."""

    def __init__(self, email: str, department: Optional[str], role: Optional[str]):
        self.email = email
        self.department = department
        self.role = role

    @property
    def is_unrestricted(self) -> bool:
        """Same rule as Model 2's department_scope(): Admin role OR
        Admin department sees everything unscoped."""
        return self.role == "admin" or self.department == "Admin"


async def get_current_caller(request: Request) -> Caller:
    """FastAPI dependency. Extracts the caller's own Bearer token from the
    incoming request and verifies it against Model 1 — never against the
    cached SERVICE token from model1_auth.py, which would defeat the
    whole point (that token proves "this is Model 3", not "this is you").

    Raises 401 if the header is missing/malformed, or if Model 1 rejects
    the token as invalid/expired. Raises 503 (not 401) if Model 1 itself
    is unreachable — that's Model 3's dependency failing, not the
    caller's credentials, and the two should not look the same to the
    person hitting this endpoint.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header (expected 'Bearer <token>').",
            headers={"WWW-Authenticate": "Bearer"},
        )
    caller_token = auth_header[len("Bearer "):].strip()
    if not caller_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        async with httpx.AsyncClient(timeout=CALLER_AUTH_TIMEOUT) as client:
            res = await client.get(
                f"{MODEL1_REGISTRY_BASE}/auth/me",
                headers={"Authorization": f"Bearer {caller_token}"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not verify caller identity — Model 1 is unreachable: {exc}",
        ) from exc

    if res.status_code == 401:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Model 1 rejected this token — it's invalid, expired, or for a deactivated account.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if res.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Model 1 returned an unexpected status ({res.status_code}) while verifying the caller.",
        )

    body = res.json()
    return Caller(
        email=body.get("email", "unknown"),
        department=body.get("department"),
        role=body.get("role"),
    )
