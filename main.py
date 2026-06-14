import os
import time
import datetime
import threading
import sqlite3
import json
import requests
import pyotp
from flask import Flask, render_template_string, request, jsonify
from SmartApi import SmartConnect

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except:
    YFINANCE_AVAILABLE = False

app = Flask(__name__)

# ── TOKENS ──────────────────────────────────────────────
NIFTY_TOKEN = "99926000"
VIX_TOKEN   = "99926017"

# ── MARKET HOURS ────────────────────────────────────────
def market_status():
    now = datetime.datetime.now()
    wd = now.weekday()
    t = now.time()
    if wd >= 5:
        return "CLOSED", "Weekend — Market Closed"
    if t < datetime.time(9, 0):
        return "CLOSED", "Market opens at 9:00 AM"
    if t < datetime.time(9, 15):
        return "PRE_OPEN", "Pre-Open Session"
    if t > datetime.time(15, 30):
        return "CLOSED", "Market Closed after 3:30 PM"
    return "OPEN", "Market Open"

def get_atm_strike(spot, interval=50):
    return int(round(spot / interval) * interval)

# ── CANDLE BUILDER ──────────────────────────────────────
CANDLE_5MIN = []
_candle_current = {"open": 0, "high": 0, "low": 0, "close": 0, "time": None}

def update_candle(price):
    global _candle_current, CANDLE_5MIN
    now = datetime.datetime.now()
    minute = now.minute
    if _candle_current["time"] is None:
        _candle_current = {"open": price, "high": price, "low": price, "close": price, "time": now}
        return
    prev_slot = int(_candle_current["time"].minute / 5)
    curr_slot = int(minute / 5)
    if curr_slot != prev_slot:
        CANDLE_5MIN.append(dict(_candle_current))
        if len(CANDLE_5MIN) > 100:
            CANDLE_5MIN.pop(0)
        _candle_current = {"open": price, "high": price, "low": price, "close": price, "time": now}
    else:
        _candle_current["high"] = max(_candle_current["high"], price)
        _candle_current["low"] = min(_candle_current["low"], price)
        _candle_current["close"] = price
        _candle_current["time"] = now

# ── DATABASE ────────────────────────────────────────────
DB_PATH = "/tmp/goat_paper.db"

def db_init():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS paper_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, direction TEXT, entry_price REAL, 
        exit_price REAL, target REAL, sl REAL, qty INTEGER DEFAULT 1,
        setup TEXT, source TEXT DEFAULT 'MANUAL', note TEXT, post_note TEXT,
        exit_reason TEXT, emotion TEXT, entry_time TEXT, exit_time TEXT,
        pnl REAL, status TEXT DEFAULT 'OPEN', decision_quality TEXT DEFAULT '—',
        emotion_score INTEGER DEFAULT 0, atm_strike INTEGER DEFAULT 0,
        option_type TEXT DEFAULT 'CE'
    )""")
    con.commit()
    con.close()

db_init()

def db_open_trade():
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    if not row: return None
    cols = ['id','direction','entry_price','exit_price','target','sl','qty','setup','source','note','post_note','exit_reason','emotion','entry_time','exit_time','pnl','status','decision_quality','emotion_score','atm_strike','option_type']
    return dict(zip(cols, row))

def db_closed_trades(limit=50):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT * FROM paper_trades WHERE status='CLOSED' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    cols = ['id','direction','entry_price','exit_price','target','sl','qty','setup','source','note','post_note','exit_reason','emotion','entry_time','exit_time','pnl','status','decision_quality','emotion_score','atm_strike','option_type']
    return [dict(zip(cols, r)) for r in rows]

def db_insert_trade(t):
    con = sqlite3.connect(DB_PATH)
    con.execute("""INSERT INTO paper_trades 
        (direction,entry_price,target,sl,qty,setup,source,note,entry_time,status,atm_strike,option_type)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (t['direction'], t['entry_price'], t['target'], t['sl'], t.get('qty',1),
         t['setup'], t.get('source','MANUAL'), t.get('note',''), t['entry_time'],
         'OPEN', t.get('atm_strike',0), t.get('option_type','CE')))
    con.commit()
    con.close()

