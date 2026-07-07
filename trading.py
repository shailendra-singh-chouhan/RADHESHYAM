import math
import datetime
from logzero import logger
from sqlalchemy import func
from models import Trade
import config

# Phase 4 Prep: Angel Client import for premium fetching
try:
    import angel_client
    ANGEL_AVAILABLE = True
except ImportError:
    ANGEL_AVAILABLE = False

def get_options_contract(spot_price: float, signal: str, index: str = "NIFTY", strategy: str = "ATM") -> dict:
    if spot_price is None or signal not in ["LONG", "SHORT"]:
        return None
    step_size = 50 if index == "NIFTY" else 100
    atm_strike = round(spot_price / step_size) * step_size
    selected_strike = atm_strike
    opt_type = ""
    if signal == "LONG":
        opt_type = "CE"
        if strategy == "ITM": selected_strike = atm_strike - step_size
    elif signal == "SHORT":
        opt_type = "PE"
        if strategy == "ITM": selected_strike = atm_strike + step_size

    contract_symbol = f"{index} {selected_strike} {opt_type}"
    logger.info(f"🎯 STRIKE SELECTED: Spot was {spot_price}, chose {contract_symbol}")

    premium_estimate = "Opens at 9:15 AM"
    if ANGEL_AVAILABLE and config.get_market_status() == "OPEN":
        try:
            search_symbol = f"{index}{selected_strike}{opt_type}"
            ltp = angel_client.get_ltp("NFO", search_symbol)
            if ltp and ltp > 0:
                premium_estimate = f"₹{ltp}"
                logger.info(f"💰 LIVE PREMIUM FETCHED: {search_symbol} @ {ltp}")
        except Exception as e:
            logger.error(f"Premium fetch failed: {e}")
            premium_estimate = "Fetching..."

    return {
        "index": index, "strike": selected_strike, "option_type": opt_type,
        "symbol": contract_symbol, "premium_estimate": premium_estimate
    }

def execute_trade(signal: str, spot_price: float) -> dict:
    contract = get_options_contract(spot_price, signal)
    if not contract: return None
    logger.info(f"🚀 EXECUTING {signal} TRADE on {contract['symbol']}")
    entry_price = spot_price
    target = spot_price + 50 if signal == "LONG" else spot_price - 50
    sl = spot_price - 25 if signal == "LONG" else spot_price + 25
    return {"direction": signal, "entry": entry_price, "target": target, "sl": sl, "contract": contract, "live_pnl": 0.0, "status": "OPEN"}

def check_risk_limits(db) -> tuple[bool, str]:
    """
    REAL RISK MANAGEMENT: अगर आज का नुकसान -2000 से ज्यादा हो, तो बॉट बंद हो जाएगा।
    """
    try:
        if not db: return True, "Risk OK (No DB)"
        
        today = datetime.datetime.now().date().isoformat()
        
        # आज के सारे बंद हुए ट्रेड्स का PnL निकालो
        total_pnl = db.query(func.coalesce(func.sum(Trade.pnl), 0.0))\
                       .filter(Trade.status == "CLOSED", Trade.trade_date == today)\
                       .scalar() or 0.0

        # अगर -2000 से नीचे गिरा, तो Risk Fail
        if total_pnl <= -2000.0:
            logger.warning(f"🛑 DAILY LIMIT HIT! Today's PnL is {total_pnl}. Stopping bot.")
            return False, f"🛑 DAILY LIMIT HIT (₹{total_pnl:.2f}). Bot Stopped."
            
        return True, f"Risk OK (Day PnL: ₹{total_pnl:.2f})"
    except Exception as e:
        logger.error(f"Risk check error: {e}")
        return True, f"Risk check skipped: {e}"

def get_institutional_stats(db) -> dict:
    try:
        if db is None: return {"fii_long": 0, "fii_short": 0, "fii_net": 0, "dii_long": 0, "dii_short": 0, "dii_net": 0, "win_rate": 0.0, "total_trades": 0, "status": "No DB"}
        total = db.query(Trade).filter(Trade.status == "CLOSED").count()
        wins = db.query(Trade).filter(Trade.status == "CLOSED", Trade.pnl > 0).count()
        win_rate = round((wins / total * 100), 1) if total > 0 else 0.0
        return {"fii_long": 0, "fii_short": 0, "fii_net": 0, "dii_long": 0, "dii_short": 0, "dii_net": 0, "win_rate": win_rate, "total_trades": total, "status": "Live"}
    except Exception as e: return {"fii_long": 0, "fii_short": 0, "fii_net": 0, "dii_long": 0, "dii_short": 0, "dii_net": 0, "win_rate": 0.0, "total_trades": 0, "status": f"Error: {e}"}

