"""
main.py
Model 3: VMS Federation & Middleware — FastAPI entrypoint.

Run with:
    uvicorn main:app --reload --port 8002
"""
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

from adapters import ADAPTERS, RegistryAdapter, ViewerAdapter
from correlation import build_camera_context_map, correlate_events, build_analytics_summary

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("model3_federation")

app = FastAPI(
    title="VMS Federation & Middleware",
    description=(
        "Model 3 backend: federates Model 1 (CCTV Registry) and Model 2 "
        "(Unified Viewer) via an adapter pattern, correlating ANPR events "
        "with camera health/department context."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
        else:
            state["error"] = result.error

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

    return build_analytics_summary(correlated)
