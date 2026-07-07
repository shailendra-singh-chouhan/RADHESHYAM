from sqlalchemy.orm import Session
from models import Trade
import config
from logzero import logger

def open_paper_trade(db: Session, direction: str = "LONG") -> tuple[bool, str]:
    """
    Opens a paper trade with support for LONG or SHORT direction.
    - LONG: SL = 0.5% below entry, Target = 1% above entry
    - SHORT: SL = 0.5% above entry, Target = 1% below entry
    """
    # 1. Check if trade already active
    active = db.query(Trade).filter(Trade.status == "ACTIVE").first()
    if active:
        return False, "A trade is already active."

    # 2. Get current price
    current_price = config.latest_prices.get("nifty")
    if not current_price:
        return False, "No market data available."

    # 3. Calculate SL/Target based on direction
    sl_pct = 0.005  # 0.5%
    target_pct = 0.01  # 1%
    
    entry = current_price
    if direction == "LONG":
        sl = round(entry * (1 - sl_pct), 2)
        target = round(entry * (1 + target_pct), 2)
    else:  # SHORT
        sl = round(entry * (1 + sl_pct), 2)
        target = round(entry * (1 - target_pct), 2)

    # 4. Save to DB
    try:
        new_trade = Trade(
            direction=direction,
            entry=entry,
            sl=sl,
            target=target,
            status="ACTIVE",
            trade_date=config.get_ist_now().date().isoformat()
        )
        db.add(new_trade)
        db.commit()
        logger.info(f"Trade opened: {direction} at {entry} (SL: {sl}, TGT: {target})")
        return True, f"{direction} trade opened at {entry}"
    except Exception as e:
        logger.error(f"Error opening trade: {e}")
        return False, f"Database error: {str(e)}"

def close_paper_trade(db: Session) -> tuple[bool, str]:
    """Closes the active paper trade."""
    active = db.query(Trade).filter(Trade.status == "ACTIVE").first()
    if not active:
        return False, "No active trade to close."
    
    current_price = config.latest_prices.get("nifty")
    if not current_price:
        return False, "Cannot close: No market data."

    active.status = "CLOSED"
    active.closed_at = config.get_ist_now()
    active.exit_price = current_price
    
    # PnL Calculation
    if active.direction == "LONG":
        active.pnl = round(current_price - active.entry, 2)
    else:
        active.pnl = round(active.entry - current_price, 2)
        
    db.commit()
    logger.info(f"Trade closed: {active.direction} at {current_price}, PnL: {active.pnl}")
    return True, f"Trade closed. PnL: {active.pnl}"

def check_risk_limits(db: Session) -> tuple[bool, str]:
    """Checks if we can take more trades based on risk rules."""
    today = config.get_ist_now().date().isoformat()
    
    # 1. Max trades per day (e.g., 5)
    total_today = db.query(Trade).filter(Trade.trade_date == today).count()
    if total_today >= 5:
        return False, "Max 5 trades limit reached."
    
    # 2. Daily Loss Limit (-2000)
    today_closed = db.query(Trade).filter(
        Trade.status == "CLOSED", Trade.trade_date == today
    ).all()
    today_pnl = sum(t.pnl or 0 for t in today_closed)
    if today_pnl <= -2000:
        return False, "Daily loss limit of -2000 reached."
        
    return True, "Risk OK"

def get_institutional_stats(db: Session) -> dict:
    if not db:
        return {"win_rate": 0, "total_trades": 0}
    
    all_closed = db.query(Trade).filter(Trade.status == "CLOSED").all()
    total = len(all_closed)
    if total == 0:
        return {"win_rate": 0, "total_trades": 0}
    
    wins = sum(1 for t in all_closed if (t.pnl or 0) > 0)
    return {
        "win_rate": round((wins / total) * 100, 1),
        "total_trades": total
    }