def open_paper_trade(db, signal: str = None, spot: float = None) -> tuple[bool, str]:
    try:
        if db is None: return False, "Database not connected"
        if signal is None: signal = config.signal_data.get("signal")
        if spot is None: spot = config.latest_prices.get("nifty")
        if signal not in ["LONG", "SHORT"] or spot is None: return False, "Invalid signal or no spot price"
        already_open = db.query(Trade).filter(Trade.status == "ACTIVE").first()
        if already_open: return False, "Already have an open trade"
        entry = spot
        target = spot + 50 if signal == "LONG" else spot - 50
        sl = spot - 25 if signal == "LONG" else spot + 25
        new_trade = Trade(direction=signal, entry=entry, target=target, sl=sl, status="ACTIVE", trade_date=config.get_ist_now().date().isoformat())
        db.add(new_trade)
        db.commit()
        logger.info(f"✅ TRADE OPENED: {signal} @ {entry} | T: {target} | SL: {sl}")
        return True, f"Opened {signal} @ {entry}"
    except Exception as e:
        logger.error(f"open_paper_trade error: {e}")
        db.rollback()
        return False, f"Error: {e}"

def close_paper_trade(db, reason: str = "Manual Close", pnl: float = None) -> tuple[bool, str]:
    try:
        if db is None: return False, "Database not connected"
        open_trade = db.query(Trade).filter(Trade.status == "ACTIVE").first()
        if not open_trade: return False, "No open trade to close"
        spot = config.latest_prices.get("nifty", open_trade.entry)
        if pnl is None:
            pnl = (spot - open_trade.entry) if open_trade.direction == "LONG" else (open_trade.entry - spot)
        open_trade.status = "CLOSED"
        open_trade.pnl = round(pnl, 2)
        open_trade.closed_at = config.get_ist_now()
        db.commit()
        logger.info(f"🔴 TRADE CLOSED: {reason} | PnL: {pnl}")
        return True, f"Closed: {reason} | PnL: {pnl}"
    except Exception as e:
        logger.error(f"close_paper_trade error: {e}")
        db.rollback()
        return False, f"Error: {e}"

def process_auto_signal(db) -> dict:
    """CORE PRO LOGIC: हर पोल पर चलेगा और ऑटो ट्रेड करेगा"""
    try:
        if not db: return {"action": "skipped", "reason": "No DB"}
        
        # ⚠️ CRITICAL: पहले Risk चेक करो
        risk_ok, risk_msg = check_risk_limits(db)
        if not risk_ok:
            return {"action": "stopped", "reason": risk_msg}

        signal_data = config.signal_data
        if not signal_data or signal_data.get("signal") == "WAIT": return {"action": "waiting", "reason": "No clear signal"}
        current_signal = signal_data.get("signal")
        confidence = signal_data.get("confidence", 0)
        if confidence < 4: return {"action": "waiting", "reason": f"Low confidence ({confidence})"}
        spot = config.latest_prices.get("nifty")
        if not spot: return {"action": "waiting", "reason": "No spot price"}
        
        active_trade = db.query(Trade).filter(Trade.status == "ACTIVE").first()
        
        if not active_trade:
            success, msg = open_paper_trade(db, current_signal, spot)
            return {"action": "auto_opened", "success": success, "msg": msg}
            
        entry = active_trade.entry; target = active_trade.target; sl = active_trade.sl; pnl = 0
        if active_trade.direction == "LONG":
            pnl = spot - entry
            if spot >= target: s, m = close_paper_trade(db, "Target Hit ✅", pnl); return {"action": "auto_closed", "success": s, "msg": m, "pnl": round(pnl,2)}
            if spot <= sl: s, m = close_paper_trade(db, "Stop Loss Hit ❌", pnl); return {"action": "auto_closed", "success": s, "msg": m, "pnl": round(pnl,2)}
        elif active_trade.direction == "SHORT":
            pnl = entry - spot
            if spot <= target: s, m = close_paper_trade(db, "Target Hit ✅", pnl); return {"action": "auto_closed", "success": s, "msg": m, "pnl": round(pnl,2)}
            if spot >= sl: s, m = close_paper_trade(db, "Stop Loss Hit ❌", pnl); return {"action": "auto_closed", "success": s, "msg": m, "pnl": round(pnl,2)}
            
        if active_trade.direction != current_signal:
            close_res = close_paper_trade(db, f"Signal Reversed to {current_signal}", pnl)
            open_res = open_paper_trade(db, current_signal, spot)
            return {"action": "reversed", "close": close_res, "open": open_res}
            
        return {"action": "holding", "reason": "Trade active, no trigger"}
    except Exception as e:
        logger.error(f"Auto signal error: {e}")
        return {"action": "error", "reason": str(e)}
