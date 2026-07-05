# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
GOAT PRO - Institutional Level Dashboard
Single File Complete Code
FIX (July 2026): Live Angel One data + market hours guard + real stats
"""

import os
import datetime
import pyotp
import threading
import time
from flask import Flask, jsonify, render_template_string
from SmartApi import SmartConnect
from logzero import logger

app = Flask(__name__)

# ====================== ANGEL ONE CONFIG ======================

ANGEL_API_KEY = os.environ.get("ANGEL_API_KEY")
ANGEL_CLIENT_ID = os.environ.get("ANGEL_CLIENT_ID")
ANGEL_MPIN = os.environ.get("ANGEL_MPIN")
ANGEL_TOTP_SECRET = os.environ.get("ANGEL_TOTP_SECRET")

# Known Angel One symbol tokens for indices (NSE)
NIFTY_TOKEN = "99926000"
NIFTY_SYMBOL = "Nifty 50"
VIX_TOKEN = "99926017"
VIX_SYMBOL = "India VIX"

smart_api = None
session_lock = threading.Lock()

def angel_login():
    """Logs into Angel One SmartAPI. Called at startup and re-called if session expires."""
    global smart_api
    try:
        totp = pyotp.TOTP(ANGEL_TOTP_SECRET).now()
        obj = SmartConnect(api_key=ANGEL_API_KEY)
        data = obj.generateSession(ANGEL_CLIENT_ID, ANGEL_MPIN, totp)
        if data.get("status"):
            smart_api = obj
            logger.info("Angel One login successful")
            return True
        else:
            logger.error(f"Angel One login failed: {data}")
            return False
    except Exception as e:
        logger.error(f"Angel One login exception: {e}")
        return False

def get_ltp(exchange, symbol, token):
    """Fetches live LTP for a given instrument. Returns None on failure."""
    global smart_api
    if smart_api is None:
        return None
    try:
        with session_lock:
            resp = smart_api.ltpData(exchange, symbol, token)
        if resp and resp.get("status"):
            return resp["data"]["ltp"]
        return None
    except Exception as e:
        logger.error(f"get_ltp error for {symbol}: {e}")
        return None

# ====================== MARKET HOURS GUARD ======================

IST_OFFSET = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def get_ist_now():
    return datetime.datetime.now(datetime.timezone.utc).astimezone(IST_OFFSET)

def get_market_status():
    """Returns 'OPEN', 'PRE_OPEN', or 'CLOSED' based on real IST time, Mon-Fri only."""
    now = get_ist_now()
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return "CLOSED"
    current_time = now.time()
    market_open = datetime.time(9, 15)
    market_close = datetime.time(15, 30)
    pre_open_start = datetime.time(9, 0)

    if pre_open_start <= current_time < market_open:
        return "PRE_OPEN"
    elif market_open <= current_time <= market_close:
        return "OPEN"
    else:
        return "CLOSED"

# ====================== IN-MEMORY PAPER TRADE STORE ======================
# NOTE: This resets on restart, same limitation as before (SQLite in /tmp).
# Migrating to PostgreSQL (DATABASE_URL already available) is the next task.

trades_lock = threading.Lock()
closed_trades = []   # list of dicts: {entry, exit, pnl, direction, timestamp}
active_trade = None   # dict or None

def check_risk_limits():
    """Real risk check: max 5 trades/day and max daily loss of 2000 (paper limits)."""
    today = get_ist_now().date()
    with trades_lock:
        todays_trades = [t for t in closed_trades if t["timestamp"].date() == today]
        todays_pnl = sum(t["pnl"] for t in todays_trades)

    if len(todays_trades) >= 5:
        return False, "Daily trade limit (5) reached"
    if todays_pnl <= -2000:
        return False, "Daily loss limit (-2000) hit"
    return True, "Risk OK"

def get_institutional_stats():
    """Real stats calculated from actual closed paper trades."""
    with trades_lock:
        trades = list(closed_trades)

    if not trades:
        return {
            "sharpe_ratio": 0,
            "max_drawdown": 0,
            "expectancy": 0,
            "win_rate": 0,
            "total_trades": 0
        }

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    win_rate = round((len(wins) / len(pnls)) * 100, 1)
    expectancy = round(sum(pnls) / len(pnls), 1)

    # Simple max drawdown from cumulative pnl curve
    cumulative = 0
    peak = 0
    max_dd = 0
    for p in pnls:
        cumulative += p
        peak = max(peak, cumulative)
        dd = cumulative - peak
        max_dd = min(max_dd, dd)

    # Simplified Sharpe: mean/stddev of pnl series (not annualized)
    if len(pnls) > 1:
        mean = sum(pnls) / len(pnls)
        variance = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
        stddev = variance ** 0.5
        sharpe = round(mean / stddev, 2) if stddev > 0 else 0
    else:
        sharpe = 0

    return {
        "sharpe_ratio": sharpe,
        "max_drawdown": max_dd,
        "expectancy": expectancy,
        "win_rate": win_rate,
        "total_trades": len(pnls)
    }

# ====================== BACKGROUND PRICE POLLING ======================

latest_prices = {"nifty": None, "vix": None, "last_update": None}

def price_poller():
    """Runs in background, refreshes live prices every 5 seconds during market hours."""
    while True:
        try:
            if get_market_status() in ("OPEN", "PRE_OPEN"):
                nifty = get_ltp("NSE", NIFTY_SYMBOL, NIFTY_TOKEN)
                vix = get_ltp("NSE", VIX_SYMBOL, VIX_TOKEN)
                if nifty is not None:
                    latest_prices["nifty"] = nifty
                if vix is not None:
                    latest_prices["vix"] = vix
                latest_prices["last_update"] = get_ist_now().isoformat()
        except Exception as e:
            logger.error(f"price_poller error: {e}")
        time.sleep(5)

# ====================== ROUTES ======================

@app.route("/health")
def health():
    return {
        "status": "healthy",
        "timestamp": datetime.datetime.now().isoformat(),
        "service": "GOAT PRO Institutional",
        "version": "2.2",
        "angel_session": smart_api is not None
    }, 200

@app.route("/ping")
def ping():
    return {"status": "alive"}, 200

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>⚡ GOAT PRO - Institutional</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
body { background: #0f172a; color: #e2e8f0; font-family: system-ui, sans-serif; }
.card { background: #1e2937; border-radius: 12px; }
.header { background: linear-gradient(135deg, #1a56db, #0e3fa8); }
.signal { font-size: 28px; font-weight: bold; }
</style>
</head>
<body class="min-h-screen p-4">
<div class="max-w-7xl mx-auto">

    <div class="header rounded-2xl p-6 mb-6 flex justify-between items-center">
        <div>
            <h1 class="text-3xl font-bold text-white">⚡ GOAT PRO</h1>
            <p class="text-blue-200">Institutional Paper Trading</p>
        </div>
        <div class="text-right">
            <div id="clock" class="text-2xl font-mono text-white"></div>
            <div id="market-status" class="text-green-400 font-semibold">--</div>
        </div>
    </div>

    <div class="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
        <div class="card p-4"><div class="text-blue-400">NIFTY</div><div id="nifty" class="text-3xl font-bold">--</div></div>
        <div class="card p-4"><div>VIX</div><div id="vix" class="text-3xl font-bold text-orange-400">--</div></div>
        <div class="card p-4"><div>P&L</div><div id="pnl" class="text-3xl font-bold text-green-400">₹0</div></div>
        <div class="card p-4"><div>Win Rate</div><div id="winrate" class="text-3xl font-bold">--%</div></div>
        <div class="card p-4"><div>Trades</div><div id="total-trades" class="text-3xl font-bold">0</div></div>
    </div>

    <div class="grid md:grid-cols-3 gap-6">

        <div class="card p-6">
            <h3 class="text-lg mb-4">Market Status</h3>
            <div id="signal" class="signal text-blue-400 mb-2">--</div>
            <div id="risk-status" class="text-sm text-gray-400">--</div>
        </div>

        <div class="card p-6">
            <h3 class="text-lg mb-4">Active Trade</h3>
            <div id="active-trade" class="text-sm">No active trade</div>
        </div>

        <div class="card p-6">
            <h3 class="text-lg mb-4">Institutional Stats (Real)</h3>
            <div class="space-y-2 text-sm">
                <div>Sharpe Ratio: <span class="font-bold" id="sharpe">0</span></div>
                <div>Max Drawdown: <span class="font-bold text-red-400" id="drawdown">0</span></div>
                <div>Expectancy: <span class="font-bold" id="expectancy">0</span></div>
            </div>
        </div>

    </div>

    <div class="text-center text-xs text-gray-500 mt-8">
        GOAT PRO Institutional • Educational Use Only • Live Angel One Feed
    </div>
</div>

<script>
function updateClock() {
    setInterval(() => {
        document.getElementById('clock').textContent = new Date().toLocaleTimeString('en-IN', {hour12:false}) + " IST";
    }, 1000);
}

async function fetchData() {
    try {
        const res = await fetch('/api/data');
        const d = await res.json();

        document.getElementById('nifty').textContent = d.spot ? d.spot.toFixed(2) : '--';
        document.getElementById('vix').textContent = d.vix ? d.vix.toFixed(2) : '--';
        document.getElementById('pnl').textContent = '₹' + (d.session_pnl_rs || 0);
        document.getElementById('winrate').textContent = (d.win_rate || 0) + '%';
        document.getElementById('total-trades').textContent = d.total_trades || 0;
        document.getElementById('signal').textContent = d.market_status;
        document.getElementById('market-status').textContent = d.market_status;
        document.getElementById('risk-status').textContent = d.risk_message || '';

        if (d.institutional_stats) {
            document.getElementById('sharpe').textContent = d.institutional_stats.sharpe_ratio;
            document.getElementById('drawdown').textContent = d.institutional_stats.max_drawdown;
            document.getElementById('expectancy').textContent = d.institutional_stats.expectancy;
        }
    } catch(e) { console.error(e); }
}

updateClock();
fetchData();
setInterval(fetchData, 6000);
</script>
</body>
</html>"""

