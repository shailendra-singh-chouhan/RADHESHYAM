import os
import time
from flask import Flask, render_template_string

app = Flask(__name__)

# INSTITUTIONAL ENGINE V15 (RISK-MANAGEMENT FOCUSED)
ENGINE_STATE = {
    "last_update": 0,
    "trade_status": "SETUP_READY",
    "spot": 23171.6,
    "velocity": 2.85,
    "vix": 15.61,
    "frozen_entry": 23163.6,
    "confidence_score": 88  # New: Confidence Metric
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="5">
    <title>GOAT PRO V15 - ALPHA</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-[#02050b] text-slate-200 font-mono p-2">
    <div class="max-w-md mx-auto border border-blue-900 bg-[#050914] p-4 rounded shadow-2xl">
        <div class="flex justify-between items-center border-b border-blue-900 pb-2 mb-4">
            <h1 class="text-lg font-black text-blue-500">GOAT PRO V15</h1>
            <span class="text-[10px] bg-blue-900 px-2 py-1 rounded">ALPHA ENGINE</span>
        </div>

        <div class="bg-blue-950/30 p-3 rounded border border-blue-800 mb-4">
            <div class="flex justify-between mb-1">
                <span class="text-[10px] uppercase text-blue-400">System Confidence</span>
                <span class="text-[10px] font-bold text-emerald-400">{{ m.confidence }}%</span>
            </div>
            <div class="w-full bg-slate-900 h-1.5 rounded-full mb-3"><div class="bg-blue-500 h-1.5 rounded-full" style="width: {{ m.confidence }}%"></div></div>
            <div class="flex justify-between">
                <span class="text-[9px] text-slate-500">SUGGESTED LOTS:</span>
                <span class="text-[9px] font-bold text-white">2 LOTS (RISK: ₹1200)</span>
            </div>
        </div>

        <div class="text-center py-4 border-y border-slate-800 mb-4">
            <p class="text-[10px] uppercase tracking-widest text-slate-500 mb-1">Command Directive</p>
            <p class="text-lg font-black text-blue-400">ENTRY: ₹{{ m.entry }}</p>
        </div>

        <div class="space-y-1">
            {% for c in m.chk %}
            <div class="flex justify-between text-[10px] {{ 'text-emerald-500' if c else 'text-red-500' }}">
                <span>CONDITION {{ loop.index }}</span>
                <span>{{ 'PASS' if c else 'FAIL' }}</span>
            </div>
            {% endfor %}
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    # Mock Logic for V15
    data = {
        "confidence": ENGINE_STATE["confidence_score"],
        "entry": ENGINE_STATE["frozen_entry"],
        "chk": [True, True, True, True, True]
    }
    return render_template_string(HTML_TEMPLATE, m=data)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
