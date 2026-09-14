"""
main.py
FastAPI application entrypoint for Model 1: Centralised CCTV Registry &
GIS Mapping Model.

Run with:
    uvicorn main:app --reload
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.openapi.utils import get_openapi
from sqlalchemy.exc import SQLAlchemyError

from database import init_postgis, create_all_tables
from routers import cameras, analytics, auth_routes, streams
from auth import verify_docs_credentials

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cctv_registry")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure PostGIS extension exists.
    try:
        init_postgis()
        # REMOVED: create_all_tables()
        logger.info("Database ready: PostGIS extension confirmed.")
    except SQLAlchemyError as exc:
        logger.error("Database initialization failed: %s", exc)
        raise
    yield
    logger.info("Shutting down CCTV Registry API.")


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

# Adjust allow_origins for your actual frontend/GIS dashboard origin(s) in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "A database error occurred. Please try again or contact support."},
    )


@app.get("/health", tags=["System"])
def health_check():
    return {"status": "ok"}


app.include_router(auth_routes.router)
app.include_router(cameras.router)
app.include_router(analytics.router)
app.include_router(streams.router, prefix="/api/v1/streams")
