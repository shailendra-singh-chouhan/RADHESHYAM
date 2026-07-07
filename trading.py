import math
from logzero import logger
from models import Trade
from datetime import datetime

def get_options_contract(spot_price: float, signal: str, index: str = "NIFTY", strategy: str = "ATM") -> dict:
    """
    Dynamically selects the exact Option Strike Price (CE/PE) based on live spot price.
    """
    if spot_price is None or signal not in ["LONG", "SHORT"]:
        return None

    step_size = 50 if index == "NIFTY" else 100
    atm_strike = round(spot_price / step_size) * step_size
    selected_strike = atm_strike
    opt_type = ""

    if signal == "LONG":
        opt_type = "CE"
        if strategy == "ITM":
            selected_strike = atm_strike - step_size
    elif signal == "SHORT":
        opt_type = "PE"
        if strategy == "ITM":
            selected_strike = atm_strike + step_size

    contract_symbol = f"{index} {selected_strike} {opt_type}"
    logger.info(f"🎯 STRIKE SELECTED: Spot was {spot_price}, chose {contract_symbol}")

    return {
        "index": index,
        "strike": selected_strike,
        "option_type": opt_type,
        "symbol": contract_symbol,
        "premium_estimate": "Pending Live Fetch"
    }

def execute_trade(signal: str, spot_price: float) -> dict:
    """
    Executes the trade by selecting the right option contract and setting Target/SL.
    """
    contract = get_options_contract(spot_price, signal)
    if not contract:
        return None

    logger.info(f"🚀 EXECUTING {signal} TRADE on {contract['symbol']}")

    entry_price = spot_price
    if signal == "LONG":
        target = spot_price + 50
        sl = spot_price - 25
    else:
        target = spot_price - 50
        sl = spot_price + 25

    trade_details = {
        "direction": signal,
        "entry": entry_price,
        "target": target,
        "sl": sl,
        "contract": contract,
        "live_pnl": 0.0,
        "status": "OPEN"
    }

    return trade_details

def check_risk_limits(db) -> tuple[bool, str]:
    """Risk limits check — Phase 3 में असली logic डालेंगे।"""
    try:
        return True, "Risk OK"
    except Exception as e:
        logger.error(f"Risk check error: {e}")
        return True, f"Risk check skipped: {e}"

def get_institutional_stats(db) -> dict:
    """Institutional stats — routes.py में win_rate और total_trades use होते हैं।"""
    try:
        if db is None:
            return {
                "fii_long": 0, "fii_short": 0, "fii_net": 0,
                "dii_long": 0, "dii_short": 0, "dii_net": 0,
                "win_rate": 0.0, "total_trades": 0,
                "status": "No DB"
            }

        total = db.query(Trade).filter(Trade.status == "CLOSED").count()
        wins = db.query(Trade).filter(Trade.status == "CLOSED", Trade.pnl > 0).count()
        win_rate = round((wins / total * 100), 1) if total > 0 else 0.0

        return {
            "fii_long": 0, "fii_short": 0, "fii_net": 0,
            "dii_long": 0, "dii_short": 0, "dii_net": 0,
            "win_rate": win_rate,
            "total_trades": total,
            "status": "Live"
        }
    except Exception as e:
        logger.error(f"Institutional stats error: {e}")
        return {
            "fii_long": 0, "fii_short": 0, "fii_net": 0,
            "dii_long": 0, "dii_short": 0, "dii_net": 0,
            "win_rate": 0.0, "total_trades": 0,
            "status": f"Error: {e}"
        }

def open_paper_trade(db) -> tuple[bool, str]:
    """Paper trade खोलता है — Phase 3 में signal से auto-connect होगा।"""
    try:
        if db is None:
            return False, "Database not connected"

        already_open = db.query(Trade).filter(Trade.status == "ACTIVE").first()
        if already_open:
            return False, "Already have an open trade"

        return False, "No active signal — use manual signal first"
    except Exception as e:
        logger.error(f"open_paper_trade error: {e}")
        return False, f"Error: {e}"

def close_paper_trade(db) -> tuple[bool, str]:
    """Paper trade बंद करता है — Phase 3 में SL/Target पर auto-close होगा।"""
    try:
        if db is None:
            return False, "Database not connected"

        open_trade = db.query(Trade).filter(Trade.status == "ACTIVE").first()
        if not open_trade:
            return False, "No open trade to close"

        return False, "Manual close not ready yet — coming in Phase 3"
    except Exception as e:
        logger.error(f"close_paper_trade error: {e}")
        return False, f"Error: {e}"
