"""
migrate_add_webrtc_url.py
One-time migration: adds camera_sources.webrtc_url.

Why this exists: create_all_tables() uses SQLAlchemy's create_all(), which
only CREATES missing tables. It will not ALTER a table that already exists,
so adding a column to models.py is invisible to an existing database — the
app starts fine and then fails on the first query referencing the new
column. Run this once against each environment that already has a
camera_sources table.

Safe to run more than once: uses IF NOT EXISTS.

Usage (from model2/backend, venv active):
    python migrate_add_webrtc_url.py
"""
from sqlalchemy import text

from database import engine


def main():
    with engine.connect() as conn:
        conn.execute(
            text("ALTER TABLE camera_sources ADD COLUMN IF NOT EXISTS webrtc_url VARCHAR")
        )
        conn.commit()
        result = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'camera_sources' AND column_name = 'webrtc_url'"
            )
        ).fetchone()

    if result:
        print("OK: camera_sources.webrtc_url exists.")
    else:
        print("FAILED: column still missing after ALTER — check DB permissions.")


if __name__ == "__main__":
    main()
