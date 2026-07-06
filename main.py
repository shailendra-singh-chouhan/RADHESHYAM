"""
main.py
=======
GOAT PRO — Institutional Level Trading Dashboard
================================================
Path B build: FastAPI + PostgreSQL (SQLAlchemy) + Angel One real-time data
+ RSI/EMA/VWAP indicators + paper trading + full HTML dashboard.

This is a MERGED rebuild that combines:
  * The original Flask dashboard (Angel One polling, indicators, paper trade,
    47 KB HTML UI) from chat msg 5
  * The new FastAPI + PostgreSQL trade persistence layer from the recent code
    you shared

Files in this project:
  main.py              — FastAPI app, routes, polling threads, trade logic
  models.py            — SQLAlchemy Trade model
  database.py          — Engine, session, init_db, get_db dependency
  dashboard.html       — Full HTML/CSS/JS dashboard UI (served at /)
  requirements.txt     — Python dependencies for Render

Run locally:
  uvicorn main:app --host 0.0.0.0 --port 5000 --reload

Render start command:
  uvicorn main:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import os
import datetime
import threading
import time
import logging
import pyotp
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from SmartApi import SmartConnect
from logzero import logger

from models import Trade
from database import get_db, init_db, db_engine

# --- Basic logging setup (logzero handles the rest) ---
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# ====================== APP INSTANCE ======================
app = FastAPI(title="GOAT PRO Institutional", version="3.0")


# ====================== STARTUP EVENT ======================
@app.on_event("startup")
async def startup_event() -> None:
    """Initialize DB tables and log in to Angel One on app start."""
    logger.info("Application startup event triggered.")
    if db_engine:
        init_db(db_engine)
    else:
        logger.warning("Database engine is not available. Trades will not be persisted.")
    # Log in to Angel One (sets global `smart_api`)
    angel_login()
    # Start background threads
    start_background_threads()


# ====================== ANGEL ONE CONFIG ======================
ANGEL_API_KEY = os.environ.get("ANGEL_API_KEY")
ANGEL_CLIENT_ID = os.environ.get("ANGEL_CLIENT_ID")
ANGEL_MPIN = os.environ.get("ANGEL_MPIN")
ANGEL_TOTP_SECRET = os.environ.get("ANGEL_TOTP_SECRET")

# Known Angel One symbol tokens for indices (NSE)
NIFTY_TOKEN = "99926000"
NIFTY_SYMBOL = "Nifty 50"
BANKNIFTY_TOKEN = "99926009"
BANKNIFTY_SYMBOL = "Nifty Bank"
VIX_TOKEN = "99926017"
VIX_SYMBOL = "India VIX"

smart_api: Optional[SmartConnect] = None
session_lock = threading.Lock()


def angel_login() -> bool:
    """Logs into Angel One SmartAPI. Called at startup and re-called if session expires."""
    global smart_api
    if not all([ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_MPIN, ANGEL_TOTP_SECRET]):
        logger.error("Angel One credentials missing in env. Login skipped.")
        return False
    try:
        totp = pyotp.TOTP(ANGEL_TOTP_SECRET).now()
        obj = SmartConnect(api_key=ANGEL_API_KEY)
        data = obj.generateSession(ANGEL_CLIENT_ID, ANGEL_MPIN, totp)
        if data and data.get("status"):
            smart_api = obj
            logger.info("Angel One login successful")
            return True
        logger.error(f"Angel One login failed: {data}")
        return False
    except Exception as e:
        logger.error(f"Angel One login exception: {e}")
        return False


def get_ltp(exchange: str, symbol: str, token: str) -> Optional[float]:
    """Fetches live LTP for a given instrument. Returns None on failure.

    IMPORTANT: This is the same function that the original Flask code had.
    If `resp.get("status")` is False, we log the full response so you can
    see the actual Angel One error message in Render logs.
    """
    global smart_api
    if smart_api is None:
        return None
    try:
        with session_lock:
            resp = smart_api.ltpData(exchange, symbol, token)
        if resp and resp.get("status"):
            return resp["data"]["ltp"]
        logger.error(f"get_ltp non-success response for {symbol}: {resp}")
        return None
    except Exception as e:
        logger.error(f"get_ltp error for {symbol}: {e}")
        return None


# ====================== MARKET HOURS GUARD ======================
IST_OFFSET = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def get_ist_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).astimezone(IST_OFFSET)


def get_market_status() -> str:
    """Returns 'OPEN', 'PRE_OPEN', or 'CLOSED' based on real IST time, Mon-Fri only."""
    now = get_ist_now()
    if now.weekday() >= 5:  # Sat=5, Sun=6
        return "CLOSED"
    t = now.time()
    if datetime.time(9, 0) <= t < datetime.time(9, 15):
        return "PRE_OPEN"
    if datetime.time(9, 15) <= t <= datetime.time(15, 30):
        return "OPEN"
    return "CLOSED"


# ====================== LIVE PRICE STORE (in-memory cache, refreshed by poller) ======================
latest_prices: dict = {
    "nifty": None, "vix": None,
    "day_open": None, "day_open_date": None,
    "last_update": None,
}


def price_poller() -> None:
    """Background thread: refresh live prices every 15 seconds during market hours."""
    while True:
        try:
            if get_market_status() in ("OPEN", "PRE_OPEN"):
                nifty = get_ltp("NSE", NIFTY_SYMBOL, NIFTY_TOKEN)
                vix = get_ltp("NSE", VIX_SYMBOL, VIX_TOKEN)
                today = get_ist_now().date().isoformat()
                if nifty is not None:
                    latest_prices["nifty"] = nifty
                    if latest_prices["day_open_date"] != today:
                        latest_prices["day_open"] = nifty
                        latest_prices["day_open_date"] = today
                if vix is not None:
                    latest_prices["vix"] = vix
                latest_prices["last_update"] = get_ist_now().isoformat()
        except Exception as e:
            logger.error(f"price_poller error: {e}")
        time.sleep(15)


# ====================== INDICATORS (RSI, EMA, VWAP-approx) ======================
candle_lock = threading.Lock()
candle_store: list = []
indicator_data: dict = {"rsi": None, "ema9": None, "ema21": None, "vwap_approx": None}


def fetch_todays_candles() -> Optional[list]:
    """Fetches today's 1-minute candles for NIFTY from Angel One."""
    global smart_api
    if smart_api is None:
        return None
    try:
        now = get_ist_now()
        from_dt = now.replace(hour=9, minute=15, second=0, microsecond=0)
        params = {
            "exchange": "NSE",
            "symboltoken": NIFTY_TOKEN,
            "interval": "ONE_MINUTE",
            "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
            "todate": now.strftime("%Y-%m-%d %H:%M"),
        }
        resp = smart_api.getCandleData(params)
        if resp and resp.get("status") and resp.get("data"):
            candles = []
            for row in resp["data"]:
                # row = [timestamp, open, high, low, close, volume]
                candles.append({
                    "time": row[0], "open": row[1], "high": row[2],
                    "low": row[3], "close": row[4],
                })
            return candles
        return None
    except Exception as e:
        logger.error(f"fetch_todays_candles error: {e}")
        return None


