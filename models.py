"""
models.py
=========
SQLAlchemy Trade model for GOAT PRO.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Index
from sqlalchemy.orm import declarative_base

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


Index("ix_trades_status_date", Trade.status, Trade.trade_date)
