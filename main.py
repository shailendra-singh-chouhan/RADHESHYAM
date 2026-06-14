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

# ── MARKET HOURS GUARD ──────────────────────────────────
def market_status():
    now = datetime.datetime.now()
    wd  = now.weekday()
    t   = now.time()
    if wd >= 5:
        return "CLOSED", "Weekend — Market Closed"
    if t < datetime.time(9, 0):
        return "CLOSED", "Market opens at 9:00 AM"
    if t < datetime.time(9, 15):
        return "PRE_OPEN", "Pre-Open Session (9:00-9:15) — No Trades"
    if t > datetime.time(15, 30):
        return "CLOSED", "Market Closed after 3:30 PM"
    return "OPEN", "Market Open"

# ── ATM STRIKE CALCULATOR ───────────────────────────────
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
        _candle_current["high"]  = max(_candle_current["high"], price)
        _candle_current["low"]   = min(_candle_current["low"], price)
        _candle_current["close"] = price
        _candle_current["time"]  = now

def is_new_candle_closed():
    now = datetime.datetime.now()
    return len(CANDLE_5MIN) > 0 and int(CANDLE_5MIN[-1]["time"].minute / 5) != int(now.minute / 5)

# ── SQLITE ──────────────────────────────────────────────
DB_PATH = "/tmp/goat_paper.db"