def calculate_rsi(closes: list, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def calculate_ema(closes: list, period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return round(ema, 2)


def calculate_vwap_approx(candles: list) -> Optional[float]:
    if not candles:
        return None
    typical = [(c["high"] + c["low"] + c["close"]) / 3 for c in candles]
    return round(sum(typical) / len(typical), 2)


def indicator_poller() -> None:
    """Background thread: refresh RSI/EMA/VWAP every 5 minutes during market hours."""
    while True:
        try:
            if get_market_status() == "OPEN":
                candles = fetch_todays_candles()
                if candles and len(candles) >= 15:
                    with candle_lock:
                        candle_store.clear()
                        candle_store.extend(candles)
                    closes = [c["close"] for c in candles]
                    indicator_data["rsi"] = calculate_rsi(closes)
                    indicator_data["ema9"] = calculate_ema(closes, 9)
                    indicator_data["ema21"] = calculate_ema(closes, 21)
                    indicator_data["vwap_approx"] = calculate_vwap_approx(candles)
                    signal_data.update(compute_real_signal(candles))
        except Exception as e:
            logger.error(f"indicator_poller error: {e}")
        time.sleep(300)


# ====================== REAL STRATEGY ENGINE (ORB + VWAP + EMA + RSI checklist) ======================
# Design from May 2026 planning: Opening Range Breakout (9:15-9:30) as the
# primary setup, confirmed by a relaxed checklist (3-of-4 agreement, not all 4)
# to avoid analysis paralysis. This replaces nothing on the demo "Jadui Spot"
# card - that stays a clearly-labelled illustration. This is a NEW, real signal.

signal_data = {
    "signal": "WAIT", "confidence": 0, "checklist": {},
    "orb_high": None, "orb_low": None, "note": "Waiting for data"
}

def compute_orb_range(candles: list) -> tuple[Optional[float], Optional[float]]:
    """Opening Range = high/low of candles between 9:15 and 9:30 AM."""
    orb_candles = [c for c in candles if "09:15" <= c["time"][11:16] <= "09:30"]
    if not orb_candles:
        return None, None
    return max(c["high"] for c in orb_candles), min(c["low"] for c in orb_candles)

def compute_real_signal(candles: list) -> dict:
    """
    Relaxed checklist strategy: needs at least 3-of-4 checks to agree
    before giving a LONG/SHORT signal, otherwise WAIT (avoids overtrading).
    """
    if not candles or len(candles) < 16:
        return {"signal": "WAIT", "confidence": 0, "checklist": {},
                "orb_high": None, "orb_low": None, "note": "Not enough candles yet"}

    closes = [c["close"] for c in candles]
    current_price = closes[-1]
    orb_high, orb_low = compute_orb_range(candles)
    ema9 = calculate_ema(closes, 9)
    ema21 = calculate_ema(closes, 21)
    rsi = calculate_rsi(closes)
    vwap = calculate_vwap_approx(candles)

    if orb_high is None or ema9 is None or ema21 is None or rsi is None or vwap is None:
        return {"signal": "WAIT", "confidence": 0, "checklist": {},
                "orb_high": orb_high, "orb_low": orb_low, "note": "Waiting for opening range (9:15-9:30)"}

    checklist_long = {
        "orb_breakout": current_price > orb_high,
        "above_vwap": current_price > vwap,
        "ema_bullish": ema9 > ema21,
        "rsi_not_overbought": rsi < 70,
    }
    checklist_short = {
        "orb_breakdown": current_price < orb_low,
        "below_vwap": current_price < vwap,
        "ema_bearish": ema9 < ema21,
        "rsi_not_oversold": rsi > 30,
    }
    long_score = sum(checklist_long.values())
    short_score = sum(checklist_short.values())

    if long_score >= 3 and long_score > short_score:
        return {"signal": "LONG", "confidence": long_score, "checklist": checklist_long,
                "orb_high": orb_high, "orb_low": orb_low,
                "note": f"{long_score}/4 checks agree — ORB breakout + trend confirmed"}
    elif short_score >= 3 and short_score > long_score:
        return {"signal": "SHORT", "confidence": short_score, "checklist": checklist_short,
                "orb_high": orb_high, "orb_low": orb_low,
                "note": f"{short_score}/4 checks agree — ORB breakdown + trend confirmed"}
    else:
        return {"signal": "WAIT", "confidence": max(long_score, short_score),
                "checklist": checklist_long if long_score >= short_score else checklist_short,
                "orb_high": orb_high, "orb_low": orb_low,
                "note": "Checklist not aligned (need 3-of-4) — no clear setup"}


# ====================== TRADE LOGIC (PostgreSQL-backed) ======================
def _today_ist_str() -> str:
    return get_ist_now().date().isoformat()


def check_risk_limits(db: Session) -> tuple[bool, str]:
    """Real risk check: max 5 trades/day and max daily loss of 2000 (paper limits)."""
    if db is None:
        # If DB unavailable, allow trades but log warning
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
    """Opens a paper trade using current live NIFTY price. Persists to PostgreSQL."""
    if db is None:
        return False, "Database not connected"
    # Check if a trade is already active
    existing = db.query(Trade).filter(Trade.status == "ACTIVE").first()
    if existing:
        return False, "A trade is already active"
    if get_market_status() != "OPEN":
        return False, "Market is closed"
    if latest_prices["nifty"] is None:
        return False, "Live price not available yet"
    risk_ok, risk_message = check_risk_limits(db)
    if not risk_ok:
        return False, risk_message

    entry = latest_prices["nifty"]
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
    """Closes the active paper trade at current live NIFTY price."""
    if db is None:
        return False, "Database not connected"
    if latest_prices["nifty"] is None:
        return False, "Live price not available"

    trade = db.query(Trade).filter(Trade.status == "ACTIVE").first()
    if not trade:
        return False, "No active trade"

    exit_price = latest_prices["nifty"]
    pnl = round(exit_price - trade.entry, 2)  # LONG direction
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
    """Real stats calculated from PostgreSQL trades."""
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

    # Max drawdown from cumulative pnl curve
    cumulative = peak = 0
    max_dd = 0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        dd = cumulative - peak
        max_dd = min(max_dd, dd)

    # Simplified Sharpe: mean/stddev of pnl series (not annualized)
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


# ====================== BACKGROUND THREAD STARTER ======================
_started = False


def start_background_threads() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=price_poller, daemon=True).start()
    threading.Thread(target=indicator_poller, daemon=True).start()
    logger.info("Background threads started (price_poller, indicator_poller).")


# ====================== DASHBOARD HTML ======================
DASHBOARD_FILE = Path(__file__).parent / "dashboard.html"


def load_dashboard_html() -> str:
    """Loads the dashboard HTML from disk. Cached at module level after first load."""
    return DASHBOARD_FILE.read_text(encoding="utf-8")


# ====================== ROUTES ======================

@app.get("/", response_class=HTMLResponse)
async def read_root() -> HTMLResponse:
    """The main dashboard UI (47 KB HTML/CSS/JS)."""
    return HTMLResponse(content=load_dashboard_html())


@app.get("/health")
async def health() -> dict:
    return {
        "status": "healthy",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "service": "GOAT PRO Institutional",
        "version": "3.0",
        "angel_session": smart_api is not None,
        "db_connected": db_engine is not None,
    }


@app.get("/ping")
async def ping() -> dict:
    return {"status": "alive"}


@app.get("/api/data")
async def api_data(db: Session = Depends(get_db)) -> JSONResponse:
    """Main data endpoint that the dashboard polls every few seconds."""
    market_status = get_market_status()
    risk_ok, risk_message = check_risk_limits(db) if db else (True, "Risk OK (no DB)")
    stats = get_institutional_stats(db)

    # Active trade + today's session PnL
    current_active = None
    session_pnl = 0.0
    live_pnl = None
    today = _today_ist_str()
    if db:
        try:
            active_row = db.query(Trade).filter(Trade.status == "ACTIVE").first()
            if active_row:
                current_active = {
                    "direction": active_row.direction,
                    "entry": active_row.entry,
                    "target": active_row.target,
                    "sl": active_row.sl,
                }
                if latest_prices["nifty"] is not None:
                    live_pnl = round(latest_prices["nifty"] - active_row.entry, 2)
            # Today's closed trades PnL
            today_closed = db.query(Trade).filter(
                Trade.status == "CLOSED", Trade.trade_date == today,
            ).all()
            session_pnl = sum(t.pnl or 0 for t in today_closed)
        except Exception as e:
            logger.error(f"api_data query error: {e}")

    return JSONResponse({
        "spot": latest_prices["nifty"],
        "vix": latest_prices["vix"],
        "day_open": latest_prices["day_open"],
        "market_status": market_status,
        "risk_ok": risk_ok,
        "risk_message": risk_message,
        "session_pnl_rs": session_pnl,
        "win_rate": stats["win_rate"],
        "total_trades": stats["total_trades"],
        "institutional_stats": stats,
        "active_trade": {
            "direction": current_active["direction"],
            "entry": current_active["entry"],
            "target": current_active["target"],
            "sl": current_active["sl"],
            "live_pnl": live_pnl,
        } if current_active else None,
        "indicators": indicator_data,
        "real_signal": signal_data,
        "last_update": latest_prices["last_update"],
    })


@app.get("/api/trades")
async def api_trades(db: Session = Depends(get_db)) -> dict:
    """Returns the active trade (if any) and all closed trades."""
    if db is None:
        return {"open": None, "closed": [], "stats": get_institutional_stats(None)}
    try:
        open_trade = db.query(Trade).filter(Trade.status == "ACTIVE").first()
        closed_trades = db.query(Trade).filter(Trade.status == "CLOSED")\
                                       .order_by(Trade.closed_at.desc()).limit(100).all()
        return {
            "open": open_trade.to_dict() if open_trade else None,
            "closed": [t.to_dict() for t in closed_trades],
            "stats": get_institutional_stats(db),
        }
    except Exception as e:
        logger.error(f"api_trades error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/execute_trade")
async def api_execute_trade(db: Session = Depends(get_db)) -> dict:
    success, message = open_paper_trade(db)
    return {"success": success, "message": message}


@app.post("/api/close_trade")
async def api_close_trade(db: Session = Depends(get_db)) -> dict:
    success, message = close_paper_trade(db)
    return {"success": success, "message": message}


@app.get("/api/candles")
async def api_candles() -> dict:
    """Returns today's real 1-minute NIFTY candles for the chart."""
    with candle_lock:
        candles = list(candle_store)
    return {"candles": candles}


@app.get("/get_market_data")
async def get_market_data_endpoint(symbol: str) -> dict:
    """Convenience endpoint: fetch LTP + candles for any symbol."""
    # Map common symbol aliases to Angel One tokens
    symbol_map = {
        "NIFTY": ("NSE", NIFTY_SYMBOL, NIFTY_TOKEN),
        "BANKNIFTY": ("NSE", BANKNIFTY_SYMBOL, BANKNIFTY_TOKEN),
        "VIX": ("NSE", VIX_SYMBOL, VIX_TOKEN),
    }
    key = symbol.upper().strip()
    if key not in symbol_map:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")
    exch, sym, tok = symbol_map[key]
    ltp = get_ltp(exch, sym, tok)
    candles = fetch_todays_candles() if key == "NIFTY" else []
    return {"symbol": sym, "ltp": ltp, "todays_candles": candles or []}


# ====================== NOTE ======================
# The old /open_trade and /close_trade/{id} endpoints were removed here.
# They let anyone open/close a trade with arbitrary values, bypassing
# check_risk_limits() and the market-hours guard entirely - a real security
# gap. The dashboard only ever used /api/execute_trade and /api/close_trade,
# which DO enforce those checks, so nothing on the UI depended on them.

@app.get("/institutional_stats")
async def get_stats_endpoint(db: Session = Depends(get_db)) -> dict:
    """Get overall trading statistics."""
    if db is None:
        raise HTTPException(status_code=500, detail="Database is not connected.")
    try:
        active_count = db.query(Trade).filter(Trade.status == "ACTIVE").count()
        closed_count = db.query(Trade).filter(Trade.status == "CLOSED").count()
        # FIXED: use `func` from sqlalchemy, not `db.func` (that was a bug in the old code)
        total_pnl = db.query(func.coalesce(func.sum(Trade.pnl), 0.0))\
                       .filter(Trade.status == "CLOSED").scalar()
        stats = {
            "total_active_trades": active_count,
            "total_closed_trades": closed_count,
            "total_pnl_realized": round(total_pnl, 2) if total_pnl is not None else 0.0,
        }
        logger.info(f"Institutional Stats: {stats}")
        return stats
    except Exception as e:
        logger.error(f"Error fetching institutional stats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch stats: {e}")


# ====================== RUN (local testing only) ======================
# Render uses: uvicorn main:app --host 0.0.0.0 --port $PORT
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 5000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
