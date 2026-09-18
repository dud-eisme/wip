"""
vahan.py
Vehicle registry (VAHAN) lookups by plate number, with caching, rate
limiting and deliberate PII stripping.

This replaces the lookup_registry() stub in vehicle_pipeline.py. Import
this module and point REGISTRY_LOOKUP_ENABLED at it; vehicle_pipeline
will then prefer registry answers over vision guesses for make/model.

--- Provider-agnostic on purpose ---------------------------------------
Direct VAHAN/Parivahan access is granted per-agency and the exact request
shape differs between the official endpoint and every licensed reseller.
Rather than hard-coding one, this module treats the provider as config:

    VAHAN_API_URL        full URL, with {plate} as the placeholder if the
                         plate goes in the path, e.g.
                         "https://api.example.com/v1/rc/{plate}"
    VAHAN_HTTP_METHOD    GET (default) or POST. POST sends
                         {"<VAHAN_PLATE_FIELD>": "<plate>"} as JSON.
    VAHAN_PLATE_FIELD    body/query key for the plate (default "rc_number")
    VAHAN_AUTH_HEADER    header name for the credential (default "Authorization")
    VAHAN_AUTH_PREFIX    prefix on the credential value (default "Bearer ")
    VAHAN_API_KEY        the credential itself
    VAHAN_FIELD_MAP      JSON object mapping OUR field names to dotted
                         paths in the provider's response, e.g.
                         {"make":"data.maker_description",
                          "model":"data.maker_model",
                          "colour":"data.colour",
                          "fuel_type":"data.fuel_type",
                          "registration_date":"data.registration_date",
                          "vehicle_class":"data.vehicle_class"}

Nothing here needs code changes to switch providers — only .env.

--- PII: read this before turning it on ---------------------------------
A full RC lookup returns owner name, father's name, and often the
registered address. That is personal data about a member of the public,
returned for any plate a camera happens to see, in a system that logs
every detection to a database indefinitely.

So this module keeps ONLY vehicle attributes by default and drops owner
fields before they ever reach a return value, let alone the database
(VAHAN_KEEP_OWNER_FIELDS, default false). If your deployment genuinely
requires owner details, that should be a deliberate, separately-audited
decision with its own access control and retention rule — not something
that arrives by accident because it was in the JSON.

The same reasoning is why lookups are OPT-IN per call site rather than
automatic on every frame: see VAHAN_LOOKUP_POLICY below.

--- Cost and rate limits ------------------------------------------------
Registry lookups are per-call billed and rate limited, and a busy
junction camera can produce hundreds of plate reads a minute. Three
things keep this from becoming a problem:

  1. A persistent cache (registration data effectively never changes for
     a given plate). Cached on disk under STORAGE_ROOT so it survives
     restarts, keyed by normalised plate.
  2. A token-bucket rate limiter, so a burst of traffic queues instead of
     hammering the provider into a 429 ban.
  3. VAHAN_LOOKUP_POLICY, which decides WHICH plates are worth a call at
     all. "flagged" (the default) looks up only plates you have marked as
     of-interest — the sane production default. "all" looks up every
     unique plate. "none" disables lookups while leaving the wiring in
     place.
"""
import json
import logging
import os
import re
import threading
import time
from typing import Dict, NamedTuple, Optional

from storage import STORAGE_ROOT

logger = logging.getLogger("cctv_model2.vahan")

VAHAN_API_URL = os.getenv("VAHAN_API_URL", "")
VAHAN_HTTP_METHOD = os.getenv("VAHAN_HTTP_METHOD", "GET").strip().upper()
VAHAN_PLATE_FIELD = os.getenv("VAHAN_PLATE_FIELD", "rc_number")
VAHAN_AUTH_HEADER = os.getenv("VAHAN_AUTH_HEADER", "Authorization")
VAHAN_AUTH_PREFIX = os.getenv("VAHAN_AUTH_PREFIX", "Bearer ")
VAHAN_API_KEY = os.getenv("VAHAN_API_KEY", "")
VAHAN_TIMEOUT_SECONDS = float(os.getenv("VAHAN_TIMEOUT_SECONDS", "10"))
VAHAN_MAX_RETRIES = int(os.getenv("VAHAN_MAX_RETRIES", "2"))

