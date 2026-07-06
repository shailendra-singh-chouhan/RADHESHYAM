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
BANKNIFTY_TOKEN = "99926009"
BANKNIFTY_SYMBOL = "Nifty Bank"
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

def open_paper_trade():
    """Opens a paper trade using the current live NIFTY price. Returns (success, message)."""
    global active_trade
    with trades_lock:
        if active_trade is not None:
            return False, "A trade is already active"
    if get_market_status() != "OPEN":
        return False, "Market is closed"
    if latest_prices["nifty"] is None:
        return False, "Live price not available yet"

    risk_ok, risk_message = check_risk_limits()
    if not risk_ok:
        return False, risk_message

    entry = latest_prices["nifty"]
    with trades_lock:
        active_trade = {
            "direction": "LONG",
            "entry": entry,
            "target": round(entry + 50, 2),
            "sl": round(entry - 25, 2),
            "opened_at": get_ist_now()
        }
    return True, "Trade opened"

def close_paper_trade():
    """Closes the active paper trade at the current live NIFTY price."""
    global active_trade
    with trades_lock:
        if active_trade is None:
            return False, "No active trade"
        if latest_prices["nifty"] is None:
            return False, "Live price not available"
        exit_price = latest_prices["nifty"]
        pnl = round(exit_price - active_trade["entry"], 2)
        closed_trades.append({
            "entry": active_trade["entry"],
            "exit": exit_price,
            "pnl": pnl,
            "direction": active_trade["direction"],
            "timestamp": get_ist_now()
        })
        active_trade = None
    return True, "Trade closed"

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

# ====================== REAL TECHNICAL INDICATORS (RSI, EMA, VWAP-approx) ======================
# NOTE: True volume-weighted VWAP isn't possible for an index (no real traded
# volume exists for NIFTY the index itself, only for its stocks/futures).
# So VWAP here is an approximation using typical price (H+L+C)/3, clearly
# labelled "approx" on the dashboard - not silently presented as real VWAP.

candle_lock = threading.Lock()
candle_store = []  # list of dicts: {time, open, high, low, close}
indicator_data = {"rsi": None, "ema9": None, "ema21": None, "vwap_approx": None}

def fetch_todays_candles():
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
            "todate": now.strftime("%Y-%m-%d %H:%M")
        }
        resp = smart_api.getCandleData(params)
        if resp and resp.get("status") and resp.get("data"):
            candles = []
            for row in resp["data"]:
                # row format: [timestamp, open, high, low, close, volume]
                candles.append({
                    "time": row[0], "open": row[1], "high": row[2],
                    "low": row[3], "close": row[4]
                })
            return candles
        return None
    except Exception as e:
        logger.error(f"fetch_todays_candles error: {e}")
        return None

