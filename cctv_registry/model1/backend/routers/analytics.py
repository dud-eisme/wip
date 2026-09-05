"""
routers/analytics.py
Gap-analysis: ageing infrastructure + PostGIS coverage-density / dead-zone
detection, packaged as a leadership-ready summary report.
"""
import math
from datetime import date, datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db
from models import Camera, User, DepartmentEnum, HealthStatusEnum
from schemas import GapAnalysisReport
from auth import get_current_user, department_scope

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])

AGEING_THRESHOLD_YEARS = 5
DEFAULT_DBSCAN_EPS_METERS = 500  # cameras within 500m of each other -> same cluster
DEFAULT_DBSCAN_MIN_POINTS = 3
DEFAULT_GRID_CELLS_PER_SIDE = 10
DEAD_ZONE_MAX_CAMERAS = 0  # grid cells with <= this many cameras are "dead zones"


def _years_between(d: date, today: date) -> float:
    return round((today - d).days / 365.25, 2)


@router.get("/gap-analysis", response_model=GapAnalysisReport)
def gap_analysis(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    department: Optional[DepartmentEnum] = Query(
        None, description="Restrict report to one department. Ignored for non-admins (auto-scoped)."
    ),
    dbscan_eps_meters: float = Query(DEFAULT_DBSCAN_EPS_METERS, gt=0, le=50000),
    dbscan_min_points: int = Query(DEFAULT_DBSCAN_MIN_POINTS, ge=1, le=100),
    grid_cells_per_side: int = Query(DEFAULT_GRID_CELLS_PER_SIDE, ge=2, le=100),
):
    scoped_department = department_scope(current_user)
    effective_department = scoped_department if scoped_department is not None else department

    base_filter_sql = ""
    params = {}
    if effective_department is not None:
        base_filter_sql = "WHERE department = :department"
        params["department"] = effective_department.value

    total_cameras = db.execute(
        text(f"SELECT COUNT(*) FROM cameras {base_filter_sql}"), params
    ).scalar_one()

    # -----------------------------------------------------------------
    # 1. Ageing infrastructure
    # -----------------------------------------------------------------
    today = datetime.now(timezone.utc).date()
    ageing_rows = db.execute(
        text(
            f"""
            SELECT id, camera_identifier, department, installation_date, health_status
            FROM cameras
            {base_filter_sql}
            {"AND" if base_filter_sql else "WHERE"} (
                installation_date <= (CURRENT_DATE - INTERVAL '{AGEING_THRESHOLD_YEARS} years')
                OR health_status = 'Maintenance Required'
            )
            ORDER BY installation_date ASC
            """
        ),
        params,
    ).mappings().all()

    ageing_list = []
    for row in ageing_rows:
        reasons = []
        age_years = _years_between(row["installation_date"], today)
        if age_years >= AGEING_THRESHOLD_YEARS:
            reasons.append("age_gt_5_years")
        if row["health_status"] == HealthStatusEnum.MAINTENANCE_REQUIRED.value:
            reasons.append("maintenance_required")
        ageing_list.append(
            {
                "id": str(row["id"]),
                "camera_identifier": row["camera_identifier"],
                "department": row["department"],
                "installation_date": row["installation_date"].isoformat(),
                "age_years": age_years,
                "health_status": row["health_status"],
                "reason": reasons,
            }
        )

    ageing_infrastructure = {
        "threshold_years": AGEING_THRESHOLD_YEARS,
        "flagged_count": len(ageing_list),
        "flagged_pct_of_total": round(len(ageing_list) / total_cameras * 100, 1) if total_cameras else 0.0,
        "cameras": ageing_list,
    }

    # -----------------------------------------------------------------
    # 2a. High-density clusters via ST_ClusterDBSCAN (geography-accurate,
    #     eps supplied in meters, transformed via geography cast)
    # -----------------------------------------------------------------
    cluster_rows = db.execute(
        text(
            f"""
            SELECT cluster_id, COUNT(*) AS camera_count,
                   ST_Y(ST_Centroid(ST_Collect(location))) AS centroid_lat,
                   ST_X(ST_Centroid(ST_Collect(location))) AS centroid_lon
            FROM (
                SELECT location,
                       ST_ClusterDBSCAN(location::geometry, eps := :eps_deg, minpoints := :minpoints)
                           OVER () AS cluster_id
                FROM cameras
                {base_filter_sql}
            ) clustered
            WHERE cluster_id IS NOT NULL
            GROUP BY cluster_id
            ORDER BY camera_count DESC
            """
        ),
        {
            **params,
            # crude meters -> degrees conversion (good enough for clustering radius at
            # state scale); for high-precision use ST_Transform to a local projected CRS.
            "eps_deg": dbscan_eps_meters / 111_320.0,
            "minpoints": dbscan_min_points,
        },
    ).mappings().all()

    high_density_clusters = [
        {
            "cluster_id": row["cluster_id"],
            "camera_count": row["camera_count"],
            "centroid_lat": round(row["centroid_lat"], 6),
            "centroid_lon": round(row["centroid_lon"], 6),
        }
        for row in cluster_rows
    ]

    # -----------------------------------------------------------------
    # 2b. Dead-zone detection via grid counting over the scoped bounding box
    # -----------------------------------------------------------------
    bbox_row = db.execute(
        text(f"SELECT ST_Extent(location) AS bbox FROM cameras {base_filter_sql}"), params
    ).mappings().first()

    grid_cells = []
    dead_zone_count = 0
    if bbox_row and bbox_row["bbox"]:
        # ST_Extent returns "BOX(min_lon min_lat,max_lon max_lat)"
        box_str = bbox_row["bbox"].replace("BOX(", "").replace(")", "")
        (min_lon, min_lat), (max_lon, max_lat) = (
            tuple(float(v) for v in part.split()) for part in box_str.split(",")
        )
        lon_step = (max_lon - min_lon) / grid_cells_per_side or 1e-9
        lat_step = (max_lat - min_lat) / grid_cells_per_side or 1e-9

        grid_rows = db.execute(
            text(
                f"""
                SELECT
                    FLOOR((ST_X(location) - :min_lon) / :lon_step) AS gx,
                    FLOOR((ST_Y(location) - :min_lat) / :lat_step) AS gy,
                    COUNT(*) AS camera_count
                FROM cameras
                {base_filter_sql}
                GROUP BY gx, gy
                """
            ),
            {**params, "min_lon": min_lon, "min_lat": min_lat, "lon_step": lon_step, "lat_step": lat_step},
        ).mappings().all()

        occupied_cells = {(int(r["gx"]), int(r["gy"])): r["camera_count"] for r in grid_rows}

        for gx in range(grid_cells_per_side):
            for gy in range(grid_cells_per_side):
                count = occupied_cells.get((gx, gy), 0)
                is_dead = count <= DEAD_ZONE_MAX_CAMERAS
                if is_dead:
                    dead_zone_count += 1
                grid_cells.append(
                    {
                        "grid_lat": round(min_lat + gy * lat_step, 6),
                        "grid_lon": round(min_lon + gx * lon_step, 6),
                        "camera_count": count,
                        "is_dead_zone": is_dead,
                    }
                )

    coverage_density = {
        "dbscan_params": {"eps_meters": dbscan_eps_meters, "min_points": dbscan_min_points},
        "high_density_clusters": high_density_clusters,
        "grid_params": {"cells_per_side": grid_cells_per_side, "dead_zone_threshold_cameras": DEAD_ZONE_MAX_CAMERAS},
        "dead_zone_cell_count": dead_zone_count,
        "total_grid_cells": len(grid_cells),
        "grid": grid_cells,
    }

    # -----------------------------------------------------------------
    # 3. Leadership summary
    # -----------------------------------------------------------------
    summary = {
        "headline": (
            f"{total_cameras} cameras tracked"
            + (f" for {effective_department.value}" if effective_department else " state-wide")
            + f"; {len(ageing_list)} flagged as ageing/needs-maintenance "
              f"({ageing_infrastructure['flagged_pct_of_total']}%); "
              f"{len(high_density_clusters)} high-density clusters identified; "
              f"{dead_zone_count} of {len(grid_cells)} grid zones show minimal coverage."
        ),
        "recommended_actions": [
            "Prioritize replacement/maintenance for cameras flagged in ageing_infrastructure.",
            "Review dead-zone grid cells for new camera placement, especially in high-footfall areas.",
            "Validate high-density clusters aren't redundant coverage that could be reallocated.",
        ],
    }

    return GapAnalysisReport(
        generated_at=datetime.now(timezone.utc),
        scope_department=effective_department.value if effective_department else None,
        total_cameras=total_cameras,
        ageing_infrastructure=ageing_infrastructure,
        coverage_density=coverage_density,
        summary=summary,
    )