VAHAN_LOOKUP_POLICY = os.getenv("VAHAN_LOOKUP_POLICY", "flagged").strip().lower()
VAHAN_MAX_CALLS_PER_MINUTE = int(os.getenv("VAHAN_MAX_CALLS_PER_MINUTE", "60"))
VAHAN_KEEP_OWNER_FIELDS = os.getenv("VAHAN_KEEP_OWNER_FIELDS", "false").strip().lower() in (
    "1", "true", "yes",
)

VAHAN_CACHE_ENABLED = os.getenv("VAHAN_CACHE_ENABLED", "true").strip().lower() in (
    "1", "true", "yes",
)
VAHAN_CACHE_PATH = os.getenv("VAHAN_CACHE_PATH", "") or str(STORAGE_ROOT / "vahan_cache.json")
# Negative results (plate genuinely not found, or a malformed OCR read
# that will never resolve) are cached too, but for much less time — a
# plate can legitimately appear in the registry later, and a permanent
# negative would hide it forever.
VAHAN_CACHE_TTL_DAYS = int(os.getenv("VAHAN_CACHE_TTL_DAYS", "180"))
VAHAN_NEGATIVE_CACHE_TTL_HOURS = int(os.getenv("VAHAN_NEGATIVE_CACHE_TTL_HOURS", "24"))

_DEFAULT_FIELD_MAP = {
    "make": "data.maker_description",
    "model": "data.maker_model",
    "colour": "data.colour",
    "fuel_type": "data.fuel_type",
    "vehicle_class": "data.vehicle_class",
    "registration_date": "data.registration_date",
}

# Fields that are dropped unless VAHAN_KEEP_OWNER_FIELDS — matched
# case-insensitively as substrings against the mapped key names, so a
# provider calling it "owner_name" or "ownerName" is caught either way.
_OWNER_FIELD_MARKERS = ("owner", "father", "address", "mobile", "phone", "aadhaar", "pan")

_PLATE_CLEAN_RE = re.compile(r"[^A-Z0-9]")


class RegistryRecord(NamedTuple):
    plate_text: str
    make: Optional[str] = None
    model: Optional[str] = None
    colour: Optional[str] = None
    fuel_type: Optional[str] = None
    vehicle_class: Optional[str] = None
    registration_date: Optional[str] = None
    source: str = "vahan"

    def as_dict(self) -> Dict[str, Optional[str]]:
        return dict(self._asdict())


def _field_map() -> Dict[str, str]:
    raw = os.getenv("VAHAN_FIELD_MAP", "")
    if not raw:
        return dict(_DEFAULT_FIELD_MAP)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except json.JSONDecodeError:
        logger.warning("VAHAN_FIELD_MAP is not valid JSON — falling back to the default map")
    return dict(_DEFAULT_FIELD_MAP)


