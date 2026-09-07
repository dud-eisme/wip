"""
main.py
FastAPI entrypoint for Model 2: Live Feed Relay + ANPR.

Run with:
    uvicorn main:app --reload --port 8001

(Use a different port than Model 1 if running both at once on one machine.)
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from database import create_all_tables
from camera_worker import source_manager
from routers import sources, stream, anpr
import storage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cctv_model2")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        create_all_tables()
        logger.info("Database ready: Model 2 tables ensured (camera_sources, anpr_jobs, anpr_events).")
    except SQLAlchemyError as exc:
        logger.error("Database initialization failed: %s", exc)
        raise
    yield
    logger.info("Shutting down — stopping all camera worker threads.")
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    return {
        "status": "ok",
        "active_camera_workers": source_manager.active_count(),
        "max_concurrent_sources": source_manager.max_concurrent_sources,
        "snapshot_storage": storage.storage_stats(),
    }


app.include_router(sources.router)
app.include_router(stream.router)
app.include_router(anpr.router)
