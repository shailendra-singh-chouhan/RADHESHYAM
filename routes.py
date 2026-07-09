
"""
routes.py — FastAPI API endpoints for GOAT PRO dashboard
Phase 3.B1 fixed: matches actual trading.py signatures
                  (open_paper_trade / close_paper_trade with real args)
"""
import logging
from datetime import datetime, time as dt_time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

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

logger = logging.getLogger(__name__)
router = APIRouter()


# ════════════════════════════════════════════════════════
# REQUEST / RESPONSE MODELS
# ════════════════════════════════════════════════════════
class ExecuteRequest(BaseModel):
    direction: Optional[str] = None  # "LONG" or "SHORT" — optional, falls back to signal
    entry: Optional[float] = None    # optional, falls back to current spot


class CloseRequest(BaseModel):
    reason: Optional[str] = "Manual Close"


# ════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════
def _market_status() -> str:
    """Check if NSE is currently open (9:15 AM – 3:30 PM IST)."""
    now = datetime.now()
    t = now.time()
    if now.weekday() >= 5:
        return "CLOSED"
    if dt_time(9, 15) <= t <= dt_time(15, 30):
        return "OPEN"
    if dt_time(9, 0) <= t < dt_time(9, 15):
        return "PRE_OPEN"
    return "CLOSED"


def _risk_check(db: Session) -> dict:
    """Check if it's safe to trade (daily loss limit, max trades)."""
    today = datetime.now().date()
    today_trades = db.query(Trade).filter(Trade.trade_date == today).all()
    total_trades = len(today_trades)
    day_pnl = sum(t.pnl or 0 for t in today_trades)
    max_loss = config.MAX_DAILY_LOSS if hasattr(config, "MAX_DAILY_LOSS") else -2000
    max_trades = config.MAX_DAILY_TRADES if hasattr(config, "MAX_DAILY_TRADES") else 5
    ok = day_pnl > max_loss and total_trades < max_trades
    msg = f"Risk OK (Day PnL: ₹{day_pnl:.2f})" if ok else (
        f"Risk BLOCKED (Day PnL: ₹{day_pnl:.2f}, Trades: {total_trades})"
    )
    return {"risk_ok": ok, "risk_message": msg}


