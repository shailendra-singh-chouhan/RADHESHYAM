"""
API Routes — Dashboard, Data, Trades, Execute, Close
"""

import os
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Trade
from strategy import shared_state
import trading
from config import AUTO_TRADE_ENABLED

router = APIRouter()

DASHBOARD_FILE = os.path.join(os.path.dirname(__file__), "dashboard.html")


def load_dashboard_html():
    try:
        with open(DASHBOARD_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "<h1>dashboard.html not found</h1>"


@router.get("/", response_class=HTMLResponse)
def serve_dashboard():
    return load_dashboard_html()


@router.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {"status": "ok"}


@router.api_route("/api/data", methods=["GET", "HEAD"])
def get_dashboard_data(request: Request, db: Session = Depends(get_db)):
    data = dict(shared_state)

    spot = data.get("spot", 0)

    # Active trade PnL — direction aware
    active = data.get("active_trade")
    if active and active.get("direction") and spot > 0:
        entry = active.get("entry", 0)
        direction = active["direction"]
        if direction == "SHORT":
            active["live_pnl"] = round(entry - spot, 2)
        else:
            active["live_pnl"] = round(spot - entry, 2)

    # Source labels
    greeks = data.get("greeks", {})
    if greeks and "source" not in greeks:
        greeks["source"] = "BS_APPROX"
    data["greeks"] = greeks

    oi = data.get("oi_data", {})
    if oi and "source" not in oi:
        oi["source"] = "FALLBACK_SPOT_ONLY"
    data["oi_data"] = oi

    gl = data.get("global", {})
    if gl and "source" not in gl:
        gl["source"] = "YAHOO_FINANCE"
    data["global"] = gl

    inst = data.get("institutional_stats", {})
    if inst and "status" not in inst:
        inst["status"] = "Live"
    data["institutional_stats"] = inst

    # Auto-trade status
    data["auto_trade"] = {
        "action": "enabled" if AUTO_TRADE_ENABLED else "disabled",
        "reason": (
            "Auto-trade active"
            if AUTO_TRADE_ENABLED
            else "Auto-trade is off (safety default) — set config.AUTO_TRADE_ENABLED=True to turn on"
        ),
    }

    # Session PnL
    trades = db.query(Trade).filter(Trade.status == "CLOSED").all()
    session_pnl = sum(t.pnl or 0 for t in trades)
    data["session_pnl_rs"] = round(session_pnl, 2)

    # Win rate
    closed = db.query(Trade).filter(Trade.status == "CLOSED").all()
    if closed:
        wins = sum(1 for t in closed if (t.pnl or 0) > 0)
        data["win_rate"] = round(wins / len(closed) * 100, 1)
        data["total_trades"] = len(closed)

    # Risk check
    if session_pnl < -5000:
        data["risk_ok"] = False
        data["risk_message"] = f"Risk limit hit! Day PnL: ₹{session_pnl}"
    else:
        data["risk_ok"] = True
        data["risk_message"] = f"Risk OK (Day PnL: ₹{session_pnl:.2f})"

    return data


@router.get("/api/trades")
def get_trades(db: Session = Depends(get_db)):
    trades = db.query(Trade).order_by(Trade.id.desc()).limit(50).all()
    result = []
    for t in trades:
        result.append({
            "id": t.id,
            "direction": t.direction,
            "entry": t.entry_price,
            "exit": t.exit_price,
            "pnl": t.pnl,
            "status": t.status,
            "signal": t.signal_type,
            "timestamp": t.trade_date.isoformat() if t.trade_date else None,
        })
    return {"trades": result}


@router.post("/api/execute_trade")
def execute_trade(request: Request, db: Session = Depends(get_db)):
    body = request.json()
    signal = body.get("signal", "WAIT")

    spot = shared_state.get("spot", 0)
    if not spot:
        return {"error": "No spot price available"}

    trade = trading.open_paper_trade(db, signal, spot)
    if trade:
        return {"status": "ok", "trade_id": trade.id, "direction": trade.direction, "entry": trade.entry_price}
    return {"error": "Cannot open trade — check risk limits or existing active trade"}


@router.post("/api/close_trade")
def close_trade(request: Request, db: Session = Depends(get_db)):
    spot = shared_state.get("spot", 0)
    if not spot:
        return {"error": "No spot price available"}

    trade = trading.close_paper_trade(db, spot)
    if trade:
        return {"status": "ok", "pnl": trade.pnl, "exit": trade.exit_price}
    return {"error": "No active trade to close"}
