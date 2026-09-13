"""
correlation.py
The actual "event correlation" logic: joins Model 2's raw ANPR events with
Model 1's camera metadata, and derives a reliability rating from the
camera's health status. This is the concrete value-add of federating the
two systems, rather than viewing them separately.
"""


def build_camera_context_map(cameras: list[dict]) -> dict:
    """
    Turns Model 1's camera list into a lookup by UUID id, for joining
    against Model 2's events — Model 2's AnprEvent.camera_id is a foreign
    key into Model 1's cameras.id (the UUID primary key), NOT the
    human-readable camera_identifier. Keying by the wrong field here
    would silently produce zero matches.
    """
    context = {}
    for cam in cameras:
        cam_id = cam.get("id")
        if cam_id:
            context[str(cam_id)] = {
                "department": cam.get("department"),
                "healthStatus": _map_health_status(cam.get("health_status")),
                "cameraIdentifier": cam.get("camera_identifier"),
            }
    return context


def _map_health_status(backend_value: str | None) -> str:
    """Model 1's Postgres values -> the frontend-facing labels every model's
    UI already uses (active/maintenance/inactive) — same mapping Model 1's
    own frontend uses in registry.js, kept consistent here."""
    mapping = {
        "Operational": "active",
        "Maintenance Required": "maintenance",
        "Defective": "inactive",
    }
    return mapping.get(backend_value, "unknown")


def compute_reliability(health_status: str) -> str:
    """An ANPR hit from an offline/defective camera is less trustworthy
    than one from a healthy camera — this is the actual correlation
    insight Model 3 adds on top of Model 2's raw detections.

    A camera Model 1 doesn't recognize at all is treated as 'medium', not
    'high' — we have no evidence the camera is healthy, so confidently
    saying "high" would overstate what we actually know.
    """
    if health_status == "inactive":
        return "low"
    if health_status in ("maintenance", "unknown"):
        return "medium"
    return "high"


def correlate_events(raw_events: list[dict], camera_context: dict) -> list[dict]:
    """
    Joins each raw Model 2 event with its camera's context from Model 1.
    Events referencing a camera Model 1 doesn't know about still pass
    through, marked with unknown context, rather than being dropped —
    federation should degrade gracefully, not silently lose data.

    Field names here match Model 2's real AnprEventOut schema:
    camera_id, plate_text, detected_at, confidence, is_flagged.
    """
    correlated = []
    for event in raw_events:
        cam_id = event.get("camera_id")
        context = camera_context.get(str(cam_id) if cam_id else None, {"department": None, "healthStatus": "unknown"})

        correlated.append(
            {
                "id": event.get("id"),
                "plateNumber": event.get("plate_text"),
                "cameraId": cam_id,
                "department": context["department"],
                "cameraHealthStatus": context["healthStatus"],
                "reliability": compute_reliability(context["healthStatus"]),
                "timestamp": event.get("detected_at"),
                "confidence": event.get("confidence"),
                "isFlagged": event.get("is_flagged", False),
                "sourceModel": "Model 2",
            }
        )
    return correlated


def build_analytics_summary(correlated_events: list[dict]) -> dict:
    """The 'sample federated analytics report' deliverable — a headline
    plus key stats, same shape as Model 1's gap-analysis summary for
    consistency across the three models' demos."""
    total = len(correlated_events)
    low_reliability = sum(1 for e in correlated_events if e["reliability"] == "low")

    department_breakdown: dict = {}
    for e in correlated_events:
        dept = e["department"] or "Unknown"
        department_breakdown[dept] = department_breakdown.get(dept, 0) + 1

    headline = (
        f"{total} events federated from Model 2, correlated against Model 1's registry. "
        f"{low_reliability} flagged low-reliability due to camera health issues."
    )

    return {
        "totalEvents": total,
        "lowReliabilityCount": low_reliability,
        "departmentBreakdown": department_breakdown,
        "headline": headline,
    }
