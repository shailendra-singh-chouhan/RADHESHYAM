import os
import json
import logging
from sqlalchemy import create_engine, Column, Integer, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.sql import func

logger = logging.getLogger(__name__)
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/goatpro")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_size=5, max_overflow=10, pool_timeout=30)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class AppStateModel(Base):
    __tablename__ = "app_states"
    id = Column(Integer, primary_key=True, index=True)
    state_json = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def save_app_state(state_dict, *args):
    db = SessionLocal()
    try:
        db.query(AppStateModel).delete()
        new_state = AppStateModel(state_json=json.dumps(state_dict, default=str))
        db.add(new_state)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Save State Error: {e}")
    finally:
        db.close()

Base.metadata.create_all(bind=engine)
