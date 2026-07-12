"""
GOAT PRO — Paper Trade Logic
"""

import logging
from datetime import datetime
from sqlalchemy.orm import Session
from models import Trade
from config import MAX_DAILY_LOSS, MAX_TRADE_LOSS, MIN_SIGNAL_CONFIDENCE
from strategy import shared_state

logger = logging.getLogger(__name__)


def _get_session_pnl(db: Session) -> float:
    """Calculate total session PnL from closed trades."""
    trades = db.query(Trade).filter(Trade.status == "CLOSED").all()
    return sum(t.pnl or 0 for t in trades)


def _get_active_trade(db: Session) -> Trade:
    """Get current open trade if any."""
    return db.query(Trade).filter(Trade.status == "OPEN").first()


def _check_risk(db: Session) -> tuple:
    """Check if new trade is allowed. Returns (ok, reason)."""
    session_pnl = _get_session_pnl(db)
    if session_pnl <= -MAX_DAILY_LOSS:
        return False, f"Daily loss limit hit (₹{session_pnl})"

    active = _get_active_trade(db)
    if active:
        return False, "Already have an open trade"

    return True, ""


def open_paper_trade(db: Session, signal: str, spot: float) -> Trade:
    """Open a new paper trade."""
    if signal not in ("LONG", "SHORT"):
        return None

    ok, reason = _check_risk(db)
    if not ok:
        logger.warning(f"Trade blocked: {reason}")
        return None

    trade = Trade(
        direction=signal,
        entry_price=spot,
        status="OPEN",
        signal_type=signal,
        trade_date=datetime.now(),
    )
    db.add(trade)
    db.commit()
    db.refresh(trade)

    # Update shared state
    shared_state["active_trade"] = {
        "direction": signal,
        "entry": spot,
        "target": round(spot + 50, 2) if signal == "LONG" else round(spot - 50, 2),
        "sl": round(spot - 30, 2) if signal == "LONG" else round(spot + 30, 2),
        "live_pnl": 0,
    }

    logger.info(f"Trade opened: {signal} @ {spot}")
    return trade


def close_paper_trade(db: Session, spot: float) -> Trade:
    """Close the active paper trade."""
    trade = _get_active_trade(db)
    if not trade:
        return None

    trade.exit_price = spot
    trade.status = "CLOSED"

    if trade.direction == "SHORT":
        trade.pnl = round(trade.entry_price - spot, 2)
    else:
        trade.pnl = round(spot - trade.entry_price, 2)

    db.commit()
    db.refresh(trade)

    # Clear shared state
    shared_state["active_trade"] = None

    logger.info(f"Trade closed: {trade.direction} PnL=₹{trade.pnl}")
    return trade


def process_auto_signal(db: Session):
    """Auto-trade logic — called by indicator poller."""
    signal_data = shared_state.get("real_signal", {})
    signal = signal_data.get("signal", "WAIT")
    confidence = signal_data.get("confidence", 0)
    spot = shared_state.get("spot", 0)

    if not spot or spot <= 0:
        return

    if signal == "WAIT" or confidence < MIN_SIGNAL_CONFIDENCE:
        return

    active = _get_active_trade(db)

    # No active trade — open one
    if not active:
        open_paper_trade(db, signal, spot)
        return

    # Active trade exists — check for reverse signal
    if active.direction != signal and confidence >= MIN_SIGNAL_CONFIDENCE:
        logger.info(f"Reverse signal: {active.direction} -> {signal}")
        close_paper_trade(db, spot)
        # Gap protection — don't immediately re-open
        return

    # Check target/SL hit
    target = shared_state.get("active_trade", {}).get("target", 0)
    sl = shared_state.get("active_trade", {}).get("sl", 0)

    if active.direction == "LONG":
        if spot >= target or spot <= sl:
            close_paper_trade(db, spot)
    elif active.direction == "SHORT":
        if spot <= target or spot >= sl:
            close_paper_trade(db, spot)
