"""
database.py
Engine / session management + PostGIS extension bootstrap.
"""
import os
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://cctv_user:cctv_pass@localhost:5432/cctv_registry",
)

# pool_pre_ping avoids stale-connection errors on long-lived deployments
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)

Base = declarative_base()


def get_db():
    """FastAPI dependency -> yields a request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_postgis():
    """
    Ensures the PostGIS extension exists. Call once at app startup.
    Requires the DB user to have CREATE EXTENSION privileges (or that a
    superuser has already run this once on the target database).
    """
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        conn.commit()


def create_all_tables():
    """Creates tables from models.py metadata. For quick-start / dev use.
    In production, prefer Alembic migrations instead."""
    import models  # noqa: F401 - ensures models are registered on Base.metadata
    Base.metadata.create_all(bind=engine)
