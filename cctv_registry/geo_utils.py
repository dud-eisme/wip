"""
geo_utils.py
Small helpers for converting between (lat, lon) pairs and PostGIS geometry.
"""
from geoalchemy2.elements import WKTElement
from geoalchemy2.shape import to_shape

from models import Camera


def make_point(latitude: float, longitude: float) -> WKTElement:
    """PostGIS stores POINT(x y) = POINT(lon, lat)."""
    return WKTElement(f"POINT({longitude} {latitude})", srid=4326)


def camera_to_dict(camera: Camera) -> dict:
    """Flattens a Camera ORM object (with PostGIS geometry) into a plain dict
    matching CameraOut, extracting latitude/longitude from the geometry."""
    point = to_shape(camera.location)
    return {
        "id": camera.id,
        "camera_identifier": camera.camera_identifier,
        "camera_name": camera.camera_name,
        "latitude": point.y,
        "longitude": point.x,
        "department": camera.department,
        "camera_type": camera.camera_type,
        "ownership": camera.ownership,
        "connectivity_status": camera.connectivity_status,
        "storage_details": camera.storage_details,
        "health_status": camera.health_status,
        "installation_date": camera.installation_date,
        "last_ping": camera.last_ping,
        "stream_endpoint": camera.stream_endpoint,
        "created_at": camera.created_at,
        "updated_at": camera.updated_at,
    }
