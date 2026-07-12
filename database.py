"""
GOAT PRO — Database Engine & Session
"""

import os
import logging
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker, declarative_base

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/goatpro")

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def _migrate_trades_table():
    """Drop and recreate trades table if schema mismatch detected."""
    try:
        inspector = inspect(engine)
        if "trades" in inspector.get_table_names():
            columns = [c["name"] for c in inspector.get_columns("trades")]
            expected = ["id", "direction", "entry_price", "exit_price", "pnl", "status", "signal_type", "trade_date"]
            if "entry_price" not in columns:
                logger.warning("Trades table schema mismatch — recreating table")
                with engine.connect() as conn:
                    conn.execute(text("DROP TABLE trades CASCADE"))
                    conn.commit()
    except Exception as e:
        logger.error(f"Migration check error: {e}")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    _migrate_trades_table()
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready")
