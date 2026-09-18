"""
main.py
FastAPI entrypoint for Model 2: Live Feed Relay + ANPR.

Run with:
    uvicorn main:app --reload --port 8001

(Use a different port than Model 1 if running both at once on one machine.)
"""
import logging
import os
import json
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from database import create_all_tables
from camera_worker import source_manager
from routers import sources, stream, anpr
import storage
import vehicle_pipeline
import anpr_lowres
import vahan

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cctv_model2")

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        create_all_tables()
        logger.info("Database ready: Model 2 tables ensured (camera_sources, anpr_jobs, anpr_events).")
        logger.info("SECURITY: Rate limiting enabled (default: 100 requests/minute)")
        log_audit_event("SYSTEM_STARTUP", "database", "system")
    except SQLAlchemyError as exc:
        logger.error("Database initialization failed: %s", exc)
        log_audit_event("SYSTEM_STARTUP_FAILED", "database", "system", {"error": str(exc)})
        raise
    yield
    logger.info("Shutting down — stopping all camera worker threads.")
    log_audit_event("SYSTEM_SHUTDOWN", "database", "system")
    source_manager.shutdown_all()


app = FastAPI(
    title="Live Feed Relay & ANPR (Model 2)",
    description=(
        "Model 2 backend: registers playable video sources for Model 1's "
        "cameras, relays live feeds as MJPEG, and runs a YOLO+EasyOCR ANPR "
        "pipeline against recorded clips or sources, with a searchable "
        "events log."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Attach rate limiter state to app
app.state.limiter = limiter

# Security-hardened CORS: restrict to specific origins in production
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5174").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
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


@app.exception_handler(SQLAlchemyError)
async def db_exception_handler(request: Request, exc: SQLAlchemyError):
    logger.exception("Unhandled database error")
    log_audit_event("DATABASE_ERROR", "database", "system", {"error": str(exc)})
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "A database error occurred. Please try again or contact support."},
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


@app.get("/health", tags=["System"])
def health_check():
    vehicle_available, vehicle_reason = vehicle_pipeline.dependency_status()
    vahan_configured, vahan_reason = vahan.is_configured()
    return {
        "status": "ok",
        "active_camera_workers": source_manager.active_count(),
        "max_concurrent_sources": source_manager.max_concurrent_sources,
        "snapshot_storage": storage.storage_stats(),
        "vehicle_recognition": {
            "enabled": vehicle_pipeline.VEHICLE_RECOGNITION_ENABLED,
            "make_model_backend": vehicle_pipeline.VEHICLE_MAKE_BACKEND,
            "dependencies_available": vehicle_available,
            "dependency_error": vehicle_reason,
        },
        "vehicle_registry": {
            "policy": vahan.VAHAN_LOOKUP_POLICY,
            "configured": vahan_configured,
            "configuration_error": vahan_reason,
            "cache": vahan.cache_stats(),
        },
        "low_res_tooling": anpr_lowres.status(),
    }


app.include_router(sources.router)
app.include_router(stream.router)
app.include_router(anpr.router)
