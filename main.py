"""
GOAT PRO — FastAPI Entry Point
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text, inspect

from database import engine, SessionLocal
from models import Base
import routes
import strategy
import stocks

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _auto_migrate():
    """Add missing columns to existing tables. Safe to run on every startup."""
    try:
        inspector = inspect(engine)
        if "trades" not in inspector.get_table_names():
            return  # create_all will handle new tables

        existing = {c["name"] for c in inspector.get_columns("trades")}

        with engine.begin() as conn:
            if "direction" not in existing:
                conn.execute(text("ALTER TABLE trades ADD COLUMN IF NOT EXISTS direction VARCHAR(10)"))
                logger.info("Migration: added direction")

            if "entry_price" not in existing:
                conn.execute(text("ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_price FLOAT"))
                conn.execute(text("UPDATE trades SET entry_price = 0 WHERE entry_price IS NULL"))
                try:
                    conn.execute(text("ALTER TABLE trades ALTER COLUMN entry_price SET NOT NULL"))
                except Exception:
                    pass
                logger.info("Migration: added entry_price")

            if "exit_price" not in existing:
                conn.execute(text("ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_price FLOAT"))
                logger.info("Migration: added exit_price")

            if "pnl" not in existing:
                conn.execute(text("ALTER TABLE trades ADD COLUMN IF NOT EXISTS pnl FLOAT"))
                logger.info("Migration: added pnl")

            if "status" not in existing:
                conn.execute(text("ALTER TABLE trades ADD COLUMN IF NOT EXISTS status VARCHAR(10) DEFAULT 'OPEN'"))
                logger.info("Migration: added status")

            if "signal_type" not in existing:
                conn.execute(text("ALTER TABLE trades ADD COLUMN IF NOT EXISTS signal_type VARCHAR(10) DEFAULT 'WAIT'"))
                logger.info("Migration: added signal_type")

            if "trade_date" not in existing:
                conn.execute(text("ALTER TABLE trades ADD COLUMN IF NOT EXISTS trade_date TIMESTAMP WITH TIME ZONE DEFAULT NOW()"))
                logger.info("Migration: added trade_date")

        logger.info("Auto-migration complete")
    except Exception as e:
        logger.warning(f"Auto-migration skipped: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables, migrate schema, start background threads. Shutdown: stop threads."""
    logger.info("GOAT PRO starting up...")

    # Create DB tables (new tables only — never alters existing ones)
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready")

    # Migrate existing tables (add missing columns that create_all skipped)
    _auto_migrate()

    # Start background pollers
    strategy.start_background_threads(db_session_factory=SessionLocal)

    # Start stock poller
    stocks.start_stock_poller()

    logger.info("All background threads started")

    yield

    # Shutdown
    strategy.stop_background_threads()
    stocks.stop_stock_poller()
    logger.info("GOAT PRO shut down")


app = FastAPI(title="GOAT PRO", version="3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes.router)
