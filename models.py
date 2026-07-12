"""
GOAT PRO — Trade Model
"""

from sqlalchemy import Column, Integer, Float, String, DateTime
from sqlalchemy.sql import func
from database import Base


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    direction = Column(String(10), nullable=False)  # LONG or SHORT
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    status = Column(String(10), default="OPEN")  # OPEN or CLOSED
    signal_type = Column(String(10), default="WAIT")  # LONG, SHORT, WAIT
    trade_date = Column(DateTime(timezone=True), server_default=func.now())
