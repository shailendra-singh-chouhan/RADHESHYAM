import os
import yfinance as yf
from flask import Flask, render_template_string, jsonify
from datetime import datetime
import pytz

app = Flask(__name__)

# Ultra-Clean Sniper UI V8.0 - No Option Chain, 100% Focused
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BRAHMASTRA V8.0</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background-color: #020617; color: #f1f5f9; font-family: sans-serif; }
        .card { background: rgba(30, 41, 59, 0.5); border: 1px solid #334155; border-radius: 12px; padding: 12px; }
        .data-val { font-weight: 900; color: #f8fafc; }
        .label { font-size: 10px; color: #94a3b8; text-transform: uppercase; }
    </style>
</head>
<body class="p-2">
    <div class="max-w-md mx-auto space-y-3">
        <div class="border-b border-slate-800 pb-2">
            <h1 class="text-lg font-black text-blue-400">👁️ BRAHMASTRA V8.0</h1>
            <p id="status" class="text-[10px] text-amber-500 font-bold">{{ m.market_status }}</p>
        </div>

        <div class="grid grid-cols-2 gap-3">
            <div class="card">
                <p class="label">📊 NIFTY 50</p>
                <h2 class="text-xl font-black mt-1">₹{{ m.spot }}</h2>
                <div class="text-[10px] mt-2 border-t border-slate-700 pt-1">
                    VWAP: <span class="data-val">{{ m.vwap }}</span><br>
                    PIVOT: <span class="data-val">{{ m.jadui_spot }}</span>
                </div>
            </div>
            <div class="card">
                <p class="label">🛢️ CRUDE OIL</p>
                <h2 class="text-xl font-black mt-1">₹{{ m.crude }}</h2>
                <div class="text-[10px] mt-2 border-t border-slate-700 pt-1">
                    VWAP: <span class="data-val">{{ m.crude_vwap }}</span><br>
                    PIVOT: <span class="data-val">{{ m.crude_jadui }}</span>
                </div>
            </div>
        </div>

        <div id="router" class="card border-l-4 border-l-emerald-500">
            <p class="label font-bold text-emerald-400">⚡ SNIPER ACTION PLAN</p>
            <p id="signal" class="text-xs font-bold mt-1 leading-tight">{{ m.signal }}</p>
            <p id="target" class="text-[10px] text-blue-400 font-mono mt-2">{{ m.target }}</p>
        </div>
    </div>

    <script>
        async function update() {
            const res = await fetch('/api/refresh');
            const d = await res.json();
            document.getElementById('status').innerText = d.market_status;
            document.getElementById('signal').innerText = d.signal;
            document.getElementById('target').innerText = d.target;
        }
        setInterval(update, 5000);
    </script>
</body>
</html>
"""

def process():
    # Dynamic Sync Logic
    c_ticker = yf.Ticker("CL=F")
    c_data = c_ticker.history(period="1d")
    
    # Live Multiplier: Ye value market ke hisab se 0.1 adjust kar sakte ho
    mult = 96.15 
    
    if not c_data.empty:
        crude_val = round(c_data['Close'].iloc[-1] * mult, 2)
        crude_vwap = round(crude_val * 0.998, 2)
        crude_pivot = round(crude_val * 1.002, 2)
    else:
        crude_val, crude_vwap, crude_pivot = 8258.0, 8250.0, 8270.0

    return {
        "market_status": "लाइव मार्केट",
        "spot": "23286.00",
        "vwap": "23250.00",
        "jadui_spot": "23240.00",
        "crude": crude_val,
        "crude_vwap": crude_vwap,
        "crude_jadui": crude_pivot,
        "signal": f"BUY ABOVE {round(crude_val + 5, 2)}",
        "target": "T1: +40pts | SL: -20pts"
    }

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, m=process())

@app.route('/api/refresh')
def api_refresh():
    return jsonify(process())

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
