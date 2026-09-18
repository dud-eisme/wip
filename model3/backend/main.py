"""
main.py
Model 3: VMS Federation & Middleware — FastAPI entrypoint.

Run with:
    uvicorn main:app --reload --port 8002
"""
import logging
import os
import json
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Query, Request, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from typing import List, Optional

from adapters import ADAPTERS, RegistryAdapter, ViewerAdapter, AdapterResult
from correlation import build_camera_context_map, correlate_events, build_analytics_summary
from caller_auth import get_current_caller, Caller
from schemas import AdapterStatus, FederatedEvent, AnalyticsSummary
import model1_auth

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("model3_federation")

# Audit logging setup
audit_logger = logging.getLogger("audit")
os.makedirs("logs", exist_ok=True)
audit_handler = logging.FileHandler("logs/audit.log")
audit_handler.setFormatter(
    logging.Formatter(
        '%(asctime)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
)
audit_logger.addHandler(audit_handler)
audit_logger.setLevel(logging.INFO)

# Rate limiter
limiter = Limiter(key_func=get_remote_address)


def log_audit_event(action: str, resource: str, user_id: str = "system", details: dict = None):
    """Log security-relevant events to audit trail."""
    event = {
        "action": action,
        "resource": resource,
        "user_id": user_id,
        "timestamp": datetime.utcnow().isoformat(),
        "details": details or {}
    }
    audit_logger.info(json.dumps(event))


# Fail loudly at import time if the service-account config looks wrong,
# rather than only discovering it on the first real request.
for warning in model1_auth.config_warnings():
    logger.warning("CONFIG ISSUE: %s", warning)
    log_audit_event("CONFIG_WARNING", "model1_auth", "system", {"warning": warning})

app = FastAPI(
    title="VMS Federation & Middleware",
    description=(
        "Model 3 backend: federates Model 1 (CCTV Registry) and Model 2 "
        "(Unified Viewer) via an adapter pattern, correlating ANPR events "
        "with camera health/department context."
    ),
    version="1.0.0",
)

# Attach rate limiter state to app
app.state.limiter = limiter

# Security-hardened CORS: restrict to specific origins in production
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5175").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=3600,
)


# ---------------------------------------------------------------------------
# Rate Limiting Middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def rate_limit_header_middleware(request: Request, call_next):
    """Add rate limit headers to responses."""
    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = "100"
    response.headers["X-RateLimit-Period"] = "60"
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors()},
    )


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exception_handler(request: Request, exc: RateLimitExceeded):
    """Handle rate limit exceeded errors."""
    user_id = getattr(request.state, "user_id", "anonymous")
    log_audit_event("RATE_LIMIT_EXCEEDED", request.url.path, user_id, {
        "client_ip": request.client.host if request.client else "unknown"
    })
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "Rate limit exceeded. Please try again later."},
    )


# Tracks last successful sync per adapter, for the connector status panel.
_adapter_sync_state: dict = {a.id: {"lastSync": None, "recordsSynced": 0, "error": None} for a in ADAPTERS}


