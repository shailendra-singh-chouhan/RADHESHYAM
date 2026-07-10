"""
routes.py — FastAPI API endpoints for GOAT PRO dashboard
Updated with Kill-Switch integration and Analytics endpoints.
"""
import logging
from datetime import datetime, time as dt_time
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

import config
from database import get_db
from models import Trade
from angel_client import get_angel_client
from strategy import (
    generate_signal,
    get_oi_data,
    calculate_greeks,
    fetch_institutional_stats,
    fetch_global_markets,
)
from trading import open_paper_trade, close_paper_trade
import auto_execute

logger = logging.getLogger(__name__)
router = APIRouter()


# ════════════════════════════════════════════════════════
# REQUEST / RESPONSE MODELS
# ════════════════════════════════════════════════════════
class ExecuteRequest(BaseModel):
    direction: Optional[str] = None
    entry: Optional[float] = None


class CloseRequest(BaseModel):
    reason: Optional[str] = "Manual Close"


# ════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════
def _market_status() -> str:
    now = datetime.now()
    t = now.time()
    if now.weekday() >= 5:
        return "CLOSED"
    if dt_time(9, 15) <= t <= dt_time(15, 30):
        return "OPEN"
    if dt_time(9, 0) <= t < dt_time(9, 15):
        return "PRE_OPEN"
    return "CLOSED"


# ════════════════════════════════════════════════════════
# MAIN DASHBOARD ENDPOINT
# ════════════════════════════════════════════════════════
@router.get("/api/data")
def get_dashboard_data(db: Session = Depends(get_db)):
    client = get_angel_client()
    status = _market_status()
    
    # Delegate risk check to auto_execute (Kill-Switch)
    risk_ok, risk_msg = auto_execute.enforce_risk_guardrail(db)

    spot = client.get_ltp("NSE", "NIFTY") or 0
    banknifty = client.get_ltp("NSE", "BANKNIFTY") or 0
    finnifty = client.get_ltp("NSE", "FINNIFTY") or 0
    sensex = client.get_ltp("BSE", "SENSEX") or 0
    crudeoil = client.get_ltp("MCX", "CRUDEOIL") or 0
    gold = client.get_ltp("MCX", "GOLD") or 0
    silver = client.get_ltp("MCX", "SILVER") or 0
    usdinr = client.get_ltp("CDS", "USDINR") or 0
    midcap = client.get_ltp("NSE", "NIFTY MIDCAP 100") or 0
    vix = client.get_ltp("NSE", "INDIA VIX") or 0

    day_open = spot
    candles = client.get_candle_data("NSE", "NIFTY", "FIFTEEN_MINUTE", days=1)
    if candles:
        day_open = candles[0]["open"]

    from indicators import (
        calculate_rsi,
        calculate_ema,
        calculate_vwap_approx,
        calculate_macd,
        calculate_supertrend,
    )

    closes = [c["close"] for c in candles] if candles else []
    rsi_val = calculate_rsi(closes, 14) if len(closes) >= 14 else None
    ema9_val = calculate_ema(closes, 9) if len(closes) >= 9 else None
    ema21_val = calculate_ema(closes, 21) if len(closes) >= 21 else None
    vwap_val = calculate_vwap_approx(candles) if candles else None
    macd_val = calculate_macd(closes) if len(closes) >= 26 else None
    st_val = calculate_supertrend(candles, 10) if len(candles) >= 10 else None

    indicators = {
        "rsi": round(rsi_val, 1) if rsi_val else 0,
        "ema9": round(ema9_val, 2) if ema9_val else 0,
        "ema21": round(ema21_val, 2) if ema21_val else 0,
        "vwap_approx": round(vwap_val, 2) if vwap_val else 0,
        "macd": {
            "macd": round(macd_val["macd"], 2) if macd_val and macd_val.get("macd") else 0,
            "signal": round(macd_val["signal"], 2) if macd_val and macd_val.get("signal") else 0,
        },
        "supertrend": {
            "trend": st_val.get("trend", "WAIT") if st_val else "WAIT",
            "value": round(st_val.get("value", 0), 2) if st_val else 0,
        },
    }

    real_signal = (generate_signal(candles, spot) if candles and spot > 0 else None) or {
        "signal": "WAIT",
        "confidence": 0,
        "checklist": {},
        "orb_high": 0,
        "orb_low": 0,
        "note": "No candle data",
    }

    oi_data = get_oi_data("NIFTY") or {}
    max_pain = oi_data.get("max_pain", 0) or int(spot / 50) * 50
    strike = max_pain
    option_type = "PE" if real_signal.get("signal") == "SHORT" else "CE"

    options_contract = {"symbol": f"NIFTY {strike} {option_type}", "premium_estimate": "---"}
    if status == "OPEN" and spot > 0:
        try:
            ltp_result = client.get_option_ltp("NIFTY", strike, option_type)
            if ltp_result:
                options_contract["premium_estimate"] = f"₹{ltp_result['ltp']:.2f}"
        except: pass

    greeks_data = calculate_greeks(spot=spot, strike=strike, option_type=option_type, days_to_expiry=5, iv_percent=15)
    inst_stats = fetch_institutional_stats() or {}
    global_data = fetch_global_markets() or {}

    stocks = {}
    for sym in ["HDFC", "SBI", "PNB", "YES", "INFY"]:
        try:
            ltp = client.get_ltp("NSE", sym)
            if ltp: stocks[sym] = {"ltp": round(ltp, 2)}
        except: pass

    active_trade = None
    open_trade = db.query(Trade).filter(Trade.status == "ACTIVE").order_by(Trade.id.desc()).first()
    if open_trade:
        pnl = (spot - open_trade.entry) * 75 if open_trade.direction == "LONG" else (open_trade.entry - spot) * 75
        active_trade = {"direction": open_trade.direction, "entry": open_trade.entry, "live_pnl": round(pnl, 2)}

    today_str = datetime.now().date().isoformat()
    today_trades = db.query(Trade).filter(Trade.trade_date == today_str).all()
    session_pnl = sum(t.pnl or 0 for t in today_trades)

    return {
        "spot": round(spot, 2), "banknifty": round(banknifty, 2), "finnifty": round(finnifty, 2),
        "market_status": status, "risk_ok": risk_ok, "risk_message": risk_msg,
        "session_pnl_rs": round(session_pnl, 2), "indicators": indicators,
        "real_signal": real_signal, "active_trade": active_trade,
        "institutional_stats": inst_stats, "oi_data": oi_data, "greeks_data": greeks_data,
        "stocks": stocks, "global": global_data, "last_update": datetime.now().isoformat()
    }