def normalise_plate(plate_text: str) -> str:
    """Upper-cases and strips everything but letters/digits, so
    'MH 12 AB 1234', 'mh12ab1234' and 'MH-12-AB-1234' are one cache key
    and one lookup rather than three."""
    return _PLATE_CLEAN_RE.sub("", (plate_text or "").upper())


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class _TokenBucket:
    """Simple thread-safe token bucket. The ANPR job thread and any API
    request handler can both call through here, so it has to be shared
    and locked rather than per-caller."""

    def __init__(self, capacity: int, refill_per_second: float):
        self._capacity = max(capacity, 1)
        self._tokens = float(self._capacity)
        self._refill = refill_per_second
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 30.0) -> bool:
        """Blocks until a token is free or `timeout` elapses. Returns
        False on timeout — the caller should then skip the lookup rather
        than queue forever behind a saturated bucket, since a stale
        lookup helps nobody and the frame loop still has to keep up."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._refill)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                shortfall = (1.0 - self._tokens) / self._refill if self._refill > 0 else timeout
            if time.monotonic() + shortfall > deadline:
                return False
            time.sleep(min(shortfall, 0.5))


_bucket = _TokenBucket(
    capacity=max(VAHAN_MAX_CALLS_PER_MINUTE // 4, 1),
    refill_per_second=VAHAN_MAX_CALLS_PER_MINUTE / 60.0,
)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: Optional[Dict[str, dict]] = None


def _load_cache() -> Dict[str, dict]:
    global _cache
    if _cache is not None:
        return _cache
    _cache = {}
    if not VAHAN_CACHE_ENABLED:
        return _cache
    try:
        with open(VAHAN_CACHE_PATH, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            _cache = loaded
            logger.info("Loaded %d cached registry lookups from %s", len(_cache), VAHAN_CACHE_PATH)
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read registry cache at %s — starting empty", VAHAN_CACHE_PATH)
    return _cache


def _persist_cache() -> None:
    """Whole-file rewrite through a temp file. The cache is small (one
    small dict per unique plate ever seen) and written rarely — only on a
    cache MISS, never on a hit — so the simplicity is worth more than
    incremental writes would be. Writing to a temp file and replacing
    means a crash mid-write can't leave a truncated cache behind."""
    if not VAHAN_CACHE_ENABLED:
        return
    try:
        path = VAHAN_CACHE_PATH
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_cache or {}, fh)
        os.replace(tmp, path)
    except OSError:
        logger.warning("Could not persist registry cache to %s", VAHAN_CACHE_PATH, exc_info=True)


def _cache_get(plate: str) -> Optional[dict]:
    cache = _load_cache()
    entry = cache.get(plate)
    if not entry:
        return None
    age = time.time() - entry.get("fetched_at", 0)
    found = entry.get("record") is not None
    ttl = (VAHAN_CACHE_TTL_DAYS * 86400) if found else (VAHAN_NEGATIVE_CACHE_TTL_HOURS * 3600)
    if age > ttl:
        return None
    return entry


def _cache_put(plate: str, record: Optional[dict]) -> None:
    with _cache_lock:
        cache = _load_cache()
        cache[plate] = {"fetched_at": time.time(), "record": record}
        _persist_cache()


def cache_stats() -> dict:
    cache = _load_cache()
    found = sum(1 for e in cache.values() if e.get("record"))
    return {
        "enabled": VAHAN_CACHE_ENABLED,
        "path": VAHAN_CACHE_PATH,
        "entries": len(cache),
        "found": found,
        "not_found": len(cache) - found,
    }


# ---------------------------------------------------------------------------
# Provider call
# ---------------------------------------------------------------------------

def _dig(payload, dotted_path: str):
    """Follows a dotted path into nested dicts/lists. Returns None rather
    than raising on any missing key or wrong type — provider responses
    vary in shape between success, 'not found', and error cases, and a
    field-map mismatch should degrade to 'no data' rather than kill the
    job."""
    current = payload
    for part in dotted_path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if current is None:
            return None
    return current


def _strip_owner_fields(mapped: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
    if VAHAN_KEEP_OWNER_FIELDS:
        return mapped
    kept = {}
    for key, value in mapped.items():
        if any(marker in key.lower() for marker in _OWNER_FIELD_MARKERS):
            continue
        kept[key] = value
    return kept


def _call_provider(plate: str) -> Optional[dict]:
    """Returns the raw JSON payload, or None if the plate isn't found.
    Raises on transport/auth failures so the caller can distinguish
    'no such vehicle' (cache the negative) from 'our provider is down'
    (do NOT cache, retry later)."""
    import requests  # lazy — the rest of the app doesn't need it

    headers = {"Accept": "application/json"}
    if VAHAN_API_KEY:
        headers[VAHAN_AUTH_HEADER] = f"{VAHAN_AUTH_PREFIX}{VAHAN_API_KEY}"

    url = VAHAN_API_URL.replace("{plate}", plate)
    last_error: Optional[Exception] = None

    for attempt in range(VAHAN_MAX_RETRIES + 1):
        try:
            if VAHAN_HTTP_METHOD == "POST":
                response = requests.post(
                    url, json={VAHAN_PLATE_FIELD: plate},
                    headers=headers, timeout=VAHAN_TIMEOUT_SECONDS,
                )
            else:
                params = None if "{plate}" in VAHAN_API_URL else {VAHAN_PLATE_FIELD: plate}
                response = requests.get(
                    url, params=params, headers=headers, timeout=VAHAN_TIMEOUT_SECONDS,
                )

            if response.status_code == 404:
                return None  # genuinely not found — a cacheable negative
            if response.status_code == 429 or response.status_code >= 500:
                # Transient. Back off and retry; the token bucket above
                # should normally prevent 429 but providers also enforce
                # daily/monthly caps the bucket knows nothing about.
                wait = 2 ** attempt
                logger.warning(
                    "Registry lookup for %s got HTTP %s — retrying in %ss",
                    plate, response.status_code, wait,
                )
                time.sleep(wait)
                last_error = RuntimeError(f"HTTP {response.status_code}")
                continue
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - re-raised below after retries
            last_error = exc
            if attempt < VAHAN_MAX_RETRIES:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"Registry lookup failed for {plate}: {last_error}")


