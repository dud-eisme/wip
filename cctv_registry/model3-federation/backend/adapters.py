"""
adapters.py
The adapter pattern: each VMS/system this middleware federates gets one
adapter class implementing a common interface (`get_cameras`, `get_events`).
Adding a fourth system later (a real Model 3+ vendor, a different VMS)
means writing one new adapter class — nothing else in this file, or in
main.py's correlation logic, needs to change. That's the actual point of
the "extensible connector framework" deliverable.
"""
import os
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

import httpx

from model1_auth import get_model1_token, invalidate_model1_token

MODEL1_REGISTRY_BASE = os.getenv("MODEL1_REGISTRY_BASE", "http://localhost:8000/api/v1")
MODEL2_VIEWER_BASE = os.getenv("MODEL2_VIEWER_BASE", "http://localhost:8001/api/v1")
ADAPTER_TIMEOUT = float(os.getenv("ADAPTER_TIMEOUT_SECONDS", "5"))


class AdapterResult:
    """Wraps an adapter's output with whether the call actually succeeded —
    callers need this to build an honest connector-status panel rather than
    pretending a failed adapter returned an empty-but-successful result."""

    def __init__(self, ok: bool, data=None, error: str | None = None):
        self.ok = ok
        self.data = data if data is not None else []
        self.error = error


class VMSAdapter(ABC):
    """Common interface every federated system adapter must implement."""

    id: str
    name: str
    source_model: str
    endpoint: str

    @abstractmethod
    async def get_cameras(self) -> AdapterResult:
        ...

    @abstractmethod
    async def get_events(self) -> AdapterResult:
        ...


class RegistryAdapter(VMSAdapter):
    """Adapter for Model 1 — CCTV Registry. Provides camera metadata
    (department, health status) used to enrich Model 2's raw events."""

    id = "adapter-model1"
    name = "Registry Adapter"
    source_model = "Model 1 — CCTV Registry"
    endpoint = MODEL1_REGISTRY_BASE

    async def get_cameras(self) -> AdapterResult:
        try:
            token = await get_model1_token()
            async with httpx.AsyncClient(timeout=ADAPTER_TIMEOUT) as client:
                res = await client.get(
                    f"{MODEL1_REGISTRY_BASE}/cameras",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if res.status_code == 401:
                    # Cached token expired/invalid server-side — retry once
                    # with a fresh one before giving up.
                    invalidate_model1_token()
                    token = await get_model1_token()
                    res = await client.get(
                        f"{MODEL1_REGISTRY_BASE}/cameras",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                res.raise_for_status()
                return AdapterResult(ok=True, data=res.json().get("items", []))
        except httpx.HTTPError as exc:
            return AdapterResult(ok=False, error=str(exc))
        except Exception as exc:  # e.g. login itself failed
            return AdapterResult(ok=False, error=f"Auth or connection error: {exc}")

    async def get_events(self) -> AdapterResult:
        # Model 1 doesn't have events (that's Model 2's domain) — not
        # applicable for this adapter.
        return AdapterResult(ok=True, data=[])


class ViewerAdapter(VMSAdapter):
    """Adapter for Model 2 — Unified Viewer. Provides raw ANPR detection
    events, before any correlation with camera context."""

    id = "adapter-model2"
    name = "Viewer Adapter"
    source_model = "Model 2 — Unified Viewer"
    endpoint = MODEL2_VIEWER_BASE

    async def get_cameras(self) -> AdapterResult:
        return AdapterResult(ok=True, data=[])

    async def get_events(self) -> AdapterResult:
        try:
            async with httpx.AsyncClient(timeout=ADAPTER_TIMEOUT) as client:
                res = await client.get(f"{MODEL2_VIEWER_BASE}/events")
                res.raise_for_status()
                return AdapterResult(ok=True, data=res.json())
        except httpx.HTTPError as exc:
            # Expected during development if Model 2's backend isn't running
            # yet — the connector status panel is designed to surface this
            # honestly rather than silently showing empty data.
            return AdapterResult(ok=False, error=f"Model 2 unreachable: {exc}")


ADAPTERS: list[VMSAdapter] = [RegistryAdapter(), ViewerAdapter()]