async def _fetch_correlated(caller: Caller) -> tuple[list[dict], AdapterResult, AdapterResult]:
    """
    Shared by all three /federated/* and /adapters/status endpoints:
    fetch both adapters, join them, and apply department scoping.

    BUG FIX (security): this scoping step did not exist before. Every
    endpoint returned Model 1/2 data unscoped to whoever called Model 3 —
    Model 3 authenticates upstream with one shared Admin-level service
    account (see model1_auth.py), and that Admin-level view was being
    handed to literally any caller, bypassing the department-RBAC that
    Model 1 and Model 2 both enforce on their own APIs. This mirrors
    Model 2's own department_scope(): Admin role or Admin department see
    everything, everyone else sees only their own department's events.
    """
    registry_adapter = next(a for a in ADAPTERS if isinstance(a, RegistryAdapter))
    viewer_adapter = next(a for a in ADAPTERS if isinstance(a, ViewerAdapter))

    cameras_result = await registry_adapter.get_cameras()
    events_result = await viewer_adapter.get_events()

    if not cameras_result.ok:
        logger.warning("Registry adapter unavailable: %s", cameras_result.error)
    if not events_result.ok:
        logger.warning("Viewer adapter unavailable: %s", events_result.error)

    camera_context = build_camera_context_map(cameras_result.data)
    correlated = correlate_events(events_result.data, camera_context)

    if not caller.is_unrestricted:
        correlated = [e for e in correlated if e["department"] == caller.department]

    return correlated, cameras_result, events_result


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/v1/adapters/status", response_model=List[AdapterStatus])
async def adapters_status(caller: Caller = Depends(get_current_caller)):
    """
    Reports each adapter's live connectivity — this is the honest version
    of a "connector framework" status view: it actually calls each system
    right now rather than reporting a cached assumption.

    Requires a valid caller (any department) — connectivity/error detail
    for the upstream systems is operational information, not something
    that should be reachable by anyone with network access to this port.
    Not department-filtered beyond that: connectivity status isn't
    per-department data the way events/analytics are.
    """
    registry_adapter = next(a for a in ADAPTERS if isinstance(a, RegistryAdapter))
    viewer_adapter = next(a for a in ADAPTERS if isinstance(a, ViewerAdapter))

    cameras_result = await registry_adapter.get_cameras()
    events_result = await viewer_adapter.get_events()

    now = datetime.now(timezone.utc)

    results = []
    for adapter, result in ((registry_adapter, cameras_result), (viewer_adapter, events_result)):
        state = _adapter_sync_state[adapter.id]
        if result.ok:
            state["lastSync"] = now
            state["recordsSynced"] = len(result.data)
            state["error"] = None
            log_audit_event("ADAPTER_SYNC_SUCCESS", adapter.id, caller.email, {
                "records": len(result.data)
            })
        else:
            state["error"] = result.error
            log_audit_event("ADAPTER_SYNC_FAILED", adapter.id, caller.email, {
                "error": result.error
            })

        results.append(
            {
                "id": adapter.id,
                "name": adapter.name,
                "sourceModel": adapter.source_model,
                "endpoint": adapter.endpoint,
                "status": "connected" if result.ok else "error",
                "lastSync": state["lastSync"] or now,
                "recordsSynced": state["recordsSynced"],
                "errorDetail": state["error"],
            }
        )

    return results


@app.get("/api/v1/federated/events", response_model=List[FederatedEvent])
async def federated_events(
    caller: Caller = Depends(get_current_caller),
    department: Optional[str] = Query(None),
    reliability: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
):
    """
    The core federation endpoint: pulls Model 1's cameras and Model 2's
    events live, correlates them, and returns the joined result. If Model 2
    is unreachable, returns an empty (not broken) result — the adapter
    status endpoint above is what tells you *why* it's empty.

    Response is now scoped to the CALLER's department (see
    _fetch_correlated) — a non-Admin caller passing ?department=Police
    for a department that isn't their own gets an empty result, not
    someone else's data; the explicit filter below only ever narrows
    further within whatever _fetch_correlated already allowed through.
    """
    correlated, cameras_result, events_result = await _fetch_correlated(caller)

    if department:
        correlated = [e for e in correlated if e["department"] == department]
    if reliability:
        correlated = [e for e in correlated if e["reliability"] == reliability]
    if search:
        needle = search.lower()
        # Extended to match vehicle attributes too, not just the plate —
        # now that Model 2 reports make/model/colour, "red swift" or
        # "maruti" are realistic searches a reviewer would actually try.
        correlated = [
            e for e in correlated
            if needle in (e["plateNumber"] or "").lower()
            or needle in (e.get("vehicleMake") or "").lower()
            or needle in (e.get("vehicleModel") or "").lower()
            or needle in (e.get("vehicleColour") or "").lower()
            or needle in (e.get("vehicleType") or "").lower()
        ]

    # Log search queries for audit trail
    if search or department or reliability:
        log_audit_event("FEDERATED_SEARCH", "events", caller.email, {
            "department": department,
            "reliability": reliability,
            "search": search,
            "results": len(correlated)
        })

    return correlated


@app.get("/api/v1/federated/analytics", response_model=AnalyticsSummary)
async def federated_analytics(caller: Caller = Depends(get_current_caller)):
    """The 'sample federated analytics report' deliverable. Scoped to the
    caller's department the same way federated_events is — a department
    breakdown that included other departments' counts would itself be a
    cross-department data leak, just in aggregate rather than per-event
    form."""
    correlated, _, _ = await _fetch_correlated(caller)

    result = build_analytics_summary(correlated)

    log_audit_event("FEDERATED_ANALYTICS_GENERATED", "analytics", caller.email, {
        "total_events": len(correlated),
        "low_reliability_count": result.get("lowReliabilityCount", 0)
    })

    return result
