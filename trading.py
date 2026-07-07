import math
from logzero import logger

def get_options_contract(spot_price: float, signal: str, index: str = "NIFTY", strategy: str = "ATM") -> dict:
    """
    Dynamically selects the exact Option Strike Price (CE/PE) based on live spot price.
    """
    if spot_price is None or signal not in ["LONG", "SHORT"]:
        return None

    # Nifty step size is 50, BankNifty is 100
    step_size = 50 if index == "NIFTY" else 100
    
    # Calculate At-The-Money (ATM) Strike
    atm_strike = round(spot_price / step_size) * step_size

    # Strike Selection Logic
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
    
    # Paper Trading Risk Logic (Target = 50 pts, StopLoss = 25 pts on Spot)
    entry_price = spot_price
    if signal == "LONG":
        target = spot_price + 50
        sl = spot_price - 25
    else: # SHORT
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
    """
    Risk limits check करता है।
    Phase 3 में यहाँ असली DB queries डालेंगे।
    """
    try:
        return True, "Risk OK"
    except Exception as e:
        logger.error(f"Risk check error: {e}")
        return True, f"Risk check skipped: {e}"

def get_institutional_stats(db) -> dict:
    """
    Institutional data (FII/DII) return करता है।
    Phase 4 में असली data लगाएंगे।
    """
    try:
        return {
            "fii_long": 0,
            "fii_short": 0,
            "fii_net": 0,
            "dii_long": 0,
            "dii_short": 0,
            "dii_net": 0,
            "status": "Coming Soon"
        }
    except Exception as e:
        logger.error(f"Institutional stats error: {e}")
        return {
            "fii_long": 0, "fii_short": 0, "fii_net": 0,
            "dii_long": 0, "dii_short": 0, "dii_net": 0,
            "status": f"Error: {e}"
        }
