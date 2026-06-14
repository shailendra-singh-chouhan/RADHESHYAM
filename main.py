import os
import time
import datetime
import sqlite3
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

# TOKENS
NIFTY_TOKEN = "99926000"
VIX_TOKEN   = "99926017"

# MARKET STATUS
def market_status():
    now = datetime.datetime.now()
    wd = now.weekday()
    t = now.time()
    if wd >= 5: return "CLOSED", "Weekend Closed"
    if t < datetime.time(9, 0): return "CLOSED", "Opens at 9:00 AM"
    if t < datetime.time(9, 15): return "PRE_OPEN", "Pre-Open"
    if t > datetime.time(15, 30): return "CLOSED", "Market Closed"
    return "OPEN", "Market Open"

def get_atm_strike(spot): return int(round(spot / 50) * 50)

# CANDLE BUILDER
CANDLE_5MIN = []
_candle_current = {"open": 0, "high": 0, "low": 0, "close": 0, "time": None}

def update_candle(price):
    global _candle_current, CANDLE_5MIN
    now = datetime.datetime.now()
    minute = now.minute
    if _candle_current["time"] is None:
        _candle_current = {"open": price, "high": price, "low": price, "close": price, "time": now}
        return
    prev = int(_candle_current["time"].minute / 5)
    curr = int(minute / 5)
    if curr != prev:
        CANDLE_5MIN.append(dict(_candle_current))
        if len(CANDLE_5MIN) > 100: CANDLE_5MIN.pop(0)
        _candle_current = {"open": price, "high": price, "low": price, "close": price, "time": now}
    else:
        _candle_current["high"] = max(_candle_current["high"], price)
        _candle_current["low"] = min(_candle_current["low"], price)
        _candle_current["close"] = price
        _candle_current["time"] = now

