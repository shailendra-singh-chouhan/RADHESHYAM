"""
database.py
===========
SQLAlchemy engine + session factory for GOAT PRO PostgreSQL.
"""

import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.engine import Engine
from typing import Generator, Optional

from models import Base

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if DATABASE_URL and DATABASE_URL.startswith("postgresql://") and "+" not in DATABASE_URL.split(":", 1)[1]:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

db_engine: Optional[Engine] = None
SessionLocal = None

if DATABASE_URL:
    try:
        db_engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,
            pool_recycle=280,
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
    target = engine or db_engine
    if target is None:
        logger.warning("init_db skipped — no engine.")
        return
    try:
        Base.metadata.create_all(bind=target)
        logger.info("Database tables verified / created.")
    except Exception as e:
        logger.error(f"init_db error: {e}")


def get_db() -> Generator[Session, None, None]:
    if SessionLocal is None:
        yield None
        return
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
