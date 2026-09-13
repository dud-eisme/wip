"""
database.py
Engine / session management.

Model 2 intentionally connects to the SAME PostgreSQL database as Model 1
(same DATABASE_URL) so it can:
  - read/write the existing `users` table (shared login — no duplicate auth)
  - foreign-key against the existing `cameras` table (Sources registry)
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/cctv_registry",
)

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


def create_all_tables():
    """
    Creates Model 2's own tables (camera_sources, anpr_events, anpr_jobs) if
    they don't exist. Uses checkfirst=True (SQLAlchemy default) so it will
    NOT touch `users` or `cameras` — those already exist from Model 1.
    In production, prefer Alembic migrations instead.
    """
    import models  # noqa: F401 - registers models on Base.metadata
    Base.metadata.create_all(bind=engine)
