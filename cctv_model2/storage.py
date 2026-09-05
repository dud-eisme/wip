"""
storage.py
Manages where detection snapshot images actually live on disk.

Layout (under STORAGE_ROOT, default "./media"):

    media/
      anpr_snapshots/
        <camera_id>/
          <YYYY-MM-DD>/
            <event_id>.jpg

Organized by camera then date so:
  - a human browsing the disk can find "what did Camera X see on date Y"
    without a database lookup
  - no single folder ever accumulates tens of thousands of files (which
    slows down every OS's file listing) — it naturally shards by day

Snapshot paths are stored in the database as paths RELATIVE to
STORAGE_ROOT (e.g. "anpr_snapshots/<camera_id>/2026-09-05/<event_id>.jpg"),
never as absolute paths — so moving the deployment to a different machine
or drive doesn't break every existing record.
"""
import os
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT", "./media")).resolve()

JPEG_QUALITY = 90


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def build_snapshot_relpath(camera_id: Optional[uuid.UUID], event_id: uuid.UUID, on_date: Optional[date] = None) -> str:
    """Returns the path (relative to STORAGE_ROOT) a snapshot for this
    detection should be saved at. Does not touch the filesystem."""
    on_date = on_date or date.today()
    camera_folder = str(camera_id) if camera_id else "unassigned"
    return f"anpr_snapshots/{camera_folder}/{on_date.isoformat()}/{event_id}.jpg"


def save_snapshot(relpath: str, image: "np.ndarray") -> str:
    """
    Saves a cropped plate image (BGR numpy array, as OpenCV produces) to
    STORAGE_ROOT/<relpath>, creating any missing folders. Returns the same
    relpath back for convenience (so callers can do
    `event.snapshot_path = save_snapshot(relpath, crop)`).
    """
    full_path = STORAGE_ROOT / relpath
    _ensure_dir(full_path.parent)
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise RuntimeError(f"Failed to encode snapshot image for {relpath}")
    full_path.write_bytes(encoded.tobytes())
    return relpath


def resolve_snapshot_path(relpath: str) -> Optional[Path]:
    """Resolves a stored relative snapshot path back to a real file on
    disk. Returns None if the path doesn't exist (e.g. deleted, or disk
    was wiped but the DB row wasn't cleaned up)."""
    full_path = (STORAGE_ROOT / relpath).resolve()
    # Guard against a relpath containing ".." escaping STORAGE_ROOT.
    if STORAGE_ROOT not in full_path.parents and full_path != STORAGE_ROOT:
        return None
    if not full_path.is_file():
        return None
    return full_path


def storage_stats() -> dict:
    """Quick disk-usage summary — handy for an admin/health endpoint."""
    if not STORAGE_ROOT.exists():
        return {"storage_root": str(STORAGE_ROOT), "exists": False, "file_count": 0, "total_bytes": 0}
    total_bytes = 0
    file_count = 0
    for f in STORAGE_ROOT.rglob("*.jpg"):
        if f.is_file():
            total_bytes += f.stat().st_size
            file_count += 1
    return {
        "storage_root": str(STORAGE_ROOT),
        "exists": True,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / (1024 * 1024), 2),
    }
