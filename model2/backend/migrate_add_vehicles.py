"""
migrate_add_vehicle_columns.py
One-time migration: adds the vehicle-recognition columns to anpr_events.

Why this exists: same reason as migrate_add_webrtc_url.py — create_all()
only CREATES missing tables, it will not ALTER an existing one, so adding
these columns to models.py is invisible to any database that already has
an anpr_events table. The app starts fine and then fails on the first
query referencing them.

Columns added (all nullable — "not determined" is a real and expected
answer from vehicle_pipeline.py, not an error):

    vehicle_type              car | motorcycle | bus | truck | bicycle | auto-rickshaw | tractor
    vehicle_type_confidence   detector confidence for that class
    vehicle_colour            plain colour name, or NULL if undeterminable
    vehicle_colour_confidence share of sampled body pixels that voted for it
    vehicle_make              e.g. "Maruti Suzuki"
    vehicle_model             e.g. "Swift"
    vehicle_make_model_confidence
    vehicle_make_model_source 'vision' or 'registry' — kept because a
                              registry (VAHAN) answer is ground truth and
                              a vision answer is a guess, and you will
                              absolutely want to filter on which is which
                              later. Stored as plain VARCHAR rather than a
                              new Postgres enum on purpose: enum changes
                              need their own ALTER TYPE migration dance
                              (see migrate_add_source_type_values.py), and
                              this field isn't worth that.
    vehicle_bbox              'x1,y1,x2,y2' of the vehicle box in the source
                              frame, so a snapshot can be re-cropped to the
                              whole vehicle later without re-running detection

Also adds the plate_consensus.py columns, since models.py puts them on
the same AnprEvent row and this migration is the one that will actually
run against an existing database:

    consensus_read_count      how many frames voted on this plate's track
    consensus_agreement       weakest per-character agreement, 0-1 — a
                              far better trust signal than confidence
                              alone (see plate_consensus.py)
    consensus_alternatives    other whole-string reads seen for the same
                              track, comma-separated, most common first

Safe to run more than once: uses IF NOT EXISTS on every column.

Usage (from model2/backend, venv active):
    python migrate_add_vehicle_columns.py
"""
from sqlalchemy import text

from database import engine

TABLE = "anpr_events"

COLUMNS = [
    ("read_count", "INTEGER"),
    ("agreement", "DOUBLE PRECISION"),
    ("vehicle_type", "VARCHAR"),
    ("vehicle_type_confidence", "DOUBLE PRECISION"),
    ("vehicle_colour", "VARCHAR"),
    ("vehicle_colour_confidence", "DOUBLE PRECISION"),
    ("vehicle_make", "VARCHAR"),
    ("vehicle_model", "VARCHAR"),
    ("vehicle_make_model_confidence", "DOUBLE PRECISION"),
    ("vehicle_make_model_source", "VARCHAR"),
    ("vehicle_bbox", "VARCHAR"),
    # Multi-frame consensus (plate_consensus.py) — added here too since
    # models.py defines them on the same AnprEvent row and create_all()
    # won't retrofit either set onto a table that already exists.
    ("consensus_read_count", "INTEGER"),
    ("consensus_agreement", "DOUBLE PRECISION"),
    ("consensus_alternatives", "VARCHAR"),
]

# Filtering "show me every white Maruti seen by camera X" is the whole
# point of storing these, so index the three fields a search UI will
# actually filter on. The others are display-only.
INDEXES = [
    ("ix_anpr_events_vehicle_type", "vehicle_type"),
    ("ix_anpr_events_vehicle_colour", "vehicle_colour"),
    ("ix_anpr_events_vehicle_make", "vehicle_make"),
]


def main():
    with engine.connect() as conn:
        for name, sql_type in COLUMNS:
            conn.execute(
                text(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {name} {sql_type}")
            )
            print(f"  ensured column: {name} {sql_type}")

        for index_name, column in INDEXES:
            conn.execute(
                text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {TABLE} ({column})")
            )
            print(f"  ensured index:  {index_name}")

        conn.commit()

        present = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = :table"
                ),
                {"table": TABLE},
            ).fetchall()
        }

    missing = [name for name, _ in COLUMNS if name not in present]
    if missing:
        print(f"FAILED: still missing {missing} — check DB permissions.")
    else:
        print(f"OK: every vehicle column exists on {TABLE}.")


if __name__ == "__main__":
    main()
