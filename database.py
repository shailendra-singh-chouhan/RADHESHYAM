"""
database.py
===========
SQLAlchemy engine + session factory for the GOAT PRO PostgreSQL database.

Reads `DATABASE_URL` from the environment (already configured on Render).
Handles both `postgres://` (older Render format) and `postgresql://` (newer).
"""

import os
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.engine import Engine
from typing import Generator, Optional

from models import Base

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

# Render's older postgres URLs used `postgres://` which SQLAlchemy 1.4+ rejects.
# Normalize to `postgresql://` (or `postgresql+psycopg2://` for explicit driver).
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if DATABASE_URL and DATABASE_URL.startswith("postgresql://") and "+" not in DATABASE_URL.split(":", 1)[1]:
    # Use psycopg2 explicitly for maximum compatibility with Render
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

# Create the engine lazily — Render may not have DATABASE_URL set on first build
db_engine: Optional[Engine] = None
SessionLocal = None

if DATABASE_URL:
    try:
        db_engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,        # detect dropped connections
            pool_recycle=280,          # recycle before Render's 5-min idle timeout
            pool_size=5,
            max_overflow=5,
            echo=False,
        )
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
        logger.info("Database engine created successfully.")
    except Exception as e:
        logger.error(f"Failed to create database engine: {e}")
        db_engine = None
else:
    logger.warning("DATABASE_URL not set. Trades will NOT be persisted.")


def init_db(engine: Optional[Engine] = None) -> None:
    """Create all tables. Safe to call on every startup (CREATE IF NOT EXISTS)."""
    target = engine or db_engine
    if target is None:
        logger.warning("init_db skipped — no engine.")
        return
    try:
        Base.metadata.create_all(bind=target)
        logger.info("Database tables verified / created.")
    except Exception as e:
        logger.error(f"init_db error: {e}")

    # ---- One-time lightweight migration ----
    # create_all() only creates tables that don't exist yet — it never
    # alters an EXISTING table. If a `trades` table already existed from
    # an earlier version of this project (without trade_date), we add the
    # missing column here so the app doesn't crash looking for it.
    try:
        with target.connect() as conn:
            conn.execute(text(
                "ALTER TABLE trades ADD COLUMN IF NOT EXISTS trade_date VARCHAR(10)"
            ))
            conn.commit()
        logger.info("Migration check passed: trade_date column ensured.")
    except Exception as e:
        logger.error(f"migration check error (safe to ignore if table is brand new): {e}")


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a SQLAlchemy session and closes it after."""
    if SessionLocal is None:
        # Yield None so endpoints can decide how to handle missing DB
        yield None
        return
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
