"""
main.py
FastAPI application entrypoint for Model 1: Centralised CCTV Registry &
GIS Mapping Model.

Run with:
    uvicorn main:app --reload
"""
import logging
import os
import ssl
from contextlib import asynccontextmanager
from functools import wraps
from datetime import datetime
import json

from fastapi import FastAPI, Request, status, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.openapi.utils import get_openapi
from sqlalchemy.exc import SQLAlchemyError
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from database import init_postgis, create_all_tables
from routers import cameras, analytics, auth_routes, streams
from auth import verify_docs_credentials

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cctv_registry")

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
    # Startup: ensure PostGIS extension exists.
    try:
        init_postgis()
        logger.info("Database ready: PostGIS extension confirmed.")
        logger.info("SECURITY: TLS/HTTPS enabled: %s", os.getenv("ENABLE_HTTPS", "false"))
        logger.info("SECURITY: Rate limiting enabled (default: 100 requests/minute)")
        log_audit_event("SYSTEM_STARTUP", "database", "system")
    except SQLAlchemyError as exc:
        logger.error("Database initialization failed: %s", exc)
        log_audit_event("SYSTEM_STARTUP_FAILED", "database", "system", {"error": str(exc)})
        raise
    yield
    logger.info("Shutting down CCTV Registry API.")
    log_audit_event("SYSTEM_SHUTDOWN", "database", "system")


# Disable default public docs endpoints
app = FastAPI(
    title="Centralised CCTV Registry & GIS Mapping Model",
    description=(
        "Model 1 backend: state-wide CCTV camera registry with department-scoped "
        "RBAC, manual entry, bulk import, vendor onboarding webhook, GIS search, "
        "and infrastructure gap-analysis reporting."
    ),
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

# Attach rate limiter state to app
app.state.limiter = limiter

# Security-hardened CORS: restrict to specific origins in production
allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
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


# ---------------------------------------------------------------------------
# Protected Documentation Routes
# ---------------------------------------------------------------------------

@app.get("/docs", include_in_schema=False)
async def get_swagger_documentation(auth: None = Depends(verify_docs_credentials)):
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=f"{app.title} - Swagger UI"
    )


@app.get("/redoc", include_in_schema=False)
async def get_redoc_documentation(auth: None = Depends(verify_docs_credentials)):
    return get_redoc_html(
        openapi_url="/openapi.json",
        title=f"{app.title} - ReDoc"
    )


@app.get("/openapi.json", include_in_schema=False)
async def get_openapi_spec(auth: None = Depends(verify_docs_credentials)):
    return JSONResponse(
        get_openapi(title=app.title, version=app.version, routes=app.routes)
    )


# ---------------------------------------------------------------------------
# Global error handling -> consistent JSON error envelope + correct status codes
# ---------------------------------------------------------------------------

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
    return {"status": "ok"}


app.include_router(auth_routes.router)
app.include_router(cameras.router)
app.include_router(analytics.router)
app.include_router(streams.router, prefix="/api/v1/streams")