def calculate_rsi(closes, period=14):
    """Wilder's RSI calculation."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
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

def calculate_ema(closes, period):
    """Standard EMA, seeded with SMA of the first `period` values."""
    if len(closes) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return round(ema, 2)

def calculate_vwap_approx(candles):
    """Approximate VWAP using typical price average (no real volume data for index)."""
    if not candles:
        return None
    typical_prices = [(c["high"] + c["low"] + c["close"]) / 3 for c in candles]
    return round(sum(typical_prices) / len(typical_prices), 2)

def indicator_poller():
    """Runs in background, refreshes RSI/EMA/VWAP-approx every 60 seconds during market hours."""
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
        except Exception as e:
            logger.error(f"indicator_poller error: {e}")
        time.sleep(60)

# ====================== BACKGROUND PRICE POLLING ======================

latest_prices = {"nifty": None, "vix": None, "day_open": None, "day_open_date": None, "last_update": None}

def price_poller():
    """Runs in background, refreshes live prices every 5 seconds during market hours."""
    while True:
        try:
            if get_market_status() in ("OPEN", "PRE_OPEN"):
                nifty = get_ltp("NSE", NIFTY_SYMBOL, NIFTY_TOKEN)
                vix = get_ltp("NSE", VIX_SYMBOL, VIX_TOKEN)
                today = get_ist_now().date().isoformat()
                if nifty is not None:
                    latest_prices["nifty"] = nifty
                    # Capture first price of the day as day_open (resets each new day)
                    if latest_prices["day_open_date"] != today:
                        latest_prices["day_open"] = nifty
                        latest_prices["day_open_date"] = today
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
<title>⚡ GOAT PRO — Multi Market Command Center</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=JetBrains+Mono:wght@300;400;700&family=Rajdhani:wght@400;600;700&display=swap');
:root{
  --bg:#f0f4ff;--panel:#fff;--panel2:#f7f9ff;--border:#dde4f5;
  --accent:#1a56db;--accent2:#0e3fa8;
  --green:#0a9e5c;--green2:#e6f9f1;
  --red:#e02d3c;--red2:#fdeef0;
  --blue:#1a56db;--blue2:#eef2ff;
  --gold:#b45309;--gold2:#fef3c7;
  --purple:#7c3aed;--purple2:#f5f3ff;
  --dim:#6b7280;--text:#1e2a3a;
  --shadow:0 2px 12px rgba(26,86,219,0.08);
  --shadow2:0 6px 28px rgba(26,86,219,0.18);
}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:'Rajdhani',sans-serif;min-height:100vh;}
body::before{content:'';position:fixed;inset:0;
  background:radial-gradient(ellipse 70% 40% at 10% 0%,rgba(26,86,219,0.07),transparent 70%),
             radial-gradient(ellipse 50% 50% at 90% 100%,rgba(26,86,219,0.05),transparent 70%);
  pointer-events:none;z-index:0;}
.wrap{max-width:1400px;margin:0 auto;padding:12px 14px;position:relative;z-index:1;}
.topbar{display:flex;align-items:center;justify-content:space-between;
  background:linear-gradient(135deg,#1a56db,#0e3fa8);
  border-radius:12px;padding:14px 22px;margin-bottom:12px;
  box-shadow:var(--shadow2);flex-wrap:wrap;gap:10px;}
.topbar h1{font-family:'Bebas Neue',sans-serif;font-size:clamp(20px,4vw,34px);
  letter-spacing:5px;color:#fff;}
.topbar small{font-family:'JetBrains Mono',monospace;font-size:9px;
  color:rgba(255,255,255,0.55);letter-spacing:2px;display:block;}
.tb-right{display:flex;gap:12px;align-items:center;flex-wrap:wrap;}
.tb-stat{text-align:center;}
.tb-val{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;color:#fff;}
.tb-label{font-size:9px;color:rgba(255,255,255,0.5);letter-spacing:1px;text-transform:uppercase;}
.tb-div{width:1px;height:26px;background:rgba(255,255,255,0.2);}
.live-pill{display:flex;align-items:center;gap:6px;
  background:rgba(255,255,255,0.15);border:1px solid rgba(255,255,255,0.3);
  border-radius:20px;padding:5px 14px;
  font-family:'JetBrains Mono',monospace;font-size:10px;color:#fff;}
.ldot{width:7px;height:7px;border-radius:50%;background:#4ade80;
  box-shadow:0 0 8px #4ade80;animation:blink 1s infinite;}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0.2}}
.legal-banner{background:linear-gradient(135deg,#fef3c7,#fde68a);
  border:1.5px solid #f59e0b;border-radius:8px;padding:10px 16px;
  margin-bottom:12px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.legal-banner span{font-size:12px;color:#92400e;line-height:1.5;flex:1;}
.legal-badge{background:#f59e0b;color:#fff;border-radius:4px;
  padding:3px 10px;font-size:10px;font-weight:700;white-space:nowrap;
  font-family:'JetBrains Mono',monospace;}
.sess-strip{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap;}
.sess{flex:1;min-width:100px;border:1.5px solid var(--border);border-radius:8px;
  padding:8px 10px;text-align:center;background:var(--panel);
  box-shadow:var(--shadow);position:relative;overflow:hidden;transition:all 0.2s;}
.sess.active{border-color:var(--accent);background:var(--blue2);}
.sess.active::after{content:'';position:absolute;bottom:0;left:0;right:0;height:3px;background:var(--accent);}
.sess-name{font-size:10px;color:var(--dim);letter-spacing:1px;font-weight:600;}
.sess-time{font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;margin:2px 0;}
.sess-heat{font-size:13px;}
.sess-tip{font-size:9px;color:var(--dim);margin-top:2px;}
.market-tabs{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap;}
.mktab{padding:7px 14px;border-radius:8px;border:1.5px solid var(--border);
  background:var(--panel);font-family:'JetBrains Mono',monospace;font-size:10px;
  cursor:pointer;transition:all 0.2s;color:var(--dim);font-weight:700;
  box-shadow:var(--shadow);text-align:center;}
.mktab:hover{border-color:var(--accent);color:var(--accent);}
.mktab.on{background:linear-gradient(135deg,#1a56db,#0e3fa8);color:#fff;
  border-color:var(--accent);box-shadow:0 4px 16px rgba(26,86,219,0.3);}
.mktab .chg{font-size:9px;display:block;margin-top:1px;}
.layout{display:grid;grid-template-columns:1fr 320px;gap:12px;}
@media(max-width:900px){.layout{grid-template-columns:1fr;}}
.left{display:flex;flex-direction:column;gap:12px;}
.right{display:flex;flex-direction:column;gap:10px;}
.card{background:var(--panel);border:1.5px solid var(--border);
  border-radius:10px;box-shadow:var(--shadow);overflow:hidden;}
.chdr{display:flex;align-items:center;justify-content:space-between;
  padding:9px 14px;border-bottom:1.5px solid var(--border);background:var(--panel2);}
.ctitle{font-family:'Bebas Neue',sans-serif;font-size:14px;letter-spacing:2px;color:var(--accent);}
.demo-tag{font-size:9px;color:var(--dim);font-weight:400;letter-spacing:0;font-family:'JetBrains Mono',monospace;background:var(--panel2);border:1px solid var(--border);border-radius:4px;padding:1px 6px;margin-left:6px;}
.live-tag{font-size:9px;color:var(--green);font-weight:700;letter-spacing:0;font-family:'JetBrains Mono',monospace;background:var(--green2);border:1px solid rgba(10,158,92,0.3);border-radius:4px;padding:1px 6px;margin-left:6px;}
.hero{background:linear-gradient(135deg,#1a56db,#0e3fa8);border-radius:10px;
  padding:16px 20px;box-shadow:var(--shadow2);color:#fff;
  display:flex;gap:18px;align-items:center;flex-wrap:wrap;}
.hero-price{font-family:'JetBrains Mono',monospace;font-size:clamp(28px,5vw,44px);font-weight:700;}
.hero-chg{font-size:13px;margin-top:3px;font-family:'JetBrains Mono',monospace;}
.hdiv{width:1px;height:50px;background:rgba(255,255,255,0.2);}
.hstats{display:flex;gap:16px;flex-wrap:wrap;}
.hst{text-align:center;}
.hst-v{font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:700;}
.hst-l{font-size:9px;color:rgba(255,255,255,0.55);letter-spacing:1px;margin-top:2px;}
.jadui{border-radius:10px;padding:14px 16px;border:2px solid;position:relative;overflow:hidden;}
.j-badge{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;
  border-radius:20px;font-family:'JetBrains Mono',monospace;font-size:10px;
  font-weight:700;margin-bottom:8px;}
.j-title{font-family:'Bebas Neue',sans-serif;font-size:clamp(16px,3vw,22px);
  letter-spacing:2px;margin-bottom:6px;}
.j-desc{font-size:13px;line-height:1.6;margin-bottom:10px;}
.j-levels{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px;}
.jlev{padding:5px 12px;border-radius:5px;font-family:'JetBrains Mono',monospace;
  font-size:11px;font-weight:700;border:1.5px solid;}
.jlev.e{background:var(--green2);color:var(--green);border-color:rgba(10,158,92,0.3);}
.jlev.s{background:var(--red2);color:var(--red);border-color:rgba(224,45,60,0.3);}
.jlev.t{background:var(--blue2);color:var(--blue);border-color:rgba(26,86,219,0.3);}
.jlev.r{background:var(--gold2);color:var(--gold);border-color:rgba(180,83,9,0.3);}
.chart-wrap{padding:12px 14px;}
.ind-row{display:flex;align-items:center;gap:8px;padding:7px 14px;
  border-bottom:1px solid var(--border);transition:background 0.15s;}
.ind-row:last-child{border-bottom:none;}
.ind-row:hover{background:var(--blue2);}
.iname{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--dim);width:90px;}
.ibar{flex:1;height:4px;background:var(--border);border-radius:2px;overflow:hidden;}
.ibfill{height:100%;border-radius:2px;transition:width 0.8s;}
.ival{font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;min-width:60px;text-align:right;}
.isig{font-size:9px;padding:2px 7px;border-radius:3px;font-weight:700;
  min-width:52px;text-align:center;font-family:'JetBrains Mono',monospace;}
.bull{background:var(--green2);color:var(--green);}
.bear{background:var(--red2);color:var(--red);}
.neu{background:var(--blue2);color:var(--blue);}
.tf-row{display:flex;gap:4px;}
.tft{padding:3px 9px;border-radius:4px;border:1px solid var(--border);
  font-family:'JetBrains Mono',monospace;font-size:10px;cursor:pointer;
  color:var(--dim);background:var(--panel2);transition:all 0.15s;}
.tft.on{background:var(--accent);color:#fff;border-color:var(--accent);}
.sm-row{display:flex;align-items:center;gap:10px;padding:8px 14px;
  border-bottom:1px solid var(--border);}
.sm-row:last-child{border-bottom:none;}
.sm-icon{font-size:20px;width:28px;text-align:center;}
.sm-info{flex:1;}
.sm-name{font-size:13px;font-weight:700;}
.sm-desc{font-size:11px;color:var(--dim);margin-top:1px;line-height:1.4;}
.sm-sig{font-size:10px;padding:2px 8px;border-radius:3px;font-weight:700;
  font-family:'JetBrains Mono',monospace;}
.theory-row{padding:10px 14px;border-bottom:1px solid var(--border);}
.theory-row:last-child{border-bottom:none;}
.theory-title{font-size:13px;font-weight:700;color:var(--accent);margin-bottom:4px;}
.theory-body{font-size:12px;color:var(--dim);line-height:1.5;}
.theory-tag{display:inline-block;padding:1px 7px;border-radius:3px;
  font-size:9px;font-weight:700;margin-right:4px;margin-bottom:4px;
  font-family:'JetBrains Mono',monospace;}
.mini{border:1.5px solid var(--border);border-radius:10px;
  background:var(--panel);box-shadow:var(--shadow);overflow:hidden;}
.mini-hdr{display:flex;justify-content:space-between;align-items:center;
  padding:8px 12px;border-bottom:1px solid var(--border);background:var(--panel2);}
.mini-name{font-family:'Bebas Neue',sans-serif;font-size:13px;letter-spacing:2px;color:var(--accent);}
.mini-px{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;}
.mini-body{padding:10px 12px;display:grid;grid-template-columns:1fr 1fr;gap:6px;}
.ms{background:var(--panel2);border-radius:6px;padding:6px 8px;border:1px solid var(--border);}
.ms-l{font-size:9px;color:var(--dim);letter-spacing:1px;text-transform:uppercase;}
.ms-v{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;margin-top:2px;}
.mini-jadui{margin:0 12px 10px;padding:8px 10px;border-radius:6px;
  font-size:11px;font-weight:700;text-align:center;border:1.5px solid;}
.greek-row{display:flex;justify-content:space-between;align-items:center;
  padding:7px 14px;border-bottom:1px solid var(--border);}
.greek-row:last-child{border-bottom:none;}
.greek-l{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--dim);}
.greek-v{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;}
.oi-p{padding:12px 14px;}
.oi-row{display:flex;justify-content:space-between;align-items:center;padding:4px 0;}
.oi-l{font-size:11px;color:var(--dim);}
.oi-v{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;}
.oi-track{height:5px;background:var(--border);border-radius:3px;overflow:hidden;margin-bottom:6px;}
.oi-fill{height:100%;border-radius:3px;transition:width 0.8s;}
.pat-row{display:flex;align-items:center;gap:10px;padding:8px 12px;
  border-bottom:1px solid var(--border);}
.pat-row:last-child{border-bottom:none;}
.pat-icon{font-size:20px;width:26px;text-align:center;}
.pat-name{font-size:13px;font-weight:700;}
.pat-desc{font-size:10px;color:var(--dim);margin-top:1px;}
.pat-conf{font-family:'JetBrains Mono',monospace;font-size:11px;
  padding:2px 8px;border-radius:3px;font-weight:700;}
.news-item{display:flex;gap:8px;padding:8px 12px;border-bottom:1px solid var(--border);}
.news-item:last-child{border-bottom:none;}
.nimp{font-size:9px;padding:2px 6px;border-radius:3px;font-weight:700;
  font-family:'JetBrains Mono',monospace;white-space:nowrap;height:fit-content;margin-top:2px;}
.ntxt{font-size:12px;line-height:1.4;}
.ntime{font-size:10px;color:var(--dim);margin-top:2px;}
.alert-item{display:flex;gap:10px;align-items:center;padding:8px 14px;
  border-bottom:1px solid var(--border);animation:flashIn 0.5s;}
@keyframes flashIn{from{background:rgba(26,86,219,0.08)}to{background:transparent}}
.alert-item:last-child{border-bottom:none;}
.alert-time{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--dim);min-width:48px;}
.alert-badge{font-size:9px;padding:2px 7px;border-radius:3px;font-weight:700;
  font-family:'JetBrains Mono',monospace;white-space:nowrap;}
.alert-msg{font-size:12px;flex:1;line-height:1.4;}
.alert-px{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--accent);font-weight:700;}
.cap-bar{display:flex;gap:16px;align-items:center;flex-wrap:wrap;
  padding:12px 16px;background:var(--panel);border:1.5px solid var(--border);
  border-radius:10px;box-shadow:var(--shadow);margin-bottom:12px;}
.cap-item{text-align:center;}
.cap-val{font-family:'JetBrains Mono',monospace;font-size:15px;font-weight:700;}
.cap-l{font-size:9px;color:var(--dim);letter-spacing:1px;text-transform:uppercase;margin-top:2px;}
.cap-div{width:1px;height:34px;background:var(--border);}
.risk-wrap{flex:1;min-width:140px;}
.risk-l{font-size:11px;color:var(--dim);margin-bottom:4px;}
.risk-track{height:7px;background:var(--border);border-radius:4px;overflow:hidden;}
.risk-fill{height:100%;border-radius:4px;
  background:linear-gradient(90deg,var(--green),#f59e0b,var(--red));transition:width 0.8s;}
.refresh-btn{background:linear-gradient(135deg,var(--accent),var(--accent2));
  border:none;border-radius:6px;padding:7px 16px;
  font-family:'Bebas Neue',sans-serif;font-size:14px;letter-spacing:2px;
  color:#fff;cursor:pointer;transition:all 0.2s;box-shadow:var(--shadow);}
.refresh-btn:hover{transform:translateY(-1px);box-shadow:var(--shadow2);}
.footer{margin-top:14px;padding:12px 16px;
  background:linear-gradient(135deg,#fef3c7,#fde68a);
  border:1.5px solid #f59e0b;border-radius:10px;
  display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;}
.footer-text{font-size:11px;color:#92400e;line-height:1.6;flex:1;}
.footer-badge{background:#f59e0b;color:#fff;border-radius:4px;
  padding:4px 12px;font-size:10px;font-weight:700;font-family:'JetBrains Mono',monospace;
  white-space:nowrap;}
</style>
</head>
<body>
<div class="wrap">

<div class="topbar">
  <div>
    <h1>⚡ GOAT PRO</h1>
    <small>🇮🇳 MULTI MARKET COMMAND CENTER · PERSONAL USE ONLY</small>
  </div>
  <div class="tb-right">
    <div class="tb-stat">
      <div class="tb-val" id="tb-time">--:--:--</div>
      <div class="tb-label">⏰ IST Time</div>
    </div>
    <div class="tb-div"></div>
    <div class="tb-stat">
      <div class="tb-val" id="tb-trades">0</div>
      <div class="tb-label">📊 Trades (Real)</div>
    </div>
    <div class="tb-div"></div>
    <div class="tb-stat">
      <div class="tb-val" id="tb-pnl" style="color:#4ade80">₹0</div>
      <div class="tb-label">💰 P&L (Real)</div>
    </div>
    <div class="tb-div"></div>
    <div class="tb-stat">
      <div class="tb-val" id="tb-winrate" style="color:#fbbf24">0%</div>
      <div class="tb-label">🎯 Win Rate (Real)</div>
    </div>
    <div class="live-pill" id="market-pill"><div class="ldot" id="market-dot"></div><span id="market-pill-text">--</span></div>
  </div>
</div>

<div class="legal-banner">
  <div class="legal-badge">⚖️ LEGAL NOTICE</div>
  <span>⚠️ Yeh sirf <strong>Educational Tool</strong> hai — Financial/Investment Advice nahi hai। NIFTY price aur VIX <strong>live Angel One feed</strong> se hain. Baaki markets, indicators, OI, Greeks aur signals <strong>illustrative demo data</strong> hain, abhi live nahi. Apni responsibility pe trade karo। SEBI registered advisor se consult karo।</span>
</div>

<div class="sess-strip" id="sess-strip">
  <div class="sess" id="s1">
    <div class="sess-name">🔥 OPENING</div>
    <div class="sess-time">9:15–11:00</div>
    <div class="sess-heat">⚡⚡⚡</div>
    <div class="sess-tip">High Volatility</div>
  </div>
  <div class="sess" id="s2">
    <div class="sess-name">😴 MID</div>
    <div class="sess-time">11:00–1:00</div>
    <div class="sess-heat">〰️〰️</div>
    <div class="sess-tip">Choppy — Careful</div>
  </div>
  <div class="sess" id="s3">
    <div class="sess-name">📈 AFTERNOON</div>
    <div class="sess-time">1:00–2:30</div>
    <div class="sess-heat">⚡⚡</div>
    <div class="sess-tip">Momentum Returns</div>
  </div>
  <div class="sess" id="s4">
    <div class="sess-name">💥 POWER HOUR</div>
    <div class="sess-time">2:30–3:30</div>
    <div class="sess-heat">🚀🚀🚀</div>
    <div class="sess-tip">Explosive Moves</div>
  </div>
</div>

<div class="cap-bar">
  <div class="cap-item">
    <div class="cap-val" style="color:var(--accent)" id="cap-trades">0</div>
    <div class="cap-l">📊 Total Trades</div>
  </div>
  <div class="cap-div"></div>
  <div class="cap-item">
    <div class="cap-val" style="color:var(--green)" id="cap-pnl">₹0</div>
    <div class="cap-l">📈 Today P&L (Real)</div>
  </div>
  <div class="cap-div"></div>
  <div class="cap-item">
    <div class="cap-val" style="color:var(--red)">₹400</div>
    <div class="cap-l">🛑 Max Risk/Trade</div>
  </div>
  <div class="cap-div"></div>
  <div class="cap-item">
    <div class="cap-val" style="color:var(--red)">-₹2,000</div>
    <div class="cap-l">⛔ Daily Loss Limit</div>
  </div>
  <div class="cap-div"></div>
  <div class="risk-wrap">
    <div class="risk-l" id="risk-status-text">🎯 Risk Status — <strong id="risk-pct">Checking...</strong></div>
    <div class="risk-track"><div class="risk-fill" id="risk-fill" style="width:0%"></div></div>
  </div>
</div>

<div class="market-tabs">
  <div class="mktab on" onclick="switchMarket(this,'nifty')">🔵 NIFTY (LIVE)<span class="chg" id="nifty-chg-tab" style="color:#4ade80">--</span></div>
  <div class="mktab" onclick="switchMarket(this,'banknifty')">🏦 BANKNIFTY 🎲<span class="chg" id="bn-chg-tab" style="color:#f87171">-0.18%</span></div>
  <div class="mktab" onclick="switchMarket(this,'sensex')">📊 SENSEX 🎲<span class="chg" id="sx-chg-tab" style="color:#4ade80">+0.44%</span></div>
  <div class="mktab" onclick="switchMarket(this,'crude')">🛢️ CRUDE OIL 🎲<span class="chg" id="cr-chg-tab" style="color:#4ade80">+1.2%</span></div>
  <div class="mktab" onclick="switchMarket(this,'gold')">🥇 MCX GOLD 🎲<span class="chg" id="gd-chg-tab" style="color:#f87171">-0.3%</span></div>
  <div class="mktab" onclick="switchMarket(this,'silver')">🥈 MCX SILVER 🎲<span class="chg" id="sv-chg-tab" style="color:#4ade80">+0.8%</span></div>
  <div class="mktab" onclick="switchMarket(this,'stocks')">🏢 STOCKS 🎲<span class="chg" style="color:var(--dim)">HDFC SBI+</span></div>
</div>

<div class="layout">
<div class="left">

  <div class="hero" id="hero-section">
    <div>
      <div style="font-size:11px;opacity:0.7;letter-spacing:2px;margin-bottom:4px" id="hero-label">🔵 NIFTY 50 · INDEX <span class="live-tag" id="hero-live-tag" style="background:rgba(255,255,255,0.2);color:#fff;border-color:transparent">LIVE</span></div>
      <div class="hero-price" id="hero-price">--</div>
      <div class="hero-chg" id="hero-chg">Waiting for market data...</div>
    </div>
    <div class="hdiv"></div>
    <div class="hstats">
      <div class="hst"><div class="hst-v" id="h-vix">--</div><div class="hst-l">🌡️ VIX (LIVE)</div></div>
      <div class="hst"><div class="hst-v" id="h-open">--</div><div class="hst-l">📂 DAY OPEN</div></div>
      <div class="hst"><div class="hst-v" id="h-pcr">-- 🎲</div><div class="hst-l">📊 PCR (Demo)</div></div>
      <div class="hst"><div class="hst-v" id="h-iv">-- 🎲</div><div class="hst-l">⚡ IV (Demo)</div></div>
      <div class="hst"><div class="hst-v" id="h-maxpain">-- 🎲</div><div class="hst-l">😰 MAX PAIN (Demo)</div></div>
    </div>
  </div>

  <div class="jadui" id="jadui-card" style="border-color:rgba(10,158,92,0.4);background:var(--green2);">
    <div class="j-badge" id="j-badge" style="background:var(--green2);color:var(--green);border:1px solid rgba(10,158,92,0.3)">🎲 DEMO ANALYSIS</div>
    <div class="j-title" id="j-title" style="color:var(--green)">🌟 BULLISH JADUI SPOT DETECTED!</div>
    <div class="j-desc" id="j-desc">
      🔨 <strong>Hammer Candle</strong> at VWAP Support + 📊 RSI 38 (Oversold Bounce) + 📈 OI Long Buildup detected.<br>
      🧠 <strong>Smart Money</strong> accumulation zone — Institutions buying dikh raha hai.<br>
      🎯 <strong>Confluence Score: 8/10</strong> — High probability reversal. Chhota SL, bada target!
      <br><span style="font-size:11px;color:var(--dim)">(Yeh illustrative demo hai — asli strategy engine agla step hai)</span>
    </div>
    <div class="j-levels">
      <div class="jlev e">🟢 ENTRY: 24,380</div>
      <div class="jlev s">🔴 SL: 24,350 (30 pts)</div>
      <div class="jlev t">🎯 TGT1: 24,440 · TGT2: 24,490</div>
      <div class="jlev r">⚖️ R:R = 1:2 ✅</div>
    </div>
  </div>

  <div class="card" style="border-color:rgba(10,158,92,0.4)">
    <div class="chdr" style="background:var(--green2)">
      <div class="ctitle" style="color:var(--green)">📝 REAL PAPER TRADE ENGINE <span style="font-size:9px;background:var(--green);color:#fff;border-radius:3px;padding:1px 6px;margin-left:6px">LIVE</span></div>
    </div>
    <div style="padding:14px 16px">
      <div id="trade-status" style="font-size:12px;color:var(--dim);margin-bottom:10px">Loading...</div>
      <div id="active-trade-box" style="display:none;background:var(--panel2);border:1px solid var(--border);border-radius:8px;padding:10px 12px;margin-bottom:10px;font-family:'JetBrains Mono',monospace;font-size:12px">
        <div>Direction: <strong id="at-dir">--</strong> · Entry: <strong id="at-entry">--</strong></div>
        <div>Target: <strong style="color:var(--green)" id="at-target">--</strong> · SL: <strong style="color:var(--red)" id="at-sl">--</strong></div>
        <div>Live P&L: <strong id="at-pnl">--</strong></div>
      </div>
      <div style="display:flex;gap:8px">
        <button class="refresh-btn" style="flex:1" id="execute-btn" onclick="executeTrade()">▶️ EXECUTE PAPER TRADE</button>
        <button class="refresh-btn" style="flex:1;background:linear-gradient(135deg,#e02d3c,#b91c2f);display:none" id="close-btn" onclick="closeTrade()">⏹️ CLOSE TRADE</button>
      </div>
      <div style="font-size:10px;color:var(--dim);margin-top:8px">Yeh असली paper trade hai — live NIFTY price pe entry/exit hoti hai, koi fake number nahi. Sirf market hours (9:15–3:30, Mon–Fri) mein kaam karega.</div>
    </div>
  </div>

  <div class="card">
    <div class="chdr">
      <div class="ctitle">📈 LIVE CANDLE CHART <span class="demo-tag">Demo</span></div>
      <div class="tf-row">
        <div class="tft on" onclick="setTf(this,'1m')">1M</div>
        <div class="tft" onclick="setTf(this,'5m')">5M</div>
        <div class="tft" onclick="setTf(this,'15m')">15M</div>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="main-canvas" height="180"></canvas></div>
  </div>


  <div class="card">
    <div class="chdr">
      <div class="ctitle">🎛️ INDICATOR DASHBOARD <span style="font-size:9px;background:var(--green);color:#fff;border-radius:3px;padding:1px 6px">RSI/EMA LIVE</span></div>
      <div style="font-size:10px;color:var(--dim);font-family:'JetBrains Mono',monospace" id="ind-tf-label">1M CANDLES</div>
    </div>
    <div class="ind-row">
      <div class="iname">📊 RSI(14)</div>
      <div class="ibar"><div class="ibfill" id="rsi-bar" style="background:var(--green);width:38%"></div></div>
      <div class="ival" id="rsi-val" style="color:var(--green)">--</div>
      <div class="isig bull" id="rsi-sig">--</div>
    </div>
    <div class="ind-row">
      <div class="iname">📏 VWAP (approx)</div>
      <div class="ibar"><div class="ibfill" id="vwap-bar" style="background:var(--blue);width:55%"></div></div>
      <div class="ival" id="vwap-val" style="color:var(--blue)">--</div>
      <div class="isig bull" id="vwap-sig">--</div>
    </div>
    <div class="ind-row">
      <div class="iname">📉 EMA 9</div>
      <div class="ibar"><div class="ibfill" id="ema9-bar" style="background:var(--green);width:65%"></div></div>
      <div class="ival" id="ema9-val" style="color:var(--green)">--</div>
      <div class="isig bull" id="ema9-sig">--</div>
    </div>
    <div class="ind-row">
      <div class="iname">📉 EMA 21</div>
      <div class="ibar"><div class="ibfill" id="ema21-bar" style="background:var(--green);width:58%"></div></div>
      <div class="ival" id="ema21-val" style="color:var(--green)">--</div>
      <div class="isig bull" id="ema21-sig">--</div>
    </div>
    <div class="ind-row">
      <div class="iname">🌊 SUPERTREND 🎲</div>
      <div class="ibar"><div class="ibfill" id="st-bar" style="background:var(--green);width:75%"></div></div>
      <div class="ival" id="st-val" style="color:var(--green)">--</div>
      <div class="isig bull" id="st-sig">✅ BUY</div>
    </div>
  </div>

  <div class="card">
    <div class="chdr"><div class="ctitle">🏦 SMART MONEY MOVES <span class="demo-tag">Demo</span></div></div>
    <div class="sm-row">
      <div class="sm-icon">🐳</div>
      <div class="sm-info">
        <div class="sm-name">FII Net Activity</div>
        <div class="sm-desc">Foreign Institutional Investors aaj net buyers hain — ₹2,840 Cr cash market mein. Bullish signal!</div>
      </div>
      <div class="sm-sig bull">🟢 BUYING</div>
    </div>
    <div class="sm-row">
      <div class="sm-icon">🏛️</div>
      <div class="sm-info">
        <div class="sm-name">DII Net Activity</div>
        <div class="sm-desc">Domestic Institutions bhi support kar rahe hain — ₹1,200 Cr buying. Market strong base hai.</div>
      </div>
      <div class="sm-sig bull">🟢 SUPPORT</div>
    </div>
    <div class="sm-row">
      <div class="sm-icon">📦</div>
      <div class="sm-info">
        <div class="sm-name">OI Long Buildup</div>
        <div class="sm-desc">24,400 CE mein fresh long positions build ho rahe hain — bullish momentum expect karo.</div>
      </div>
      <div class="sm-sig bull">🟢 LONG</div>
    </div>
  </div>

  <div class="card">
    <div class="chdr"><div class="ctitle">📰 NEWS & MARKET IMPACT <span class="demo-tag">Demo</span></div></div>
    <div id="news-list">
      <div class="news-item">
        <div class="nimp" style="background:var(--red2);color:var(--red);border:1px solid rgba(224,45,60,0.3)">🔴 HIGH</div>
        <div>
          <div class="ntxt"><strong>RBI Governor Speech</strong> — 2:30 PM aaj · Market volatile ho sakta hai</div>
          <div class="ntime">⚠️ 2:20 PM se pehle position reduce karo · New entry avoid karo</div>
        </div>
      </div>
      <div class="news-item">
        <div class="nimp" style="background:var(--gold2);color:var(--gold);border:1px solid rgba(180,83,9,0.3)">🟡 MED</div>
        <div>
          <div class="ntxt"><strong>FII Net Buyers</strong> — ₹2,840 Cr cash market mein aaj</div>
          <div class="ntime">✅ Bullish sentiment · Market support strong hai</div>
        </div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="chdr">
      <div class="ctitle">🚨 LIVE ALERT FEED <span class="demo-tag">Demo</span></div>
    </div>
    <div id="alert-list"></div>
  </div>

</div>

<div class="right">

  <div class="card">
    <div class="chdr"><div class="ctitle">⚡ GREEKS & IV <span class="demo-tag">Demo</span></div></div>
    <div class="greek-row">
      <div class="greek-l">📊 IV (ATM)</div>
      <div class="greek-v" style="color:var(--gold)" id="iv-v">14.2%</div>
    </div>
    <div class="greek-row">
      <div class="greek-l">⚡ GAMMA</div>
      <div class="greek-v" style="color:var(--purple)" id="gam-v">0.0048</div>
    </div>
    <div class="greek-row">
      <div class="greek-l">⏳ THETA/day</div>
      <div class="greek-v" style="color:var(--red)" id="tht-v">-₹12.4</div>
    </div>
  </div>

  <div class="card">
    <div class="chdr">
      <div class="ctitle">📊 OI ANALYSIS <span class="demo-tag">Demo</span></div>
    </div>
    <div class="oi-p">
      <div class="oi-row"><div class="oi-l">🔴 CALL OI</div><div class="oi-v" style="color:var(--red)" id="call-oi">48.2L</div></div>
      <div class="oi-track"><div class="oi-fill" id="call-bar" style="width:44%;background:var(--red)"></div></div>
      <div class="oi-row"><div class="oi-l">🟢 PUT OI</div><div class="oi-v" style="color:var(--green)" id="put-oi">59.8L</div></div>
      <div class="oi-track"><div class="oi-fill" id="put-bar" style="width:55%;background:var(--green)"></div></div>
    </div>
  </div>

  <div class="card">
    <div class="chdr"><div class="ctitle">🕯️ CANDLE PATTERNS <span class="demo-tag">Demo</span></div></div>
    <div id="pat-list">
      <div class="pat-row">
        <div class="pat-icon">🔨</div>
        <div style="flex:1"><div class="pat-name">Hammer</div><div class="pat-desc">Strong bullish reversal at support</div></div>
        <div class="pat-conf" style="background:var(--green2);color:var(--green);border:1px solid rgba(10,158,92,0.3)">94% 🟢</div>
      </div>
    </div>
  </div>

  <div class="mini">
    <div class="mini-hdr">
      <div class="mini-name">🏦 BANKNIFTY 🎲</div>
      <div class="mini-px" id="bn-px" style="color:var(--red)">51,240 ▼</div>
    </div>
    <div class="mini-body">
      <div class="ms"><div class="ms-l">📊 RSI</div><div class="ms-v" style="color:var(--red)" id="bn-rsi">71.2</div></div>
      <div class="ms"><div class="ms-l">🌊 TREND</div><div class="ms-v" style="color:var(--red)" id="bn-tr">BEAR</div></div>
    </div>
  </div>

  <div class="mini">
    <div class="mini-hdr">
      <div class="mini-name">📊 SENSEX 🎲</div>
      <div class="mini-px" id="sx-px" style="color:var(--green)">80,140 ▲</div>
    </div>
    <div class="mini-body">
      <div class="ms"><div class="ms-l">📊 RSI</div><div class="ms-v" style="color:var(--green)" id="sx-rsi">52.4</div></div>
      <div class="ms"><div class="ms-l">🌊 TREND</div><div class="ms-v" style="color:var(--green)" id="sx-tr">BULL</div></div>
    </div>
  </div>

  <div class="mini">
    <div class="mini-hdr">
      <div class="mini-name">🛢️ CRUDE OIL 🎲</div>
      <div class="mini-px" id="cr-px" style="color:var(--green)">₹6,842 ▲</div>
    </div>
    <div class="mini-body">
      <div class="ms"><div class="ms-l">📊 RSI</div><div class="ms-v" style="color:var(--green)" id="cr-rsi">62.1</div></div>
      <div class="ms"><div class="ms-l">📈 TREND</div><div class="ms-v" style="color:var(--green)" id="cr-tr">BULL</div></div>
    </div>
  </div>

  <div class="mini">
    <div class="mini-hdr">
      <div class="mini-name">🥇 MCX GOLD 🎲</div>
      <div class="mini-px" id="gd-px" style="color:var(--red)">₹71,240 ▼</div>
    </div>
    <div class="mini-body">
      <div class="ms"><div class="ms-l">📊 RSI</div><div class="ms-v" style="color:var(--dim)" id="gd-rsi">48.2</div></div>
      <div class="ms"><div class="ms-l">📈 TREND</div><div class="ms-v" style="color:var(--dim)" id="gd-tr">NEUTRAL</div></div>
    </div>
  </div>

  <div class="mini">
    <div class="mini-hdr">
      <div class="mini-name">🥈 MCX SILVER 🎲</div>
      <div class="mini-px" id="sv-px" style="color:var(--green)">₹84,120 ▲</div>
    </div>
    <div class="mini-body">
      <div class="ms"><div class="ms-l">📊 RSI</div><div class="ms-v" style="color:var(--green)" id="sv-rsi">55.8</div></div>
      <div class="ms"><div class="ms-l">📈 TREND</div><div class="ms-v" style="color:var(--green)" id="sv-tr">BULL</div></div>
    </div>
  </div>

  <div class="card">
    <div class="chdr"><div class="ctitle">🏢 STOCKS 🎲 <span class="demo-tag">Demo</span></div></div>
    <div id="stock-list">
      <div class="ind-row">
        <div class="iname">🏦 HDFC</div>
        <div class="ibar"><div class="ibfill" style="background:var(--green);width:62%"></div></div>
        <div class="ival" style="color:var(--green)" id="hdfc-v">₹1,842</div>
        <div class="isig bull">📈 BUY</div>
      </div>
      <div class="ind-row">
        <div class="iname">🏛️ SBI</div>
        <div class="ibar"><div class="ibfill" style="background:var(--green);width:70%"></div></div>
        <div class="ival" style="color:var(--green)" id="sbi-v">₹824</div>
        <div class="isig bull">📈 BUY</div>
      </div>
    </div>
  </div>

  <button class="refresh-btn" style="width:100%" onclick="refreshAll()">🔄 REFRESH DEMO SIGNALS</button>

</div>
</div>

<div class="footer" style="margin-top:12px">
  <div class="footer-badge">⚖️ LEGAL</div>
  <div class="footer-text">
    ⚠️ Yeh tool sirf <strong>Personal Educational Use</strong> ke liye hai। NIFTY aur VIX live Angel One feed se hain, baaki sab demo/illustrative hai।
    Yeh SEBI registered financial/investment advice nahi hai। Trading mein substantial risk hota hai।
    Kisi bhi trade se pehle SEBI registered advisor se consult karo।
  </div>
</div>

</div>

<script>
function updateClock(){
  const n=new Date();
  const t=`${String(n.getHours()).padStart(2,'0')}:${String(n.getMinutes()).padStart(2,'0')}:${String(n.getSeconds()).padStart(2,'0')}`;
  document.getElementById('tb-time').textContent=t;
  const m=n.getHours()*60+n.getMinutes();
  [['s1',9*60+15,11*60],['s2',11*60,13*60],['s3',13*60,14*60+30],['s4',14*60+30,15*60+30]].forEach(([id,from,to])=>{
    document.getElementById(id).classList.toggle('active',m>=from&&m<to);
  });
}
setInterval(updateClock,1000); updateClock();

const canvas=document.getElementById('main-canvas');
const ctx=canvas.getContext('2d');
let candles=[], currentTf='1m', currentMarket='nifty';

function genCandles(n,base){
  const arr=[]; let p=base;
  for(let i=0;i<n;i++){
    const o=p,c=o+(Math.random()-0.47)*28;
    const h=Math.max(o,c)+Math.random()*12;
    const l=Math.min(o,c)-Math.random()*12;
    arr.push({o,h,l,c}); p=c;
  }
  return arr;
}

function drawChart(){
  const W=canvas.parentElement.offsetWidth-28, H=180;
  canvas.width=W; canvas.height=H;
  ctx.clearRect(0,0,W,H);
  if(!candles.length)return;
  const hi=Math.max(...candles.map(c=>c.h));
  const lo=Math.min(...candles.map(c=>c.l));
  const range=hi-lo||1;
  const pad=12;
  const toY=v=>pad+(hi-v)/range*(H-pad*2);
  const gap=Math.floor(W/candles.length);
  const cw=Math.max(3,gap-2);
  ctx.strokeStyle='rgba(26,86,219,0.05)'; ctx.lineWidth=1;
  for(let i=0;i<5;i++){
    const y=pad+(H-pad*2)/4*i;
    ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(W,y); ctx.stroke();
  }
  candles.forEach((c,i)=>{
    const x=i*gap+gap/2, bull=c.c>=c.o;
    const col=bull?'#0a9e5c':'#e02d3c';
    ctx.strokeStyle=col; ctx.lineWidth=1;
    ctx.beginPath(); ctx.moveTo(x,toY(c.h)); ctx.lineTo(x,toY(c.l)); ctx.stroke();
    ctx.fillStyle=bull?'rgba(10,158,92,0.8)':'rgba(224,45,60,0.8)';
    const t=toY(Math.max(c.o,c.c)),bh=Math.max(1,toY(Math.min(c.o,c.c))-t);
    ctx.fillRect(x-cw/2,t,cw,bh);
  });
}

function setTf(el,tf){
  document.querySelectorAll('.tft').forEach(t=>t.classList.remove('on'));
  el.classList.add('on'); currentTf=tf;
  document.getElementById('ind-tf-label').textContent=tf.toUpperCase();
  candles=genCandles(tf==='1m'?60:tf==='5m'?40:20, 24250+Math.random()*100);
  drawChart(); updateIndicators();
}

// ── REAL DATA (NIFTY + VIX + Stats) ─────────────────────────
async function fetchRealData(){
  try{
    const res=await fetch('/api/data');
    const d=await res.json();

    document.getElementById('tb-trades').textContent=d.total_trades||0;
    document.getElementById('cap-trades').textContent=d.total_trades||0;
    document.getElementById('tb-pnl').textContent='₹'+(d.session_pnl_rs||0);
    document.getElementById('cap-pnl').textContent='₹'+(d.session_pnl_rs||0);
    document.getElementById('tb-winrate').textContent=(d.win_rate||0)+'%';

    const pill=document.getElementById('market-pill-text');
    const dot=document.getElementById('market-dot');
    pill.textContent=d.market_status;
    dot.style.background = d.market_status==='OPEN' ? '#4ade80' : '#9ca3af';
    dot.style.boxShadow = d.market_status==='OPEN' ? '0 0 8px #4ade80' : 'none';

    document.getElementById('risk-status-text').innerHTML = '🎯 Risk Status — <strong id="risk-pct">'+(d.risk_message||'--')+'</strong>';
    document.getElementById('risk-fill').style.width = d.risk_ok ? '20%' : '100%';
    document.getElementById('risk-fill').style.background = d.risk_ok ? 'var(--green)' : 'var(--red)';

    if(currentMarket==='nifty'){
      document.getElementById('hero-price').textContent = d.spot ? d.spot.toFixed(2) : '--';
      document.getElementById('h-vix').textContent = d.vix ? d.vix.toFixed(2) : '--';
      document.getElementById('h-open').textContent = d.day_open ? d.day_open.toFixed(2) : '--';

      const chgEl=document.getElementById('hero-chg');
      const tabEl=document.getElementById('nifty-chg-tab');
      if(d.spot && d.day_open){
        const chg=d.spot-d.day_open, pct=((chg/d.day_open)*100).toFixed(2);
        if(chg>=0){
          chgEl.style.color='#4ade80';
          chgEl.innerHTML='▲ +'+chg.toFixed(2)+' ('+pct+'%)';
          tabEl.textContent='+'+pct+'%'; tabEl.style.color='#4ade80';
        } else {
          chgEl.style.color='#f87171';
          chgEl.innerHTML='▼ '+chg.toFixed(2)+' ('+pct+'%)';
          tabEl.textContent=pct+'%'; tabEl.style.color='#f87171';
        }
      } else {
        chgEl.textContent = d.market_status==='CLOSED' ? 'Market Closed' : 'Waiting for live price...';
      }
    }

    // Real technical indicators (RSI, EMA9, EMA21, VWAP-approx)
    if(d.indicators){
      const ind=d.indicators;
      if(ind.rsi!==null && ind.rsi!==undefined){
        const rc = ind.rsi<30?'var(--green)':ind.rsi>70?'var(--red)':'var(--blue)';
        const rs = ind.rsi<30?'🟢 OVERSOLD':ind.rsi>70?'🔴 OVERBOUGHT':'⚡ NEUTRAL';
        document.getElementById('rsi-val').textContent=ind.rsi;
        document.getElementById('rsi-val').style.color=rc;
        document.getElementById('rsi-bar').style.cssText=`background:${rc};width:${ind.rsi}%`;
        document.getElementById('rsi-sig').textContent=rs;
        document.getElementById('rsi-sig').className='isig '+(ind.rsi<30?'bull':ind.rsi>70?'bear':'neu');
      } else {
        document.getElementById('rsi-val').textContent='Waiting...';
        document.getElementById('rsi-sig').textContent='--';
      }
      if(ind.ema9!==null && ind.ema9!==undefined && ind.ema21!==null){
        const bullish = ind.ema9 >= ind.ema21;
        const ec = bullish?'var(--green)':'var(--red)';
        document.getElementById('ema9-val').textContent=ind.ema9; document.getElementById('ema9-val').style.color=ec;
        document.getElementById('ema21-val').textContent=ind.ema21; document.getElementById('ema21-val').style.color=ec;
        document.getElementById('ema9-sig').textContent=bullish?'📈 BULL':'📉 BEAR';
        document.getElementById('ema9-sig').className='isig '+(bullish?'bull':'bear');
        document.getElementById('ema21-sig').textContent=bullish?'📈 BULL':'📉 BEAR';
        document.getElementById('ema21-sig').className='isig '+(bullish?'bull':'bear');
      } else {
        document.getElementById('ema9-val').textContent='Waiting...';
        document.getElementById('ema21-val').textContent='Waiting...';
      }
      if(ind.vwap_approx!==null && ind.vwap_approx!==undefined){
        document.getElementById('vwap-val').textContent=ind.vwap_approx;
        if(d.spot){
          const above = d.spot >= ind.vwap_approx;
          document.getElementById('vwap-sig').textContent = above?'🔵 ABOVE':'🔴 BELOW';
          document.getElementById('vwap-sig').className='isig '+(above?'bull':'bear');
        }
      } else {
        document.getElementById('vwap-val').textContent='Waiting...';
      }
    }

    // Real paper trade panel
    const statusEl=document.getElementById('trade-status');
    const box=document.getElementById('active-trade-box');
    const execBtn=document.getElementById('execute-btn');
    const closeBtn=document.getElementById('close-btn');
    if(d.active_trade){
      box.style.display='block';
      execBtn.style.display='none';
      closeBtn.style.display='block';
      document.getElementById('at-dir').textContent=d.active_trade.direction;
      document.getElementById('at-entry').textContent=d.active_trade.entry;
      document.getElementById('at-target').textContent=d.active_trade.target;
      document.getElementById('at-sl').textContent=d.active_trade.sl;
      const pnl=d.active_trade.live_pnl;
      const pnlEl=document.getElementById('at-pnl');
      pnlEl.textContent = pnl!==null ? (pnl>=0?'+₹'+pnl:'₹'+pnl) : '--';
      pnlEl.style.color = pnl>=0 ? 'var(--green)' : 'var(--red)';
      statusEl.textContent = 'Trade active — live NIFTY price ke saath track ho raha hai';
    } else {
      box.style.display='none';
      execBtn.style.display='block';
      closeBtn.style.display='none';
      statusEl.textContent = d.market_status==='OPEN' ? 'Ready — koi trade active nahi hai' : 'Market band hai — trade sirf 9:15–3:30 (Mon-Fri) mein execute hoga';
    }
  } catch(e){ console.error(e); }
}
fetchRealData();
setInterval(fetchRealData, 6000);

async function executeTrade(){
  try{
    const res=await fetch('/api/execute_trade',{method:'POST'});
    const d=await res.json();
    if(!d.success) alert('⚠️ '+d.message);
    fetchRealData();
  }catch(e){ alert('Error: could not execute trade'); }
}

async function closeTrade(){
  try{
    const res=await fetch('/api/close_trade',{method:'POST'});
    const d=await res.json();
    if(!d.success) alert('⚠️ '+d.message);
    fetchRealData();
  }catch(e){ alert('Error: could not close trade'); }
}

// ── DEMO GREEKS/OI/MINIS TICKS (unchanged — illustrative only) ──
function tickGreeks(){
  document.getElementById('iv-v').textContent=(13.5+Math.random()*2).toFixed(1)+'%';
  document.getElementById('gam-v').textContent=(0.004+Math.random()*0.002).toFixed(4);
  document.getElementById('tht-v').textContent='-₹'+(10+Math.random()*5).toFixed(1);
}
setInterval(tickGreeks,4000);

function tickOI(){
  const ce=(44+Math.random()*10).toFixed(1);
  const pe=(52+Math.random()*15).toFixed(1);
  document.getElementById('call-oi').textContent=ce+'L';
  document.getElementById('put-oi').textContent=pe+'L';
  document.getElementById('call-bar').style.width=Math.min(85,parseFloat(ce)/1.2)+'%';
  document.getElementById('put-bar').style.width=Math.min(85,parseFloat(pe)/1.2)+'%';
}
setInterval(tickOI,5000);

function tickMinis(){
  const bnBase=51240+(Math.random()-0.52)*30;
  document.getElementById('bn-px').textContent='₹'+bnBase.toFixed(0)+' ▼';
  const sxBase=80140+(Math.random()-0.47)*20;
  document.getElementById('sx-px').textContent='₹'+sxBase.toFixed(0)+' ▲';
  const crBase=6842+(Math.random()-0.47)*15;
  document.getElementById('cr-px').textContent='₹'+crBase.toFixed(0)+' ▲';
  const gdBase=71240+(Math.random()-0.52)*20;
  document.getElementById('gd-px').textContent='₹'+gdBase.toFixed(0)+' ▼';
  const svBase=84120+(Math.random()-0.47)*25;
  document.getElementById('sv-px').textContent='₹'+svBase.toFixed(0)+' ▲';
  document.getElementById('hdfc-v').textContent='₹'+(1842+(Math.random()-0.5)*8).toFixed(0);
  document.getElementById('sbi-v').textContent='₹'+(824+(Math.random()-0.5)*4).toFixed(0);
}
setInterval(tickMinis,3000);

const SETUPS=[
  {rsi:38.4,rc:'var(--green)',rs:'🟢 OVERSOLD',vwap:24362,ema9:24371,ema21:24344,ec:'var(--green)',es:'📈 BULL',st:24290,sc:'var(--green)',ss:'✅ BUY'},
  {rsi:68.2,rc:'var(--red)',rs:'🔴 OVERBOUGHT',vwap:24480,ema9:24460,ema21:24490,ec:'var(--red)',es:'📉 BEAR',st:24510,sc:'var(--red)',ss:'🔴 SELL'},
];
let si=0;
function updateIndicators(){
  const s=SETUPS[si]; si=(si+1)%SETUPS.length;
  // NOTE: RSI, VWAP, EMA9, EMA21 are now real (updated separately via fetchRealData).
  // Only Supertrend remains demo/illustrative here.
  document.getElementById('st-val').textContent=s.st; document.getElementById('st-val').style.color=s.sc;
  addAlert();
}

const alertMsgs=[
  {b:'🎲 DEMO',cls:'neu',m:'Yeh illustrative signal hai — asli strategy engine agla step hai'},
];
function addAlert(){
  const now=new Date();
  const t=`${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`;
  const a=alertMsgs[0];
  const list=document.getElementById('alert-list');
  const item=document.createElement('div'); item.className='alert-item';
  item.innerHTML=`<div class="alert-time">${t}</div><div class="alert-badge ${a.cls}" style="background:var(--blue2);color:var(--blue)">${a.b}</div><div class="alert-msg">${a.m}</div>`;
  list.insertBefore(item,list.firstChild);
  if(list.children.length>5)list.removeChild(list.lastChild);
}

const markets={
  banknifty:{label:'🏦 BANKNIFTY · INDEX 🎲',price:51240,open:51100,pcr:'0.88 🎲'},
  sensex:{label:'📊 SENSEX · BSE INDEX 🎲',price:80140,open:79880,pcr:'1.12 🎲'},
  crude:{label:'🛢️ CRUDE OIL · MCX 🎲',price:6842,open:6760,pcr:'N/A'},
  gold:{label:'🥇 MCX GOLD 🎲',price:71240,open:71380,pcr:'N/A'},
  silver:{label:'🥈 MCX SILVER 🎲',price:84120,open:83800,pcr:'N/A'},
  stocks:{label:'🏢 BANK STOCKS 🎲',price:1842,open:1830,pcr:'1.05 🎲'},
};

function switchMarket(el,key){
  document.querySelectorAll('.mktab').forEach(t=>t.classList.remove('on'));
  el.classList.add('on');
  currentMarket=key;
  if(key==='nifty'){
    document.getElementById('hero-label').innerHTML='🔵 NIFTY 50 · INDEX <span class="live-tag" style="background:rgba(255,255,255,0.2);color:#fff;border-color:transparent">LIVE</span>';
    fetchRealData();
  } else {
    const m=markets[key];
    document.getElementById('hero-label').textContent=m.label;
    document.getElementById('hero-price').textContent=m.price.toLocaleString('en-IN');
    document.getElementById('hero-chg').textContent='Demo data — illustrative only';
    document.getElementById('h-vix').textContent='--';
    document.getElementById('h-open').textContent=m.open.toLocaleString('en-IN');
  }
  candles=genCandles(60, (markets[key]?markets[key].price:24300)*0.995);
  drawChart(); updateIndicators();
}

function refreshAll(){
  candles=genCandles(currentTf==='1m'?60:currentTf==='5m'?40:20,24250+Math.random()*100);
  drawChart(); updateIndicators(); tickGreeks(); tickOI(); tickMinis();
}

candles=genCandles(60,24250); drawChart(); updateIndicators();
setInterval(drawChart,5000);
setInterval(updateIndicators,25000);
window.addEventListener('resize',drawChart);
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
        current_active = dict(active_trade) if active_trade else None

    live_pnl = None
    if current_active and latest_prices["nifty"] is not None:
        live_pnl = round(latest_prices["nifty"] - current_active["entry"], 2)

    return jsonify({
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
            "live_pnl": live_pnl
        } if current_active else None,
        "indicators": indicator_data,
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

@app.route("/api/execute_trade", methods=["POST"])
def api_execute_trade():
    success, message = open_paper_trade()
    return jsonify({"success": success, "message": message})

@app.route("/api/close_trade", methods=["POST"])
def api_close_trade():
    success, message = close_paper_trade()
    return jsonify({"success": success, "message": message})

# ====================== APP STARTUP ======================
# IMPORTANT: This runs at module-import time, so it works both with
# `python main.py` (local) AND with gunicorn (Render's production server).
# The old `if __name__ == "__main__":` block never fires under gunicorn,
# which is why Angel One login was silently never attempted before.

angel_login()
_poller_thread = threading.Thread(target=price_poller, daemon=True)
_poller_thread.start()
_indicator_thread = threading.Thread(target=indicator_poller, daemon=True)
_indicator_thread.start()

# ====================== RUN (local testing only) ======================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
