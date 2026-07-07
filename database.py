import json
import os
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.engine import Engine
from typing import Generator, Optional

from models import Base, Trade, AppState
import config

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────
# Database Setup
# ────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL")

# For Render.com PostgreSQL, convert postgres:// to postgresql://
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if DATABASE_URL and DATABASE_URL.startswith("postgresql://") and "+" not in DATABASE_URL.split(":", 1)[1]:
    # Use psycopg2 explicitly for maximum compatibility with Render
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

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

def init_db() -> None:
    """Create all tables and load initial app state. Safe to call on every startup."""
    if db_engine is None:
        logger.warning("init_db skipped — no engine.")
        return
    try:
        Base.metadata.create_all(bind=db_engine)
        logger.info("Database tables verified / created.")
    except Exception as e:
        logger.error(f"init_db error: {e}")

    # ---- One-time lightweight migration for trade_date ----
    try:
        with db_engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE trades ADD COLUMN IF NOT EXISTS trade_date VARCHAR(10)"
            ))
            conn.commit()
        logger.info("Migration check passed: trade_date column ensured.")
    except Exception as e:
        logger.error(f"migration check error (safe to ignore if table is brand new): {e}")

    # Load app state on startup
    if SessionLocal:
        with SessionLocal() as db:
            load_app_state(db, config.state_manager)

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

# ────────────────────────────────────────────
# State Persistence Functions
# ────────────────────────────────────────────
def save_app_state(db: Session, state_manager: config.StateManager):
    """Saves the current state of the StateManager to the database."""
    if not db_engine: return
    try:
        state_data = {
            "latest_prices": state_manager.latest_prices,
            "oi_data": state_manager.oi_data,
            "greeks_data": state_manager.greeks_data,
            "news_feed": state_manager.news_feed,
            "market_alerts": state_manager.market_alerts,
            "candle_store": state_manager.candle_store,
            "indicator_data": state_manager.indicator_data,
            "signal_data": state_manager.signal_data,
            "active_trade_context": state_manager.active_trade_context,
        }
        
        for key, value in state_data.items():
            serialized_value = json.dumps(value)
            app_state_entry = db.query(AppState).filter(AppState.state_key == key).first()
            if app_state_entry:
                app_state_entry.state_value = serialized_value
            else:
                app_state_entry = AppState(state_key=key, state_value=serialized_value)
                db.add(app_state_entry)
        db.commit()
        state_manager.last_state_save_time = config.get_ist_now()
        logger.debug("App state saved to DB.")
    except Exception as e:
        db.rollback()
        logger.error(f"Error saving app state to DB: {e}")

def load_app_state(db: Session, state_manager: config.StateManager):
    """Loads the application state from the database into the StateManager."""
    if not db_engine: return
    try:
        for key in [
            "latest_prices", "oi_data", "greeks_data", "news_feed", 
            "market_alerts", "candle_store", "indicator_data", "signal_data",
            "active_trade_context"
        ]:
            app_state_entry = db.query(AppState).filter(AppState.state_key == key).first()
            if app_state_entry:
                value = json.loads(app_state_entry.state_value)
                state_manager.set_state(key, value)
        logger.info("App state loaded from DB.")
    except Exception as e:
        logger.error(f"Error loading app state from DB: {e}")