def db_close_trade(trade_id, exit_price, exit_reason, post_note, emotion, pnl):
    con = sqlite3.connect(DB_PATH)
    con.execute("""UPDATE paper_trades SET exit_price=?, exit_reason=?, post_note=?, 
        emotion=?, exit_time=?, pnl=?, status='CLOSED' WHERE id=?""",
        (exit_price, exit_reason, post_note, emotion, time.strftime("%H:%M:%S"), pnl, trade_id))
    con.commit()
    con.close()

def db_clear_trades():
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM paper_trades")
    con.commit()
    con.close()

def calc_stats(trades):
    if not trades:
        return {"total":0, "wins":0, "losses":0, "win_rate":0, "total_pnl":0}
    wins = [t for t in trades if (t.get('pnl') or 0) > 0]
    total = len(trades)
    wr = round(len(wins)/total*100) if total else 0
    tot_pnl = round(sum(t.get('pnl') or 0 for t in trades), 1)
    return {"total":total, "wins":len(wins), "losses":total-len(wins), "win_rate":wr, "total_pnl":tot_pnl}

# ── SESSION & DATA ──────────────────────────────────────
SESSION_CACHE = {"obj": None, "logged_in_at": 0}

def get_session():
    if SESSION_CACHE["obj"] and time.time() - SESSION_CACHE["logged_in_at"] < 3000:
        return SESSION_CACHE["obj"], None
    try:
        totp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now()
        obj = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
        s = obj.generateSession(os.environ["ANGEL_CLIENT_ID"], os.environ["ANGEL_MPIN"], totp)
        if s.get("status"):
            SESSION_CACHE.update({"obj": obj, "logged_in_at": time.time()})
            return obj, None
        return None, "Login Failed"
    except Exception as e:
        return None, str(e)

def get_nifty_yfinance():
    if not YFINANCE_AVAILABLE: return None
    try:
        return float(yf.Ticker("^NSEI").fast_info['last_price'])
    except:
        return None

# ── ENGINE ──────────────────────────────────────────────
ENGINE = {
    "last_update": 0, "last_spot": 0, "velocity": 0,
    "status": "BLOCKED", "signal": "Initializing...",
    "entry":0, "target":0, "sl":0, "atm_strike":0, "option_type":"CE",
    "session_pnl":0, "trades_total":0, "trades_won":0, "trades_lost":0,
    "data_source":"—"
}

def run_pipeline():
    global ENGINE
    now = time.time()
    if ENGINE.get("payload") and now - ENGINE["last_update"] < 5:
        return ENGINE["payload"]

    mstatus, mmsg = market_status()
    if mstatus != "OPEN":
        payload = {"spot": ENGINE.get("last_spot",0), "vix":15, "status":"BLOCKED", "signal":mmsg,
                   "market_status":mstatus, "market_msg":mmsg, "candles":CANDLE_5MIN[-15:]}
        ENGINE["payload"] = payload
        return payload

    # Fetch Data
    obj, err = get_session()
    spot = vix = None
    data_source = "Angel"
    if obj:
        try:
            nr = obj.ltpData("NSE", "NIFTY", NIFTY_TOKEN)
            vr = obj.ltpData("NSE", "INDIAVIX", VIX_TOKEN)
            if nr.get("status"): spot = float(nr["data"]["ltp"])
            if vr.get("status"): vix = float(vr["data"]["ltp"])
        except:
            pass

    if not spot:
        spot = get_nifty_yfinance()
        data_source = "yfinance"

    if not spot:
        return {"error": "No market data"}

    update_candle(spot)
    vel = round(spot - ENGINE["last_spot"], 2) if ENGINE["last_spot"] else 0
    ENGINE["last_spot"] = spot
    ENGINE["velocity"] = vel
    atm = get_atm_strike(spot)

    # Simple Signal Logic
    if vel > 0.5:
        direction = "LONG"
        option_type = "CE"
    elif vel < -0.5:
        direction = "SHORT"
        option_type = "PE"
    else:
        direction = ENGINE.get("direction", "LONG")
        option_type = ENGINE.get("option_type", "CE")

    ENGINE["direction"] = direction
    ENGINE["option_type"] = option_type
    ENGINE["atm_strike"] = atm
    ENGINE["data_source"] = data_source

    payload = {
        "spot": round(spot, 2),
        "vix": round(vix or 15, 2),
        "velocity": vel,
        "status": ENGINE["status"],
        "signal": ENGINE["signal"],
        "entry": ENGINE["entry"],
        "target": ENGINE["target"],
        "sl": ENGINE["sl"],
        "atm_strike": atm,
        "option_type": option_type,
        "direction": direction,
        "market_status": mstatus,
        "market_msg": mmsg,
        "data_source": data_source,
        "candles": CANDLE_5MIN[-20:],
        "total": ENGINE["trades_total"],
        "win_rate": 0,
        "pnl": round(ENGINE["session_pnl"], 1)
    }
    ENGINE["payload"] = payload
    ENGINE["last_update"] = now
    return payload

