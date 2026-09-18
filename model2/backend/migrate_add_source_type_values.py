"""
migrate_add_source_type_values.py
One-time migration: adds any missing values to the Postgres enum type
`source_type_enum`.

Why this exists: SQLAlchemy's create_all() only CREATES missing types and
tables. When a new member is added to SourceTypeEnum in models.py (HLS was
added after the type already existed in deployed databases), the Postgres
type is left untouched — so the app starts fine and then fails on the
first INSERT using the new value, with:

    invalid input value for enum source_type_enum: "hls"

This is the same class of bug as the earlier `health_status_enum` issue in
Model 1: Python-side enum changed, database-side type did not.

IMPORTANT — why AUTOCOMMIT: `ALTER TYPE ... ADD VALUE` cannot be executed
inside a transaction block on PostgreSQL versions before 12, and even on
12+ the newly added value cannot be USED in the same transaction that
added it. Running each ALTER in its own autocommitted statement avoids
both problems.

Safe to run repeatedly: uses IF NOT EXISTS, and reports what it actually
changed.

Usage (from model2/backend, venv active):
    python migrate_add_source_type_values.py
"""
from sqlalchemy import text

from database import engine
from models import SourceTypeEnum

ENUM_TYPE_NAME = "source_type_enum"


def existing_values(conn) -> set:
    rows = conn.execute(
        text(
            "SELECT e.enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = :typename"
        ),
        {"typename": ENUM_TYPE_NAME},
    ).fetchall()
    return {r[0] for r in rows}


def main():
    wanted = [member.value for member in SourceTypeEnum]

    # AUTOCOMMIT: see the module docstring — ALTER TYPE ... ADD VALUE must
    # not run inside a transaction block.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        present = existing_values(conn)
        if not present:
            print(
                f"Type '{ENUM_TYPE_NAME}' does not exist yet. Nothing to migrate — "
                f"create_all_tables() will create it with all current values."
            )
            return

        print(f"Existing values: {sorted(present)}")
        missing = [v for v in wanted if v not in present]

        if not missing:
            print("Nothing to add — the database already has every value in SourceTypeEnum.")
            return

        for value in missing:
            # The value is interpolated rather than bound because Postgres
            # does not accept a bind parameter in ALTER TYPE. It comes from
            # our own SourceTypeEnum definition, never from user input.
            conn.execute(
                text(f"ALTER TYPE {ENUM_TYPE_NAME} ADD VALUE IF NOT EXISTS '{value}'")
            )
            print(f"  added: {value}")

        final = existing_values(conn)
        print(f"Final values: {sorted(final)}")

        still_missing = [v for v in wanted if v not in final]
        if still_missing:
            print(f"FAILED: still missing {still_missing} — check DB permissions.")
        else:
            print("OK: every SourceTypeEnum value now exists in the database.")


if __name__ == "__main__":
    main()