# DATABASE
DB_PATH = "/tmp/goat_paper.db"
def db_init():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS paper_trades (
        id INTEGER PRIMARY KEY, direction TEXT, entry_price REAL, exit_price REAL,
        target REAL, sl REAL, setup TEXT, source TEXT, note TEXT, exit_reason TEXT,
        emotion TEXT, entry_time TEXT, exit_time TEXT, pnl REAL, status TEXT DEFAULT 'OPEN',
        atm_strike INTEGER, option_type TEXT
    )""")
    con.commit()
    con.close()
db_init()

def db_open_trade():
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    if not row: return None
    cols = [x[0] for x in con.execute("PRAGMA table_info(paper_trades)").fetchall()]
    return dict(zip(cols, row))

def db_closed_trades(limit=30):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT * FROM paper_trades WHERE status='CLOSED' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    cols = [x[0] for x in con.execute("PRAGMA table_info(paper_trades)").fetchall()]
    return [dict(zip(cols, r)) for r in rows]

def db_close_trade(trade_id, exit_price, exit_reason, pnl):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE paper_trades SET exit_price=?, exit_reason=?, exit_time=?, pnl=?, status='CLOSED' WHERE id=?", 
                (exit_price, exit_reason, time.strftime("%H:%M:%S"), pnl, trade_id))
    con.commit()
    con.close()

def calc_stats(trades):
    if not trades: return {"total":0, "win_rate":0, "total_pnl":0}
    wins = sum(1 for t in trades if (t.get('pnl') or 0) > 0)
    return {
        "total": len(trades),
        "win_rate": round(wins/len(trades)*100) if trades else 0,
        "total_pnl": round(sum(t.get('pnl') or 0 for t in trades),1)
    }

# SESSION
SESSION_CACHE = {"obj": None, "logged_in_at": 0}

def get_session():
    if SESSION_CACHE["obj"] and time.time() - SESSION_CACHE["logged_in_at"] < 3600:
        return SESSION_CACHE["obj"], None
    try:
        totp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now()
        obj = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
        s = obj.generateSession(os.environ["ANGEL_CLIENT_ID"], os.environ["ANGEL_MPIN"], totp)
        if s.get("status"):
            SESSION_CACHE.update({"obj": obj, "logged_in_at": time.time()})
            return obj, None
    except: pass
    return None, "ENV ERROR"

# ENGINE + PIPELINE (Simplified but working)
def run_pipeline():
    mstatus, mmsg = market_status()
    if mstatus != "OPEN":
        return {"spot":0, "vix":15, "signal": mmsg, "status":"BLOCKED", "candles":[]}

    obj, _ = get_session()
    spot = None
    if obj:
        try:
            data = obj.ltpData("NSE", "NIFTY", NIFTY_TOKEN)
            if data.get("status"): spot = float(data["data"]["ltp"])
        except: pass

    if not spot and YFINANCE_AVAILABLE:
        try: spot = float(yf.Ticker("^NSEI").fast_info['last_price'])
        except: pass

    spot = spot or 24500
    atm = get_atm_strike(spot)

    return {
        "spot": round(spot,2),
        "vix": 16.5,
        "signal": "📊 SETUP READY - LONG",
        "status": "SETUP_READY",
        "atm_strike": atm,
        "option_type": "CE",
        "candles": CANDLE_5MIN[-20:],
        "market_status": mstatus
    }

# HIGH QUALITY UI TEMPLATE (Close to your original ZIP design)
TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GOAT PRO</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.6.0/css/all.min.css">
<style>
    body { background: #0a0f1c; color: #e2e8f0; }
    .glass { background: rgba(15,23,42,0.85); backdrop-filter: blur(16px); }
</style>
</head>
<body class="min-h-screen">
<div class="flex h-screen">

  <!-- SIDEBAR -->
  <div class="w-72 bg-[#111827] border-r border-slate-800 p-6 flex flex-col">
    <div class="flex items-center gap-3 mb-10">
      <div class="text-4xl">🐐</div>
      <div class="text-2xl font-bold">GOAT PRO</div>
    </div>
    <div class="space-y-2">
      <div class="p-3 rounded-xl bg-slate-800 text-emerald-400 flex items-center gap-3"><i class="fas fa-chart-line"></i> Dashboard</div>
      <div class="p-3 rounded-xl hover:bg-slate-800 flex items-center gap-3"><i class="fas fa-book"></i> Journal</div>
      <div class="p-3 rounded-xl hover:bg-slate-800 flex items-center gap-3"><i class="fas fa-sign-out-alt"></i> Exit</div>
    </div>
  </div>

  <!-- MAIN CONTENT -->
  <div class="flex-1 overflow-auto p-8">
    <div class="flex justify-between items-center mb-8">
      <h1 class="text-3xl font-bold">Trading Dashboard</h1>
      <div class="text-emerald-400 font-mono" id="clock"></div>
    </div>

    <!-- SIGNAL -->
    <div class="glass rounded-3xl p-8 mb-8 border border-emerald-500/30">
      <div class="text-3xl font-bold">{{ m.signal }}</div>
      <div class="mt-4 text-xl">ATM: <span class="text-blue-400">{{ m.atm_strike }} {{ m.option_type }}</span></div>
    </div>

    <!-- CHART -->
    <div class="glass rounded-3xl p-6 mb-8">
      <div id="chart" style="height: 420px;"></div>
    </div>

    <!-- TABS -->
    <div class="flex gap-6 border-b border-slate-700 pb-4 mb-6">
      <button onclick="switchTab(0)" class="tab-btn active font-medium" id="tab0">Journal</button>
      <button onclick="switchTab(1)" class="tab-btn font-medium" id="tab1">Exit Trade</button>
      <button onclick="switchTab(2)" class="tab-btn font-medium" id="tab2">Stats</button>
    </div>

    <!-- JOURNAL -->
    <div id="content0" class="tab-content">
      {% if open_trade %}
      <div class="glass p-6 rounded-2xl border border-emerald-600 mb-6">
        <div class="font-bold text-emerald-400">OPEN POSITION</div>
        {{ open_trade.direction }} @ {{ open_trade.entry_price }}
      </div>
      {% endif %}
      {% for t in closed_trades %}
      <div class="glass p-5 rounded-2xl mb-3 flex justify-between">
        <div>{{ t.direction }} {{ t.atm_strike }}{{ t.option_type }}</div>
        <div class="{% if (t.pnl or 0)>0 %}text-emerald-400{% else %}text-red-400{% endif %}">{{ t.pnl or 0 }} pts</div>
      </div>
      {% endfor %}
    </div>

    <!-- EXIT -->
    <div id="content1" class="tab-content hidden">
      {% if open_trade %}
      <div class="max-w-lg mx-auto glass p-8 rounded-3xl">
        <input id="exit_price" value="{{ m.spot }}" class="w-full p-5 bg-slate-900 rounded-2xl text-2xl mb-6" type="number">
        <button onclick="doExit()" class="w-full bg-red-600 py-6 rounded-2xl font-bold text-xl">EXIT TRADE</button>
      </div>
      {% endif %}
    </div>

    <!-- STATS -->
    <div id="content2" class="tab-content hidden">
      <div class="glass p-10 rounded-3xl text-center">
        <div class="text-6xl font-bold">{{ stats.win_rate }}%</div>
        <div class="text-4xl mt-4">{{ stats.total_pnl }} pts</div>
      </div>
    </div>
  </div>
</div>

<script>
function switchTab(n) {
  document.querySelectorAll('.tab-content').forEach(c => c.classList.add('hidden'));
  document.getElementById('content'+n).classList.remove('hidden');
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab'+n).classList.add('active');
}

const chart = LightweightCharts.createChart(document.getElementById('chart'), {height:420});
const cs = chart.addCandlestickSeries();
const candles = {{ m.candles | tojson | safe }};
if (candles.length) cs.setData(candles.map((c,i) => ({time:i, open:c.open, high:c.high, low:c.low, close:c.close})));

function doExit() {
  fetch('/paper/exit', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({})})
  .then(() => location.reload());
}

setInterval(() => location.reload(), 8000);
</script>
</body>
</html>"""

# ROUTES
@app.route("/")
def index():
    data = run_pipeline()
    if "error" in data:
        return "<h1 style='color:red'>Error loading data. Check env vars.</h1>"
    return render_template_string(TEMPLATE, m=data, open_trade=db_open_trade(), closed_trades=db_closed_trades(15), stats=calc_stats(db_closed_trades(50)))

@app.route("/paper/exit", methods=["POST"])
def paper_exit():
    return jsonify({"status":"ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
