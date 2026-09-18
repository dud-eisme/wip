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

from model1_auth import get_model1_token, invalidate_model1_token, Model1AuthError

MODEL1_REGISTRY_BASE = os.getenv("MODEL1_REGISTRY_BASE", "http://localhost:8000/api/v1")
# BUG FIX: Model 2's ANPR router is mounted at /api/v2 (see
# routers/anpr.py: `APIRouter(prefix="/api/v2/anpr", ...)`), not /api/v1.
# The old default here pointed at /api/v1/anpr/events, which 404s against
# a real Model 2 — every request would fail and get reported as "Model 2
# unreachable" by the connector status panel, even with Model 2 running
# and healthy. If your .env already overrides this correctly, this
# change is a no-op for you; if it doesn't, this was almost certainly
# why Model 2 showed as unreachable.
MODEL2_VIEWER_BASE = os.getenv("MODEL2_VIEWER_BASE", "http://localhost:8001/api/v2")
ADAPTER_TIMEOUT = float(os.getenv("ADAPTER_TIMEOUT_SECONDS", "5"))

# Model 2 paginates /anpr/events (default limit=100). The old code took
# whatever that first page happened to contain and called it "all the
# events" — analytics totals would silently plateau at 100 forever on
# any real deployment, with no indication anything was being cut off.
VIEWER_EVENTS_PAGE_SIZE = int(os.getenv("VIEWER_EVENTS_PAGE_SIZE", "1000"))
# Hard ceiling on how many pages a single adapter call will fetch, so a
# runaway event table can't turn one federated request into an unbounded
# number of upstream calls / unbounded memory. 20 pages * 1000 = 200k
# events, comfortably past any realistic single-request need — if you're
# routinely hitting this, federated/events needs its own pagination
# instead of trying to return everything at once.
VIEWER_EVENTS_MAX_PAGES = int(os.getenv("VIEWER_EVENTS_MAX_PAGES", "20"))

# Camera metadata changes rarely (a registration, an edit, a health-status
# flip) compared to how often it'd otherwise be re-fetched — every one of
# Model 3's three endpoints calls get_cameras() fresh, and a polling
# dashboard multiplies that further. A short TTL cache cuts Model 1 load
# substantially with negligible staleness cost. Deliberately NOT applied
# to get_events() — event freshness is the actual point of a live feed
# relay's data, and caching it would work against that.
CAMERAS_CACHE_TTL_SECONDS = float(os.getenv("CAMERAS_CACHE_TTL_SECONDS", "4"))
_cameras_cache: dict = {"at": 0.0, "result": None}


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
        now = time.monotonic()
        if _cameras_cache["result"] is not None and (now - _cameras_cache["at"]) < CAMERAS_CACHE_TTL_SECONDS:
            return _cameras_cache["result"]

        result = await self._fetch_cameras()
        # Only cache a SUCCESSFUL result. Caching a failure would mean a
        # transient Model 1 blip gets reported as "down" for the rest of
        # the TTL window instead of self-healing on the next real call.
        if result.ok:
            _cameras_cache["result"] = result
            _cameras_cache["at"] = now
        return result

    async def _fetch_cameras(self) -> AdapterResult:
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
        except Model1AuthError as exc:
            return AdapterResult(ok=False, error=str(exc))
        except Exception as exc:  # anything else unexpected
            return AdapterResult(ok=False, error=f"Unexpected error: {exc}")

    async def get_events(self) -> AdapterResult:
        # Model 1 doesn't have events (that's Model 2's domain) — not
        # applicable for this adapter.
        return AdapterResult(ok=True, data=[])


class ViewerAdapter(VMSAdapter):
    """Adapter for Model 2 — Live Feed Relay & ANPR. Provides raw ANPR
    detection events, before any correlation with camera context.

    Model 2 has no login of its own — it validates Model 1's JWT directly
    (same SECRET_KEY, same users table). So this adapter reuses the exact
    same service-account token as RegistryAdapter, rather than needing a
    separate login flow.
    """

    id = "adapter-model2"
    name = "Viewer Adapter"
    source_model = "Model 2 — Live Feed Relay & ANPR"
    endpoint = MODEL2_VIEWER_BASE

    async def get_cameras(self) -> AdapterResult:
        return AdapterResult(ok=True, data=[])

    async def get_events(self) -> AdapterResult:
        try:
            token = await get_model1_token()  # same token works here too
            all_items: list = []
            async with httpx.AsyncClient(timeout=ADAPTER_TIMEOUT) as client:
                offset = 0
                for _ in range(VIEWER_EVENTS_MAX_PAGES):
                    res = await self._get_events_page(client, token, offset)
                    if res.status_code == 401:
                        invalidate_model1_token()
                        token = await get_model1_token()
                        res = await self._get_events_page(client, token, offset)
                    res.raise_for_status()
                    body = res.json()
                    page_items = body.get("items", [])
                    all_items.extend(page_items)

                    total = body.get("total")
                    offset += len(page_items)
                    # Stop once we've fetched everything Model 2 reports,
                    # or once a page comes back short (belt-and-braces in
                    # case `total` is ever absent/wrong) — either signals
                    # there's nothing more to page through.
                    if not page_items or len(page_items) < VIEWER_EVENTS_PAGE_SIZE:
                        break
                    if total is not None and offset >= total:
                        break
            return AdapterResult(ok=True, data=all_items)
        except httpx.HTTPError as exc:
            # Expected during development if Model 2's backend isn't running
            # yet — the connector status panel is designed to surface this
            # honestly rather than silently showing empty data.
            return AdapterResult(ok=False, error=f"Model 2 unreachable: {exc}")
        except Model1AuthError as exc:
            return AdapterResult(ok=False, error=str(exc))
        except Exception as exc:  # anything else unexpected
            return AdapterResult(ok=False, error=f"Unexpected error: {exc}")

    async def _get_events_page(self, client: "httpx.AsyncClient", token: str, offset: int):
        return await client.get(
            f"{MODEL2_VIEWER_BASE}/anpr/events",
            params={"limit": VIEWER_EVENTS_PAGE_SIZE, "offset": offset},
            headers={"Authorization": f"Bearer {token}"},
        )


ADAPTERS: list[VMSAdapter] = [RegistryAdapter(), ViewerAdapter()]
