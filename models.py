from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Index, Text
from sqlalchemy.orm import declarative_base
import json

Base = declarative_base()


class Trade(Base):
    """A single paper trade (BUY/LONG or SELL/SHORT)."""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    direction = Column(String(16), nullable=False, index=True)
    entry = Column(Float, nullable=False)
    target = Column(Float, nullable=True)
    sl = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    status = Column(String(16), nullable=False, default="ACTIVE", index=True)
    opened_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime, nullable=True)
    trade_date = Column(String(10), nullable=False, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "direction": self.direction,
            "entry": self.entry,
            "target": self.target,
            "sl": self.sl,
            "exit_price": self.exit_price,
            "pnl": self.pnl,
            "status": self.status,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "trade_date": self.trade_date,
        }


class AppState(Base):
    """Stores a snapshot of the application's StateManager for persistence."""
    __tablename__ = "app_state"

    id = Column(Integer, primary_key=True, index=True)
    state_key = Column(String(255), unique=True, nullable=False)
    state_value = Column(Text, nullable=False) # Storing JSON serialized state
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "state_key": self.state_key,
            "state_value": json.loads(self.state_value),
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }


Index("ix_trades_status_date", Trade.status, Trade.trade_date)