# ════════════════════════════════════════════════════════
# ANALYTICS & HISTORY ENDPOINTS
# ════════════════════════════════════════════════════════
@router.get("/api/stats")
def get_stats(db: Session = Depends(get_db)):
    closed = db.query(Trade).filter(Trade.status == "CLOSED").all()
    total = len(closed)
    wins = sum(1 for t in closed if (t.pnl or 0) > 0)
    acc_pnl = sum(t.pnl or 0 for t in closed)
    
    today_str = datetime.now().date().isoformat()
    today_trades = db.query(Trade).filter(Trade.trade_date == today_str).all()
    day_pnl = sum(t.pnl or 0 for t in today_trades)

    return {
        "total_trades": total,
        "win_rate": round(wins/total*100, 1) if total > 0 else 0,
        "accumulated_pnl": round(acc_pnl, 2),
        "day_pnl": round(day_pnl, 2),
        "kill_switch": auto_execute.kill_switch_status()
    }

@router.get("/api/history")
def get_history(limit: int = 25, db: Session = Depends(get_db)):
    trades = db.query(Trade).order_by(Trade.id.desc()).limit(limit).all()
    return [{
        "id": t.id, "date": t.trade_date, "symbol": "NIFTY", "direction": t.direction,
        "entry": t.entry, "exit": t.exit_price, "pnl": t.pnl, "status": t.status
    } for t in trades]

@router.get("/api/kill_switch")
def get_kill_switch():
    return auto_execute.kill_switch_status()


# ════════════════════════════════════════════════════════
# EXECUTE / CLOSE TRADE ENDPOINTS (Aligned with Frontend)
# ════════════════════════════════════════════════════════
@router.post("/api/execute_trade")
def execute_trade_endpoint(db: Session = Depends(get_db)):
    # Check Kill-Switch first
    ok, msg = auto_execute.enforce_risk_guardrail(db)
    if not ok:
        raise HTTPException(status_code=423, detail=msg)
        
    success, msg = open_paper_trade(db)
    return {"success": success, "message": msg}

@router.post("/api/close_trade")
def close_trade_endpoint(db: Session = Depends(get_db)):
    success, msg = close_paper_trade(db)
    return {"success": success, "message": msg}

@router.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok", "time": datetime.now().isoformat()}