@app.route("/")
def index():
    return render_template_string(TEMPLATE)

@app.route("/api/data")
def api_data():
    market_status = get_market_status()
    risk_ok, risk_message = check_risk_limits()
    stats = get_institutional_stats()

    with trades_lock:
        session_pnl = sum(t["pnl"] for t in closed_trades if t["timestamp"].date() == get_ist_now().date())

    return jsonify({
        "spot": latest_prices["nifty"],
        "vix": latest_prices["vix"],
        "market_status": market_status,
        "risk_ok": risk_ok,
        "risk_message": risk_message,
        "session_pnl_rs": session_pnl,
        "win_rate": stats["win_rate"],
        "total_trades": stats["total_trades"],
        "institutional_stats": stats,
        "last_update": latest_prices["last_update"]
    })

@app.route("/api/trades")
def api_trades():
    with trades_lock:
        closed = list(closed_trades)
    return jsonify({
        "open": active_trade,
        "closed": [{"pnl": t["pnl"], "direction": t["direction"], "timestamp": t["timestamp"].isoformat()} for t in closed],
        "stats": get_institutional_stats()
    })

# ====================== APP STARTUP ======================
# IMPORTANT: This runs at module-import time, so it works both with
# `python main.py` (local) AND with gunicorn (Render's production server).
# The old `if __name__ == "__main__":` block never fires under gunicorn,
# which is why Angel One login was silently never attempted before.

angel_login()
_poller_thread = threading.Thread(target=price_poller, daemon=True)
_poller_thread.start()

# ====================== RUN (local testing only) ======================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
