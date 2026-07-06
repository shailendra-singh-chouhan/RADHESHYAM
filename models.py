# models.py

from sqlalchemy import Column, Integer, Float, DateTime, String
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
import logging

# Logger setup
logger = logging.getLogger(__name__)

# Base class for all ORM models
Base = declarative_base()

class Trade(Base):
    """
    Database model for tracking trades.
    """
    __tablename__ = 'trades' # This is the actual table name in PostgreSQL

    id = Column(Integer, primary_key=True, autoincrement=True)
    direction = Column(String, nullable=False) # e.g., 'BUY', 'SELL'
    entry = Column(Float, nullable=False)       # Entry price
    target = Column(Float, nullable=False)      # Target price
    sl = Column(Float, nullable=False)          # Stop Loss price
    exit_price = Column(Float)                  # Exit price (NULL if trade is open)
    pnl = Column(Float)                         # Profit or Loss (NULL if trade is open)
    opened_at = Column(DateTime, nullable=False, default=datetime.utcnow) # Timestamp when trade was opened (UTC)
    closed_at = Column(DateTime)                # Timestamp when trade was closed (NULL if open)
    status = Column(String, default='ACTIVE')   # Trade status (e.g., 'ACTIVE', 'CLOSED')

    def __repr__(self):
        """
        Provides a readable representation of the Trade object.
        """
        return (f"<Trade(id={self.id}, direction='{self.direction}', "
                f"status='{self.status}', opened_at='{self.opened_at.strftime('%Y-%m-%d %H:%M:%S')}')>")
