"""
GOAT PRO — Database Engine & State Persistence
"""

import os
import json
import logging
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.sql import func

logger = logging.getLogger(__name__)

# Connection Pool Settings optimized for Render Free Tier + Supabase/PostgreSQL
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/goatpro")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# System State Table Definition
class AppStateModel(Base):
    __tablename__ = "app_states"
    
    id = Column(Integer, primary_key=True, index=True)
    state_json = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

def save_app_state(state_dict):
    """
    Serializes and dumps the live operational state_manager memory into PostgreSQL.
    Prevents thread failure by catching exceptions locally.
    """
    db = SessionLocal()
    try:
        # Keep only one active row to prevent database bloat
        db.query(AppStateModel).delete()
        
        # Serialize dict to JSON string safely
        serialized_state = json.dumps(state_dict, default=str)
        new_state = AppStateModel(state_json=serialized_state)
        
        db.add(new_state)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to persist state to database: {e}")
    finally:
        db.close()
