"""
GOAT PRO — Paper Trade Logic
"""

import logging
from datetime import datetime
from sqlalchemy.orm import Session
from models import Trade
from config import MAX_DAILY_LOSS, MAX_TRADE_LOSS, MIN_SIGNAL_CONFIDENCE

logger = logging.getLogger(__name__)


def _get_shared_state():
    """Lazy import to avoid circular dependency."""
    import strategy
    return strategy.shared_state


def _get_session_pnl(db: Session) -> float:
    trades = db.query(Trade).filter(Trade.status == "CLOSED").all()
    return sum(t.pnl or 0 for t in trades)


def _get_active_trade(db: Session) -> Trade:
    return db.query(Trade).filter(Trade.status == "OPEN").first()


def _check_risk(db: Session) -> tuple:
    session_pnl = _get_session_pnl(db)
    if session_pnl <= -MAX_DAILY_LOSS:
        return False, f"Daily loss limit hit (₹{session_pnl})"

    active = _get_active_trade(db)
    if active:
        return False, "Already have an open trade"

    return True, ""


def check_risk_limits(db: Session) -> tuple:
    """Public wrapper around _check_risk. Returns (ok: bool, message: str)."""
    return _check_risk(db)


def get_institutional_stats(db: Session) -> dict:
    """Calculate win rate and total trades from closed trades."""
    closed = db.query(Trade).filter(Trade.status == "CLOSED").all()
    total = len(closed)
    if total:
        wins = sum(1 for t in closed if (t.pnl or 0) > 0)
        win_rate = round(wins / total * 100, 1)
    else:
        win_rate = 0.0

    return {
        "win_rate": win_rate,
        "total_trades": total,
        "status": "Live",
    }


def get_options_contract(spot: float, signal: str) -> dict:
    """Return basic ATM Nifty options contract details."""
    if not spot or spot <= 0:
        return {}

    # Nifty strike interval is 50 points
    atm_strike = round(spot / 50) * 50

    if signal == "LONG":
        option_type = "CE"
    elif signal == "SHORT":
        option_type = "PE"
    else:
        return {}

    return {
        "symbol": f"NIFTY{atm_strike}{option_type}",
        "strike": atm_strike,
        "type": option_type,
        "spot": spot,
        "signal": signal,
    }


def open_paper_trade(db: Session, signal: str, spot: float) -> Trade:
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

    shared_state = _get_shared_state()
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

    shared_state = _get_shared_state()
    shared_state["active_trade"] = None

    logger.info(f"Trade closed: {trade.direction} PnL=₹{trade.pnl}")
    return trade


def process_auto_signal(db: Session):
    shared_state = _get_shared_state()
    signal_data = shared_state.get("real_signal", {})
    signal = signal_data.get("signal", "WAIT")
    confidence = signal_data.get("confidence", 0)
    spot = shared_state.get("spot", 0)

    if not spot or spot <= 0:
        return

    if signal == "WAIT" or confidence < MIN_SIGNAL_CONFIDENCE:
        return

    active = _get_active_trade(db)

    if not active:
        open_paper_trade(db, signal, spot)
        return

    if active.direction != signal and confidence >= MIN_SIGNAL_CONFIDENCE:
        logger.info(f"Reverse signal: {active.direction} -> {signal}")
        close_paper_trade(db, spot)
        return

    target = shared_state.get("active_trade", {}).get("target", 0)
    sl = shared_state.get("active_trade", {}).get("sl", 0)

    if active.direction == "LONG":
        if spot >= target or spot <= sl:
            close_paper_trade(db, spot)
    elif active.direction == "SHORT":
        if spot <= target or spot >= sl:
            close_paper_trade(db, spot)