def lookup(plate_text: str, force: bool = False) -> Optional[RegistryRecord]:
    """
    Looks a plate up against the registry. Returns a RegistryRecord, or
    None if the plate isn't registered / the lookup couldn't be made.

    `force=True` bypasses VAHAN_LOOKUP_POLICY (not the cache) — use it for
    an explicit "look this one up" button in the UI, where a human has
    asked for this specific plate regardless of the automatic policy.

    Never raises. A provider outage degrades to None and a logged warning:
    ANPR must keep logging events when the registry is unreachable.
    """
    plate = normalise_plate(plate_text)
    if not plate:
        return None
    if not VAHAN_API_URL:
        logger.debug("VAHAN_API_URL is unset — skipping registry lookup for %s", plate)
        return None
    if VAHAN_LOOKUP_POLICY == "none" and not force:
        return None

    cached = _cache_get(plate)
    if cached is not None:
        record = cached.get("record")
        return RegistryRecord(**record) if record else None

    if not _bucket.acquire():
        logger.warning("Registry rate limit saturated — skipping lookup for %s", plate)
        return None

    try:
        payload = _call_provider(plate)
    except Exception as exc:  # noqa: BLE001
        # Do NOT cache: this is our problem, not a statement about the
        # plate. Caching it would suppress retries for a whole TTL.
        logger.warning("Registry lookup error for %s: %s", plate, exc)
        return None

    if payload is None:
        _cache_put(plate, None)
        return None

    mapped = {key: _dig(payload, path) for key, path in _field_map().items()}
    mapped = _strip_owner_fields(mapped)
    mapped = {k: (str(v).strip() if v is not None else None) for k, v in mapped.items()}

    record = RegistryRecord(
        plate_text=plate,
        make=mapped.get("make"),
        model=mapped.get("model"),
        colour=mapped.get("colour"),
        fuel_type=mapped.get("fuel_type"),
        vehicle_class=mapped.get("vehicle_class"),
        registration_date=mapped.get("registration_date"),
    )
    _cache_put(plate, record.as_dict())
    return record


def should_look_up(is_flagged: bool) -> bool:
    """Policy gate the job loop calls before paying for a lookup.
    'flagged' is the default because looking up every plate a junction
    camera sees is both the expensive option and the one that collects
    the most registry data about uninvolved people."""
    if VAHAN_LOOKUP_POLICY == "all":
        return True
    if VAHAN_LOOKUP_POLICY == "flagged":
        return is_flagged
    return False


def is_configured() -> tuple:
    """(configured, reason_if_not) — for the /health endpoint, so a
    misconfigured registry is visible without waiting for the first
    silent None."""
    if VAHAN_LOOKUP_POLICY not in ("none", "flagged", "all"):
        return False, f"VAHAN_LOOKUP_POLICY={VAHAN_LOOKUP_POLICY!r} (expected none|flagged|all)"
    if VAHAN_LOOKUP_POLICY == "none":
        return True, None
    if not VAHAN_API_URL:
        return False, "VAHAN_API_URL is unset"
    if not VAHAN_API_KEY:
        return False, "VAHAN_API_KEY is unset"
    try:
        import requests  # noqa: F401
    except ImportError as exc:
        return False, str(exc)
    return True, None