# ── BEAUTIFUL UI TEMPLATE ─────────────────────────────────
TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🐐 GOAT PRO</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
<style>
    body { background:#0a0f1c; color:#e2e8f0; font-family: system-ui; }
    .glass { background:rgba(15,23,42,0.9); backdrop-filter:blur(12px); }
</style>
</head>
<body class="min-h-screen p-6">
<div class="max-w-7xl mx-auto">
    <div class="flex justify-between items-center mb-8">
        <h1 class="text-5xl font-bold flex items-center gap-4"><span>🐐</span> GOAT PRO</h1>
        <div class="text-right">
            <div id="clock" class="font-mono text-2xl"></div>
            <div class="text-sm text-slate-400">{{ m.data_source }}</div>
        </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <!-- SIGNAL -->
        <div class="lg:col-span-2 glass rounded-3xl p-10 border border-slate-700">
            <div class="flex gap-8 items-center">
                <div class="text-8xl">{% if m.status == 'TRADE_ACTIVE' %}🚀{% else %}📊{% endif %}</div>
                <div>
                    <div class="text-4xl font-bold mb-4">{{ m.signal }}</div>
                    {% if m.status == 'TRADE_ACTIVE' %}
                    <div class="grid grid-cols-3 gap-8">
                        <div>Entry: <span class="block text-2xl">{{ m.entry }}</span></div>
                        <div class="text-emerald-400">Target: <span class="block text-2xl">{{ m.target }}</span></div>
                        <div class="text-red-400">SL: <span class="block text-2xl">{{ m.sl }}</span></div>
                    </div>
                    {% endif %}
                </div>
            </div>
        </div>

        <!-- SPOT & VIX -->
        <div class="glass rounded-3xl p-8 flex flex-col justify-center">
            <div class="text-center">
                <div class="text-6xl font-bold text-white">{{ m.spot }}</div>
                <div class="text-emerald-400 text-xl mt-2">NIFTY • {{ m.velocity }} pts</div>
            </div>
            <div class="mt-8 text-center">
                <div class="text-5xl font-bold {% if m.vix < 18 %}text-emerald-400{% else %}text-orange-400{% endif %}">{{ m.vix }}</div>
                <div class="text-sm text-slate-400">INDIA VIX</div>
            </div>
        </div>
    </div>

    <!-- CHART -->
    <div class="glass rounded-3xl p-6 mt-6" style="height:420px">
        <div id="chart"></div>
    </div>

    <div class="mt-8 text-center text-slate-500 text-sm">
        Refreshing every 7 seconds • Paper Trading Only
    </div>
</div>

<script>
setInterval(() => document.getElementById('clock').textContent = new Date().toLocaleTimeString('en-IN'), 1000);

const chart = LightweightCharts.createChart(document.getElementById('chart'), {
    width: 1100, height: 400,
    layout: { background: { color: '#0a0f1c' }, textColor: '#94a3b8' }
});
const candleSeries = chart.addCandlestickSeries();
const candles = {{ m.candles | tojson | safe }};
if (candles.length) {
    candleSeries.setData(candles.map((c,i) => ({time:i, open:c.open, high:c.high, low:c.low, close:c.close})));
}

setInterval(() => location.reload(), 7000);
</script>
</body>
</html>"""

# ── ROUTES ──────────────────────────────────────────────
@app.route("/")
def index():
    data = run_pipeline()
    if "error" in data:
        return f"<h1 style='color:red;padding:50px'>Error: {data['error']}<br><br>Check Angel One Environment Variables</h1>"
    return render_template_string(TEMPLATE, m=data)

@app.route("/ping")
def ping():
    return jsonify({"status": "alive"})

@app.route("/paper/exit", methods=["POST"])
def paper_exit():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("🐐 GOAT PRO Started")
    app.run(host="0.0.0.0", port=port)
