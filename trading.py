import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional, tuple
from logzero import logger

import config
from models import Trade

def _today_ist_str() -> str:
    return config.get_ist_now().date().isoformat()

def check_risk_limits(db: Session) -> tuple[bool, str]:
    """Checks daily max trades (5) and max drawdown limit (-2000)."""
    if db is None:
        return True, "Risk OK (no DB)"
    today = _today_ist_str()
    todays_closed = db.query(Trade).filter(
        Trade.status == "CLOSED",
        Trade.trade_date == today,
    ).all()
    todays_pnl = sum(t.pnl or 0 for t in todays_closed)
    if len(todays_closed) >= 5:
        return False, "Daily trade limit (5) reached"
    if todays_pnl <= -2000:
        return False, "Daily loss limit (-2000) hit"
    return True, "Risk OK"

def open_paper_trade(db: Session) -> tuple[bool, str]:
    """Executes a long paper trade position on Nifty."""
    if db is None:
        return False, "Database not connected"
    existing = db.query(Trade).filter(Trade.status == "ACTIVE").first()
    if existing:
        return False, "A trade is already active"
    if config.get_market_status() != "OPEN":
        return False, "Market is closed"
    if config.latest_prices["nifty"] is None:
        return False, "Live price not available yet"
    risk_ok, risk_message = check_risk_limits(db)
    if not risk_ok:
        return False, risk_message

    entry = config.latest_prices["nifty"]
    new_trade = Trade(
        direction="LONG",
        entry=entry,
        target=round(entry + 50, 2),
        sl=round(entry - 25, 2),
        opened_at=datetime.datetime.utcnow(),
        status="ACTIVE",
        trade_date=_today_ist_str(),
    )
    try:
        db.add(new_trade)
        db.commit()
        db.refresh(new_trade)
        logger.info(f"Opened trade #{new_trade.id} at {entry}")
        return True, "Trade opened"
    except Exception as e:
        db.rollback()
        logger.error(f"open_paper_trade error: {e}")
        return False, f"DB error: {e}"

def close_paper_trade(db: Session) -> tuple[bool, str]:
    """Closes active long positions and saves realized PnL."""
    if db is None:
        return False, "Database not connected"
    if config.latest_prices["nifty"] is None:
        return False, "Live price not available"

    trade = db.query(Trade).filter(Trade.status == "ACTIVE").first()
    if not trade:
        return False, "No active trade"

    exit_price = config.latest_prices["nifty"]
    pnl = round(exit_price - trade.entry, 2)
    trade.exit_price = exit_price
    trade.pnl = pnl
    trade.closed_at = datetime.datetime.utcnow()
    trade.status = "CLOSED"
    try:
        db.commit()
        db.refresh(trade)
        logger.info(f"Closed trade #{trade.id} with PnL {pnl}")
        return True, f"Trade closed (PnL: {pnl})"
    except Exception as e:
        db.rollback()
        logger.error(f"close_paper_trade error: {e}")
        return False, f"DB error: {e}"

def get_institutional_stats(db: Optional[Session]) -> dict:
    """Calculates Sharpe Ratio, Max Drawdown, and Win Rate metrics."""
    empty = {
        "sharpe_ratio": 0, "max_drawdown": 0, "expectancy": 0,
        "win_rate": 0, "total_trades": 0,
    }
    if db is None:
        return empty
    try:
        closed_trades = db.query(Trade).filter(Trade.status == "CLOSED")\
                                       .order_by(Trade.closed_at.asc()).all()
    except Exception as e:
        logger.error(f"get_institutional_stats query error: {e}")
        return empty

    if not closed_trades:
        return empty

    pnls = [t.pnl or 0 for t in closed_trades]
    wins = [p for p in pnls if p > 0]
    win_rate = round((len(wins) / len(pnls)) * 100, 1) if pnls else 0
    expectancy = round(sum(pnls) / len(pnls), 1) if pnls else 0

    cumulative = peak = 0
    max_dd = 0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        dd = cumulative - peak
        max_dd = min(max_dd, dd)

    sharpe = 0
    if len(pnls) > 1:
        mean = sum(pnls) / len(pnls)
        variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
        stddev = variance ** 0.5
        if stddev > 0:
            sharpe = round(mean / stddev, 2)

    return {
        "sharpe_ratio": sharpe,
        "max_drawdown": max_dd,
        "expectancy": expectancy,
        "win_rate": win_rate,
        "total_trades": len(pnls),
    }