def db_init():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS paper_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        direction TEXT, entry_price REAL, exit_price REAL,
        target REAL, sl REAL, qty INTEGER DEFAULT 1,
        setup TEXT, source TEXT DEFAULT 'MANUAL',
        note TEXT, post_note TEXT, exit_reason TEXT,
        emotion TEXT, entry_time TEXT, exit_time TEXT,
        pnl REAL, status TEXT DEFAULT 'OPEN',
        decision_quality TEXT DEFAULT '—',
        emotion_score INTEGER DEFAULT 0,
        atm_strike INTEGER DEFAULT 0,
        option_type TEXT DEFAULT 'CE'
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS introspection (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, rule_followed INTEGER, sl_skip TEXT,
        revenge TEXT, discipline INTEGER, tomorrow_rule TEXT,
        created_at TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS decision_quality (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, dq_score INTEGER, breakdown TEXT, created_at TEXT
    )""")
    con.commit()
    con.close()

db_init()

# ... (keep all db_ functions exactly as in the original app.py) ...

def db_open_trade():
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    if not row: return None
    cols = ['id','direction','entry_price','exit_price','target','sl','qty','setup','source',
            'note','post_note','exit_reason','emotion','entry_time','exit_time','pnl','status',
            'decision_quality','emotion_score','atm_strike','option_type']
    return dict(zip(cols, row))

def db_closed_trades(limit=50):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT * FROM paper_trades WHERE status='CLOSED' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    cols = ['id','direction','entry_price','exit_price','target','sl','qty','setup','source',
            'note','post_note','exit_reason','emotion','entry_time','exit_time','pnl','status',
            'decision_quality','emotion_score','atm_strike','option_type']
    return [dict(zip(cols, r)) for r in rows]

# ... (keep db_insert_trade, db_close_trade, db_clear_trades, db_add_intro, db_get_intros, db_add_dq, db_get_dqs, calc_dq_score, calc_stats exactly as original) ...

# ── SESSION, YFINANCE, TELEGRAM, GOAT BRAIN, ENGINE (keep exactly as in original) ...

# (All the ENGINE logic, run_pipeline, get_session, etc. remain unchanged from the provided app.py)

# ── ENHANCED UI TEMPLATE (Integrated modern shadcn-inspired design) ────────────────────────────────────────
TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🐐 GOAT PRO Trading</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.6.0/css/all.min.css">
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        body { font-family: 'Inter', system-ui, sans-serif; }
        .glass { background: rgba(15, 23, 42, 0.8); backdrop-filter: blur(12px); }
        .card { transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); }
        .card:hover { transform: translateY(-2px); }
        .signal-active { animation: pulse 2s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.8; } }
        .candle-up { color: #22c55e; }
        .candle-down { color: #ef4444; }
    </style>
</head>
<body class="bg-[#0a0f1c] text-slate-200 min-h-screen">
<div class="flex h-screen">
    <!-- SIDEBAR -->
    <div class="w-72 bg-[#111827] border-r border-slate-800 flex flex-col">
        <div class="p-6 border-b border-slate-800">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 bg-yellow-500 rounded-2xl flex items-center justify-center text-2xl">🐐</div>
                <div>
                    <h1 class="text-2xl font-bold tracking-tight">GOAT PRO</h1>
                    <p class="text-xs text-emerald-400">Paper Trading v2.0</p>
                </div>
            </div>
        </div>
        
        <div class="p-4 flex-1 overflow-auto">
            <div class="space-y-6">
                <!-- Market Status -->
                <div>
                    <div class="text-xs uppercase tracking-widest text-slate-500 mb-2 px-3">MARKET</div>
                    <div class="glass rounded-2xl p-4 border border-slate-700">
                        <div class="flex justify-between items-center">
                            <span class="text-sm">NIFTY 50</span>
                            <span id="market-status" class="px-3 py-1 text-xs font-medium rounded-full 
                                {% if m.market_status == 'OPEN' %}bg-emerald-500/20 text-emerald-400
                                {% elif m.market_status == 'PRE_OPEN' %}bg-amber-500/20 text-amber-400
                                {% else %}bg-red-500/20 text-red-400{% endif %}">
                                {{ m.market_msg }}
                            </span>
                        </div>
                    </div>
                </div>

                <!-- Quick Stats -->
                <div class="grid grid-cols-2 gap-3">
                    <div class="glass rounded-2xl p-4 border border-slate-700">
                        <div class="text-xs text-slate-400">Spot</div>
                        <div id="spot-value" class="text-3xl font-semibold text-white">{{ m.spot }}</div>
                    </div>
                    <div class="glass rounded-2xl p-4 border border-slate-700">
                        <div class="text-xs text-slate-400">VIX</div>
                        <div id="vix-value" class="text-3xl font-semibold {% if m.vix < 18 %}text-emerald-400{% else %}text-orange-400{% endif %}">{{ m.vix }}</div>
                    </div>
                </div>

                <!-- ATM -->
                <div class="glass rounded-2xl p-5 border border-slate-700">
                    <div class="flex justify-between text-sm mb-3">
                        <span class="text-slate-400">ATM Strike</span>
                        <span class="font-mono text-xl font-bold text-blue-400">{{ m.atm_strike }}</span>
                    </div>
                    <div class="flex gap-2">
                        <div class="flex-1 bg-emerald-500/10 text-emerald-400 text-center py-2 rounded-xl text-sm font-medium">CE</div>
                        <div class="flex-1 bg-red-500/10 text-red-400 text-center py-2 rounded-xl text-sm font-medium">PE</div>
                    </div>
                </div>
            </div>
        </div>

        <div class="p-4 border-t border-slate-800 mt-auto">
            <div onclick="location.reload()" class="cursor-pointer flex items-center justify-center gap-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-2xl py-3 text-sm font-medium">
                <i class="fas fa-sync"></i> Refresh
            </div>
        </div>
    </div>

    <!-- MAIN CONTENT -->
    <div class="flex-1 flex flex-col overflow-hidden">
        <!-- Top Bar -->
        <div class="h-16 border-b border-slate-800 bg-[#111827] flex items-center px-8 justify-between">
            <div class="flex items-center gap-8">
                <div class="flex items-center gap-2 text-emerald-400">
                    <i class="fas fa-circle text-xs animate-pulse"></i>
                    <span class="font-medium">LIVE</span>
                </div>
                <div class="text-slate-400 text-sm font-mono" id="current-time"></div>
            </div>
            
            <div class="flex items-center gap-6 text-sm">
                <div class="flex items-center gap-2">
                    <span class="text-slate-400">Data:</span>
                    <span class="font-medium text-amber-400">{{ m.data_source }}</span>
                </div>
                <div onclick="clearTrades()" class="cursor-pointer text-red-400 hover:text-red-500 flex items-center gap-1.5">
                    <i class="fas fa-trash"></i>
                    <span class="text-xs">CLEAR</span>
                </div>
            </div>
        </div>

        <!-- Main Area -->
        <div class="flex-1 p-8 overflow-auto">
            <!-- SIGNAL -->
            <div class="mb-8">
                <div id="signal-box" class="glass rounded-3xl p-8 border 
                    {% if m.status == 'TRADE_ACTIVE' %}border-emerald-500 bg-emerald-950/30
                    {% elif m.status == 'SETUP_READY' %}border-amber-500 bg-amber-950/30
                    {% else %}border-slate-700{% endif %}">
                    <div class="flex items-start gap-6">
                        <div class="text-6xl">
                            {% if m.status == 'TRADE_ACTIVE' %}🚀{% elif m.status == 'SETUP_READY' %}📈{% else %}⏳{% endif %}
                        </div>
                        <div class="flex-1">
                            <div id="signal-text" class="text-3xl font-semibold mb-2">{{ m.signal }}</div>
                            {% if m.status == 'TRADE_ACTIVE' %}
                            <div class="grid grid-cols-3 gap-8 text-sm mt-6">
                                <div>
                                    <div class="text-slate-400 text-xs">ENTRY</div>
                                    <div class="font-mono text-xl">{{ m.entry }}</div>
                                </div>
                                <div>
                                    <div class="text-slate-400 text-xs">TARGET</div>
                                    <div class="font-mono text-xl text-emerald-400">{{ m.target }}</div>
                                </div>
                                <div>
                                    <div class="text-slate-400 text-xs">STOP LOSS</div>
                                    <div class="font-mono text-xl text-red-400">{{ m.sl }}</div>
                                </div>
                            </div>
                            {% endif %}
                        </div>
                    </div>
                </div>
            </div>

            <!-- GOAT BRAIN -->
            {% if m.brain %}
            <div class="glass rounded-3xl p-6 mb-8 border border-yellow-500/30">
                <div class="uppercase text-xs tracking-widest text-yellow-400 mb-3 flex items-center gap-2">
                    <i class="fas fa-brain"></i> GOAT INTELLIGENCE
                </div>
                <div class="text-slate-300 whitespace-pre-line leading-relaxed">{{ m.brain }}</div>
            </div>
            {% endif %}

            <!-- CHART -->
            <div class="glass rounded-3xl p-6 mb-8 border border-slate-700">
                <div class="flex justify-between items-center mb-6">
                    <div class="font-semibold">5-Minute Candles</div>
                    <div class="text-xs text-slate-400">Last 20 candles</div>
                </div>
                <div id="chart" class="w-full" style="height: 320px;"></div>
            </div>

            <!-- CHECKLIST + STATS -->
            <div class="grid grid-cols-12 gap-6">
                <!-- Checklist -->
                <div class="col-span-7 glass rounded-3xl p-6 border border-slate-700">
                    <div class="flex items-center justify-between mb-6">
                        <div class="font-semibold">5-Point GOAT Checklist</div>
                        <div class="px-5 py-1.5 bg-slate-800 rounded-2xl text-sm font-mono">
                            {{ m.pass_count }}/5
                        </div>
                    </div>
                    {% set labels = ['Above Round Level', 'Positive Velocity', 'Distance from Round OK', 'VIX < 18', 'Strike Range Valid'] %}
                    {% for i in range(5) %}
                    <div class="flex items-center gap-4 py-3 border-b border-slate-800 last:border-0">
                        <div class="w-8 h-8 flex items-center justify-center rounded-2xl text-xl
                            {% if m.chk[i] %}bg-emerald-500/20 text-emerald-400{% else %}bg-red-500/20 text-red-400{% endif %}">
                            {% if m.chk[i] %}✓{% else %}✕{% endif %}
                        </div>
                        <div class="flex-1">{{ labels[i] }}</div>
                    </div>
                    {% endfor %}
                </div>

                <!-- Session Stats -->
                <div class="col-span-5 space-y-6">
                    <div class="glass rounded-3xl p-6 border border-slate-700">
                        <div class="grid grid-cols-2 gap-6">
                            <div>
                                <div class="text-xs text-slate-400">TRADES</div>
                                <div class="text-4xl font-semibold mt-1">{{ m.total }}</div>
                            </div>
                            <div>
                                <div class="text-xs text-slate-400">WIN RATE</div>
                                <div class="text-4xl font-semibold mt-1 text-emerald-400">{{ m.win_rate }}%</div>
                            </div>
                        </div>
                        <div class="h-2 bg-slate-800 rounded-full mt-6 overflow-hidden">
                            <div class="h-full bg-gradient-to-r from-emerald-400 to-teal-400" style="width: {{ m.win_rate }}%"></div>
                        </div>
                    </div>

                    <div class="glass rounded-3xl p-6 border border-slate-700 text-center">
                        <div class="text-xs text-slate-400">SESSION P&amp;L</div>
                        <div class="text-5xl font-bold mt-2 {% if m.pnl >= 0 %}text-emerald-400{% else %}text-red-400{% endif %}">
                            {{ '+' if m.pnl >= 0 else '' }}{{ m.pnl }}
                        </div>
                        <div class="text-xs text-slate-500 mt-1">points</div>
                    </div>
                </div>
            </div>
        </div>

        <!-- TABS -->
        <div class="border-t border-slate-800 bg-[#111827]">
            <div class="flex border-b border-slate-800">
                <button onclick="switchTab(0)" class="tab-button flex-1 py-5 text-center font-medium border-b-2 border-transparent hover:text-white active" id="tab-0">JOURNAL</button>
                <button onclick="switchTab(1)" class="tab-button flex-1 py-5 text-center font-medium border-b-2 border-transparent hover:text-white" id="tab-1">EXIT TRADE</button>
                <button onclick="switchTab(2)" class="tab-button flex-1 py-5 text-center font-medium border-b-2 border-transparent hover:text-white" id="tab-2">STATS</button>
            </div>

            <!-- JOURNAL TAB -->
            <div id="content-0" class="tab-content p-8">
                {% if open_trade %}
                <div class="glass rounded-3xl p-8 border border-emerald-500/30 mb-8">
                    <div class="flex justify-between">
                        <div>
                            <span class="px-4 py-1 bg-emerald-500/10 text-emerald-400 text-sm font-medium rounded-2xl">{{ open_trade.direction }}</span>
                            <span class="ml-4 text-2xl font-semibold">{{ open_trade.atm_strike }} {{ open_trade.option_type }}</span>
                        </div>
                        <div class="text-right">
                            <div class="text-emerald-400 font-mono text-xl">+{{ open_trade.entry_price }}</div>
                        </div>
                    </div>
                </div>
                {% endif %}

                <div class="space-y-4">
                    {% for t in closed_trades[:8] %}
                    <div class="glass rounded-2xl p-5 flex justify-between items-center border {% if t.pnl and t.pnl > 0 %}border-emerald-900{% else %}border-red-900{% endif %}">
                        <div>
                            <span class="{% if t.direction == 'LONG' %}text-emerald-400{% else %}text-red-400{% endif %} font-medium">{{ t.direction }}</span>
                            <span class="ml-3 text-slate-400">{{ t.atm_strike }} {{ t.option_type }}</span>
                        </div>
                        <div class="text-right">
                            <div class="font-mono {% if t.pnl and t.pnl > 0 %}text-emerald-400{% else %}text-red-400{% endif %}">{{ '+' if t.pnl and t.pnl > 0 else '' }}{{ t.pnl or 0 }} pts</div>
                            <div class="text-xs text-slate-500">{{ t.exit_reason }}</div>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>

            <!-- EXIT TAB -->
            <div id="content-1" class="tab-content p-8 hidden">
                {% if open_trade %}
                <div class="max-w-md mx-auto glass rounded-3xl p-8">
                    <h3 class="font-semibold text-xl mb-6">Close Position</h3>
                    <input id="exit-price" type="number" value="{{ m.spot }}" class="w-full bg-slate-900 border border-slate-700 rounded-2xl px-5 py-4 text-2xl mb-6">
                    <select id="exit-reason" class="w-full bg-slate-900 border border-slate-700 rounded-2xl px-5 py-4 mb-6">
                        <option>Target Hit</option>
                        <option>Stop Loss Hit</option>
                        <option>Manual Exit</option>
                        <option>Time Based</option>
                    </select>
                    <button onclick="executeExit({{ open_trade.id }}, '{{ open_trade.direction }}', {{ open_trade.entry_price }})" 
                            class="w-full py-6 bg-red-600 hover:bg-red-700 rounded-3xl text-lg font-semibold">
                        CONFIRM EXIT
                    </button>
                </div>
                {% else %}
                <div class="text-center py-20 text-slate-400">No open trade</div>
                {% endif %}
            </div>

            <!-- STATS TAB -->
            <div id="content-2" class="tab-content p-8 hidden">
                <div class="grid grid-cols-2 gap-6">
                    <div class="glass rounded-3xl p-8">
                        <div class="text-sm text-slate-400 mb-2">TOTAL TRADES</div>
                        <div class="text-6xl font-bold">{{ stats.total }}</div>
                    </div>
                    <div class="glass rounded-3xl p-8">
                        <div class="text-sm text-slate-400 mb-2">EXPECTANCY</div>
                        <div class="text-6xl font-bold {% if stats.expectancy >= 0 %}text-emerald-400{% else %}text-red-400{% endif %}">{{ stats.expectancy }}</div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>

<script>
function updateClock() {
    const el = document.getElementById('current-time');
    setInterval(() => {
        el.textContent = new Date().toLocaleTimeString('en-IN', {hour: '2-digit', minute:'2-digit', second:'2-digit'});
    }, 1000);
}
updateClock();

let chart;
function initChart(candles) {
    const chartContainer = document.getElementById('chart');
    if (!chartContainer) return;
    chartContainer.innerHTML = '';
    chart = LightweightCharts.createChart(chartContainer, {
        width: chartContainer.clientWidth,
        height: 320,
        layout: { background: { color: '#111827' }, textColor: '#94a3b8' },
        grid: { vertLines: { color: '#334155' }, horzLines: { color: '#334155' } },
    });
    const candleSeries = chart.addCandlestickSeries();
    const data = candles.map((c, idx) => ({
        time: idx,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close
    }));
    candleSeries.setData(data);
}

function switchTab(n) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
    document.getElementById('content-' + n).classList.remove('hidden');
    document.querySelectorAll('.tab-button').forEach(el => el.classList.remove('active', 'border-emerald-400'));
    document.getElementById('tab-' + n).classList.add('active', 'border-emerald-400');
}

function executeExit(tradeId, direction, entry) {
    const price = parseFloat(document.getElementById('exit-price').value);
    fetch('/paper/exit', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            trade_id: tradeId,
            exit_price: price,
            direction: direction,
            entry_price: entry
        })
    }).then(() => location.reload());
}

function clearTrades() {
    if (confirm("Clear all trade history?")) {
        fetch('/paper/clear', {method: 'POST'}).then(() => location.reload());
    }
}

// Auto refresh
setInterval(() => location.reload(), 8000);

// Initialize chart on load
window.onload = () => {
    const candles = {{ m.candles | tojson | safe }};
    if (candles && candles.length) initChart(candles);
};
</script>
</body>
</html>"""

# (All original routes remain the same)
@app.route("/")
def index():
    data = run_pipeline()
    if "error" in data:
        return f"<h1 style='color:red'>Error: {data['error']}</h1>"
    closed = db_closed_trades()
    open_t = db_open_trade()
    return render_template_string(
        TEMPLATE,
        m=data,
        open_trade=open_t,
        closed_trades=closed,
        stats=calc_stats(closed)
    )

# Keep all other routes exactly as in the original app.py (/ping, /api/data, /paper/exit, /paper/intro, /paper/clear)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("🐐 GOAT PRO starting...")
    app.run(host="0.0.0.0", port=port, debug=False)
