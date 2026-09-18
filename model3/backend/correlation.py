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


def compute_reliability(health_status: str, consensus_agreement: "float | None" = None) -> str:
    """An ANPR hit from an offline/defective camera is less trustworthy
    than one from a healthy camera — that was the original correlation
    insight. But camera health isn't the only thing that can make a read
    doubtful: Model 2's multi-frame consensus tracking (plate_consensus.py)
    reports `consensus_agreement`, the weakest per-character vote across
    every frame that saw the plate. A healthy camera with a poorly-agreed
    read and a defective camera with a unanimous read are two DIFFERENT
    problems — one is "can we trust this camera", the other is "can we
    trust this OCR result" — so this function now takes the worse of the
    two signals rather than only ever looking at camera health.

    consensus_agreement is optional because older Model 2 events (or a
    Model 2 version that predates plate_consensus.py) won't have it —
    absence is treated as "no OCR-quality signal available", not as
    grounds for downgrading, since that would punish events for missing
    data rather than for a demonstrated problem.
    """
    if health_status == "inactive":
        health_reliability = "low"
    elif health_status in ("maintenance", "unknown"):
        health_reliability = "medium"
    else:
        health_reliability = "high"

    if consensus_agreement is None:
        return health_reliability

    # Thresholds mirror plate_consensus.CONSENSUS_MIN_AGREEMENT's spirit
    # (0.5 is already "reject outright" territory in Model 2) — here
    # they're softer since a merely-mediocre agreement shouldn't force
    # 'low' on its own, only combine with an already-weak camera signal.
    if consensus_agreement < 0.6:
        agreement_reliability = "low"
    elif consensus_agreement < 0.8:
        agreement_reliability = "medium"
    else:
        agreement_reliability = "high"

    order = {"low": 0, "medium": 1, "high": 2}
    return min(health_reliability, agreement_reliability, key=lambda r: order[r])


def correlate_events(raw_events: list[dict], camera_context: dict) -> list[dict]:
    """
    Joins each raw Model 2 event with its camera's context from Model 1.
    Events referencing a camera Model 1 doesn't know about still pass
    through, marked with unknown context, rather than being dropped —
    federation should degrade gracefully, not silently lose data.

    Field names here match Model 2's real AnprEventOut schema:
    camera_id, plate_text, detected_at, confidence, is_flagged, plus the
    vehicle_* / consensus_* fields vehicle_pipeline.py and
    plate_consensus.py add to each event.
    """
    correlated = []
    for event in raw_events:
        cam_id = event.get("camera_id")
        context = camera_context.get(str(cam_id) if cam_id else None, {"department": None, "healthStatus": "unknown"})
        consensus_agreement = event.get("consensus_agreement")

        correlated.append(
            {
                "id": event.get("id"),
                "plateNumber": event.get("plate_text"),
                "cameraId": cam_id,
                "department": context["department"],
                "cameraHealthStatus": context["healthStatus"],
                "reliability": compute_reliability(context["healthStatus"], consensus_agreement),
                "timestamp": event.get("detected_at"),
                "confidence": event.get("confidence"),
                "isFlagged": event.get("is_flagged", False),
                "sourceModel": "Model 2",
                # Vehicle recognition (vehicle_pipeline.py) — absent means
                # "not determined" on Model 2's side, not a federation
                # failure, so these pass through as None unchanged.
                "vehicleType": event.get("vehicle_type"),
                "vehicleColour": event.get("vehicle_colour"),
                "vehicleMake": event.get("vehicle_make"),
                "vehicleModel": event.get("vehicle_model"),
                "vehicleMakeModelSource": event.get("vehicle_make_model_source"),
                # Multi-frame consensus (plate_consensus.py) — read_count
                # and agreement are what compute_reliability above used;
                # surfaced here too so the frontend can show WHY a read is
                # rated the way it is, not just the final label.
                "consensusReadCount": event.get("consensus_read_count"),
                "consensusAgreement": consensus_agreement,
            }
        )
    return correlated


def build_analytics_summary(correlated_events: list[dict]) -> dict:
    """The 'sample federated analytics report' deliverable — a headline
    plus key stats, same shape as Model 1's gap-analysis summary for
    consistency across the three models' demos."""
    total = len(correlated_events)
    low_reliability = sum(1 for e in correlated_events if e["reliability"] == "low")
    # Separate from low_reliability on purpose: a camera-health problem
    # and a poor-OCR-agreement problem call for different action (fix/
    # replace the camera vs. re-tune ANPR thresholds or accept the read
    # is genuinely doubtful), and folding both into one number hides
    # which one a given deployment actually has more of.
    low_agreement = sum(
        1 for e in correlated_events
        if e.get("consensusAgreement") is not None and e["consensusAgreement"] < 0.6
    )

    department_breakdown: dict = {}
    for e in correlated_events:
        dept = e["department"] or "Unknown"
        department_breakdown[dept] = department_breakdown.get(dept, 0) + 1

    headline = (
        f"{total} events federated from Model 2, correlated against Model 1's registry. "
        f"{low_reliability} flagged low-reliability overall"
        + (f" ({low_agreement} of those due to weak OCR agreement rather than camera health)"
           if low_agreement else "")
        + "."
    )

    return {
        "totalEvents": total,
        "lowReliabilityCount": low_reliability,
        "lowAgreementCount": low_agreement,
        "departmentBreakdown": department_breakdown,
        "headline": headline,
    }
