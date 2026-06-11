import os
import time
from flask import Flask, render_template_string

app = Flask(__name__)

# INSTITUTIONAL ENGINE V16 (COMMAND CENTER + PERFORMANCE ALPHA)
ENGINE_STATE = {
    "spot": 23165.40,
    "vix": 15.5,
    "velocity": 0.85,
    "trade_status": "SETUP_READY",
    "signal": "WAITING PULLBACK TO 23163.0",
    "frozen_entry": 23163.0,
    "frozen_target": 23275.0,
    "frozen_sl": 23115.0,
    "trades_won": 12,
    "trades_lost": 3,
    "chk": [True, True, True, True, True]
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="5">
    <title>GOAT PRO V16 - COMMAND CENTER</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-[#050914] text-slate-200 font-mono p-2 sm:p-4">
    <div class="max-w-md mx-auto space-y-3">
        
        <div class="flex justify-between items-center border-b border-blue-900 pb-2">
            <h1 class="text-xl font-black text-blue-500">GOAT PRO V16</h1>
            <span class="text-[10px] bg-blue-900 px-2 py-0.5 rounded animate-pulse">LIVE COMMAND</span>
        </div>

        <div class="bg-slate-900 border border-slate-800 p-3 rounded">
            <div class="flex justify-between mb-1">
                <span class="text-[9px] uppercase text-slate-400">System Efficiency</span>
                <span class="text-[9px] font-bold text-emerald-400">{{ m.win_rate }}% WIN-RATE</span>
            </div>
            <div class="w-full bg-black h-1.5 rounded-full"><div class="bg-blue-600 h-1.5 rounded-full" style="width: {{ m.win_rate }}%"></div></div>
        </div>

        <div class="grid grid-cols-2 gap-2">
            <div class="bg-slate-900 border border-slate-800 p-3 rounded text-center">
                <p class="text-[9px] text-slate-500 uppercase">NIFTY SPOT</p>
                <h2 class="text-lg font-black text-white">₹{{ m.spot }}</h2>
                <p class="text-[9px] text-emerald-400">VEL: {{ m.velocity }}</p>
            </div>
            <div class="bg-slate-900 border border-slate-800 p-3 rounded text-center">
                <p class="text-[9px] text-slate-500 uppercase">INDIA VIX</p>
                <h2 class="text-lg font-black text-purple-400">{{ m.vix }}</h2>
                <p class="text-[9px] text-slate-500">STABLE</p>
            </div>
        </div>

        <div class="bg-slate-900 border border-slate-800 p-4 rounded">
            <p class="text-[9px] font-bold text-blue-400 uppercase tracking-widest mb-3">Execution Matrix</p>
            <div class="text-sm font-black text-white mb-4">{{ m.signal }}</div>
            <div class="grid grid-cols-3 gap-2 text-center">
                <div class="bg-black p-2 rounded border border-blue-900">
                    <div class="text-[8px] text-slate-500 uppercase">Entry</div>
                    <div class="text-xs font-bold text-blue-400">{{ m.entry }}</div>
                </div>
                <div class="bg-black p-2 rounded border border-emerald-900">
                    <div class="text-[8px] text-slate-500 uppercase">Target</div>
                    <div class="text-xs font-bold text-emerald-400">{{ m.target }}</div>
                </div>
                <div class="bg-black p-2 rounded border border-red-900">
                    <div class="text-[8px] text-slate-500 uppercase">SL</div>
                    <div class="text-xs font-bold text-red-400">{{ m.sl }}</div>
                </div>
            </div>
        </div>

        <div class="bg-slate-900 border border-slate-800 p-3 rounded">
            <h3 class="text-[9px] font-bold text-slate-500 uppercase mb-2">Institutional Checklist</h3>
            <div class="grid grid-cols-5 gap-1">
                {% for status in m.chk %}
                <div class="h-2 rounded {{ 'bg-emerald-500' if status else 'bg-red-900' }}"></div>
                {% endfor %}
            </div>
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    # Performance Math
    total = ENGINE_STATE["trades_won"] + ENGINE_STATE["trades_lost"]
    wr = int((ENGINE_STATE["trades_won"] / total) * 100) if total > 0 else 0
    
    data = {
        "win_rate": wr,
        "spot": ENGINE_STATE["spot"],
        "vix": ENGINE_STATE["vix"],
        "velocity": ENGINE_STATE["velocity"],
        "signal": ENGINE_STATE["signal"],
        "entry": ENGINE_STATE["frozen_entry"],
        "target": ENGINE_STATE["frozen_target"],
        "sl": ENGINE_STATE["frozen_sl"],
        "chk": ENGINE_STATE["chk"]
    }
    return render_template_string(HTML_TEMPLATE, m=data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