# ════════════════════════════════════════════════════════
# MAIN DASHBOARD ENDPOINT
# ════════════════════════════════════════════════════════
@router.get("/api/data")
def get_dashboard_data(db: Session = Depends(get_db)):
    """Main dashboard endpoint — returns everything the frontend needs."""
    client = get_angel_client()
    status = _market_status()
    risk = _risk_check(db)

    # ── Spot prices ──
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

    # ── Indicators (from candles) ──
    from indicators import (
        calculate_rsi,
        calculate_ema,
        calculate_vwap,
        calculate_macd,
        calculate_supertrend,
    )

    closes = [c["close"] for c in candles] if candles else []
    highs = [c["high"] for c in candles] if candles else []
    lows = [c["low"] for c in candles] if candles else []

    rsi_val = calculate_rsi(closes, 14) if len(closes) >= 14 else None
    ema9_val = calculate_ema(closes, 9) if len(closes) >= 9 else None
    ema21_val = calculate_ema(closes, 21) if len(closes) >= 21 else None
    vwap_val = calculate_vwap(candles) if candles else None
    macd_val = calculate_macd(closes) if len(closes) >= 26 else None
    st_val = (
        calculate_supertrend(highs, lows, closes, 10, 3)
        if len(closes) >= 10
        else None
    )

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

    # ── Signal ──
    real_signal = generate_signal(candles, spot) if candles and spot > 0 else {
        "signal": "WAIT",
        "confidence": 0,
        "checklist": {},
        "orb_high": 0,
        "orb_low": 0,
        "note": "No candle data",
    }

    # ── OI Data ──
    oi_data = get_oi_data("NIFTY")

    # ── Options Contract ──
    sig_direction = real_signal.get("signal", "WAIT")
    max_pain = oi_data.get("max_pain", 0) or int(spot / 50) * 50
    strike = max_pain
    option_type = "PE" if sig_direction == "SHORT" else (
        "CE" if sig_direction == "LONG" else "PE"
    )

    options_contract = {
        "index": "NIFTY",
        "strike": strike,
        "option_type": option_type,
        "symbol": f"NIFTY {strike} {option_type}",
        "premium_estimate": "Opens at 9:15 AM",
        "live_premium": None,
        "live_premium_source": "UNAVAILABLE",
        "live_premium_expiry": None,
        "live_premium_lotsize": None,
        "full_details": None,
    }

    if status == "OPEN" and spot > 0:
        try:
            ltp_result = client.get_option_ltp("NIFTY", strike, option_type)
            if ltp_result and ltp_result.get("ltp") is not None:
                ltp_val = ltp_result["ltp"]
                options_contract["premium_estimate"] = f"₹{ltp_val:.2f}"
                options_contract["live_premium"] = round(ltp_val, 2)
                options_contract["live_premium_source"] = ltp_result.get(
                    "source", "ANGEL_ONE_LIVE"
                )
                options_contract["live_premium_expiry"] = ltp_result.get("expiry")
                options_contract["live_premium_lotsize"] = ltp_result.get("lotsize")
                options_contract["full_details"] = ltp_result
            else:
                source = ltp_result.get("source", "UNAVAILABLE") if ltp_result else "UNAVAILABLE"
                options_contract["premium_estimate"] = "Contract Not Found"
                options_contract["live_premium_source"] = source
        except Exception as e:
            logger.error("get_option_ltp call failed: %s", e)
            options_contract["live_premium_source"] = f"ERROR: {e}"
    elif status != "OPEN":
        options_contract["premium_estimate"] = "Market Closed"
        options_contract["live_premium_source"] = "MARKET_CLOSED"

    # ── Greeks ──
    expiry_str = options_contract.get("live_premium_expiry")
    days_to_exp = 5.0
    if expiry_str:
        try:
            exp_dt = datetime.strptime(expiry_str, "%d-%b-%Y")
            days_to_exp = max((exp_dt - datetime.now()).total_seconds() / 86400, 0.5)
        except Exception:
            pass

    greeks_data = calculate_greeks(
        spot=spot,
        strike=strike,
        option_type=option_type,
        days_to_expiry=days_to_exp,
        iv_percent=float(oi_data.get("pcr", 13) * 11) if oi_data.get("pcr") else 13.0,
    )

    # ── Institutional stats ──
    inst_stats = fetch_institutional_stats()

    # ── Global markets ──
    global_data = fetch_global_markets()

    # ── Stocks ──
    stock_symbols = ["HDFC", "SBI", "PNB", "YES", "INFY"]
    stocks = {}
    for sym in stock_symbols:
        try:
            ltp = client.get_ltp("NSE", sym)
            if ltp and ltp > 0:
                stocks[sym] = {
                    "ltp": round(ltp, 2),
                    "open": round(ltp * 0.998, 2),
                    "high": round(ltp * 1.002, 2),
                    "low": round(ltp * 0.997, 2),
                    "last_update": datetime.now().isoformat(),
                }
        except Exception:
            pass

    # ── Active trade (FIXED: trading.py uses status="ACTIVE", not "OPEN") ──
    active_trade = None
    open_trades = db.query(Trade).filter(Trade.status == "ACTIVE").order_by(Trade.id.desc()).first()
    if open_trades:
        live_pnl = 0
        if open_trades.direction == "LONG":
            live_pnl = (spot - open_trades.entry) * 75
        elif open_trades.direction == "SHORT":
            live_pnl = (open_trades.entry - spot) * 75
        active_trade = {
            "direction": open_trades.direction,
            "entry": round(open_trades.entry, 2),
            "target": round(open_trades.target, 2) if open_trades.target else None,
            "sl": round(open_trades.sl, 2) if open_trades.sl else None,
            "live_pnl": round(live_pnl, 2),
        }

    # ── Trade stats ──
    all_trades = db.query(Trade).all()
    total_trades = len(all_trades)
    wins = sum(1 for t in all_trades if (t.pnl or 0) > 0)
    win_rate = round((wins / total_trades * 100), 1) if total_trades > 0 else 0

    today = datetime.now().date()
    today_trades = db.query(Trade).filter(Trade.trade_date == today).all()
    session_pnl = sum(t.pnl or 0 for t in today_trades)

    # ── Auto-trade status ──
    auto_enabled = getattr(config, "AUTO_TRADE_ENABLED", False)
    auto_info = {
        "action": "disabled",
        "reason": (
            "Auto-trade is off (safety default) — "
            "set config.AUTO_TRADE_ENABLED=True to turn on"
        ),
    }
    if auto_enabled:
        auto_info = {
            "action": "enabled",
            "reason": "Auto-trade is ON — paper trades will execute on signals",
        }

    # ── Alerts (from OI) ──
    alerts = []
    if spot > 0 and oi_data.get("max_pain"):
        mp = oi_data["max_pain"]
        distance = abs(spot - mp)
        if distance < 50:
            alerts.append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "badge": "INFO",
                "msg": f"Price near Max Pain {mp} (real OI level)",
                "px": round(spot, 2),
            })

    # ── Build final response ──
    return {
        "spot": round(spot, 2) if spot else 0,
        "banknifty": round(banknifty, 2) if banknifty else 0,
        "finnifty": round(finnifty, 2) if finnifty else 0,
        "sensex": round(sensex, 2) if sensex else 0,
        "crudeoil": round(crudeoil, 2) if crudeoil else 0,
        "gold": round(gold, 2) if gold else 0,
        "silver": round(silver, 2) if silver else 0,
        "usdinr": round(usdinr, 2) if usdinr else 0,
        "midcap": round(midcap, 2) if midcap else 0,
        "vix": round(vix, 2) if vix else 0,
        "day_open": round(day_open, 2) if day_open else 0,
        "market_status": status,
        "risk_ok": risk["risk_ok"],
        "risk_message": risk["risk_message"],
        "auto_trade": auto_info,
        "session_pnl_rs": round(session_pnl, 2),
        "win_rate": win_rate,
        "total_trades": total_trades,
        "institutional_stats": inst_stats,
        "options_contract": options_contract,
        "oi_data": oi_data,
        "greeks_data": greeks_data,
        "alerts": alerts,
        "stocks": stocks,
        "news_feed": [],
        "indicators": indicators,
        "global": global_data,
        "active_trade": active_trade,
        "real_signal": real_signal,
        "market_alerts": alerts,
        "last_update": datetime.now().isoformat() + "+05:30",
    }


# ════════════════════════════════════════════════════════
# EXECUTE / CLOSE TRADE ENDPOINTS
# ════════════════════════════════════════════════════════
@router.post("/api/execute")
def execute_trade_endpoint(req: ExecuteRequest, db: Session = Depends(get_db)):
    """Manually open a paper trade. Uses trading.open_paper_trade()."""
    try:
        # trading.open_paper_trade auto-calculates target/sl via ATR
        # and pulls signal + spot from state_manager if not provided.
        success, msg = open_paper_trade(db, signal=req.direction, spot=req.entry)
        if not success:
            raise HTTPException(status_code=400, detail=msg)
        return {"success": True, "message": msg}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("execute_trade_endpoint failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/close")
def close_trade_endpoint(req: CloseRequest, db: Session = Depends(get_db)):
    """Close the currently active paper trade."""
    try:
        success, msg = close_paper_trade(db, reason=req.reason or "Manual Close")
        if not success:
            raise HTTPException(status_code=400, detail=msg)
        return {"success": True, "message": msg}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("close_trade_endpoint failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.api_route("/health", methods=["GET", "HEAD"])
def health():
    """Health check endpoint for Render."""
    return {"status": "ok", "time": datetime.now().isoformat()}
