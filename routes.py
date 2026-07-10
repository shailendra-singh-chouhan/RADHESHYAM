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
            "value": round(st_val.get("value", 0),
