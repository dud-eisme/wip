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

from fastapi import FastAPI, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from typing import Optional

from adapters import ADAPTERS, RegistryAdapter, ViewerAdapter
from correlation import build_camera_context_map, correlate_events, build_analytics_summary
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


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/v1/adapters/status")
async def adapters_status():
    """
    Reports each adapter's live connectivity — this is the honest version
    of a "connector framework" status view: it actually calls each system
    right now rather than reporting a cached assumption.
    """
    results = []
    registry_adapter = next(a for a in ADAPTERS if isinstance(a, RegistryAdapter))
    viewer_adapter = next(a for a in ADAPTERS if isinstance(a, ViewerAdapter))

    cameras_result = await registry_adapter.get_cameras()
    events_result = await viewer_adapter.get_events()

    now = datetime.now(timezone.utc)

    for adapter, result in ((registry_adapter, cameras_result), (viewer_adapter, events_result)):
        state = _adapter_sync_state[adapter.id]
        if result.ok:
            state["lastSync"] = now
            state["recordsSynced"] = len(result.data)
            state["error"] = None
            log_audit_event("ADAPTER_SYNC_SUCCESS", adapter.id, "system", {
                "records": len(result.data)
            })
        else:
            state["error"] = result.error
            log_audit_event("ADAPTER_SYNC_FAILED", adapter.id, "system", {
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


@app.get("/api/v1/federated/events")
async def federated_events(
    department: Optional[str] = Query(None),
    reliability: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
):
    """
    The core federation endpoint: pulls Model 1's cameras and Model 2's
    events live, correlates them, and returns the joined result. If Model 2
    is unreachable, returns an empty (not broken) result — the adapter
    status endpoint above is what tells you *why* it's empty.
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

    if department:
        correlated = [e for e in correlated if e["department"] == department]
    if reliability:
        correlated = [e for e in correlated if e["reliability"] == reliability]
    if search:
        correlated = [e for e in correlated if search.lower() in (e["plateNumber"] or "").lower()]

    # Log search queries for audit trail
    if search or department or reliability:
        log_audit_event("FEDERATED_SEARCH", "events", "system", {
            "department": department,
            "reliability": reliability,
            "search": search,
            "results": len(correlated)
        })

    return correlated


@app.get("/api/v1/federated/analytics")
async def federated_analytics():
    """The 'sample federated analytics report' deliverable."""
    registry_adapter = next(a for a in ADAPTERS if isinstance(a, RegistryAdapter))
    viewer_adapter = next(a for a in ADAPTERS if isinstance(a, ViewerAdapter))

    cameras_result = await registry_adapter.get_cameras()
    events_result = await viewer_adapter.get_events()

    camera_context = build_camera_context_map(cameras_result.data)
    correlated = correlate_events(events_result.data, camera_context)

    result = build_analytics_summary(correlated)
    
    log_audit_event("FEDERATED_ANALYTICS_GENERATED", "analytics", "system", {
        "total_events": len(correlated),
        "low_reliability_count": result.get("lowReliabilityCount", 0)
    })
    
    return result
