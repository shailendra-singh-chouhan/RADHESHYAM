# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
  GOAT PRO — Virtual Paper Trading System
  Single-file Flask app for Render deployment.
"""

import os
import datetime
from flask import Flask, jsonify

app = Flask(__name__)

# ====================== HEALTH CHECK ======================
@app.route('/health')
def health():
    return {"status": "healthy", "time": datetime.datetime.now().isoformat()}, 200

# ====================== MAIN PAGE ======================
TEMPLATE = """<!DOCTYPE html>
<html lang="hi">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>⚡ GOAT PRO - Premium</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body { background: #0f172a; color: #e2e8f0; font-family: system-ui, sans-serif; }
    .card { background: #1e2937; border-radius: 12px; }
  </style>
</head>
<body class="min-h-screen p-4">
  <div class="max-w-7xl mx-auto">
    <div class="flex justify-between items-center mb-6">
      <h1 class="text-3xl font-bold">⚡ GOAT PRO</h1>
      <div class="text-right">
        <p id="clock" class="text-xl font-mono"></p>
        <p class="text-green-400 text-sm">Market Status: <span id="mstatus">LOADING</span></p>
      </div>
    </div>

    <div class="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
      <div class="card p-4"><div class="text-blue-400">NIFTY</div><div class="text-3xl font-bold" id="nifty">--</div></div>
      <div class="card p-4"><div>BANKNIFTY</div><div class="text-3xl font-bold" id="banknifty">--</div></div>
      <div class="card p-4"><div>VIX</div><div class="text-3xl font-bold text-orange-400" id="vix">--</div></div>
      <div class="card p-4"><div>P&L</div><div class="text-3xl font-bold text-green-400" id="pnl">₹0</div></div>
      <div class="card p-4"><div>Win Rate</div><div class="text-3xl font-bold" id="winrate">0%</div></div>
    </div>

    <div class="card p-6 mb-6">
      <h2 class="text-xl mb-4">NIFTY Live Chart</h2>
      <canvas id="priceChart" height="110"></canvas>
    </div>

    <div class="grid md:grid-cols-3 gap-6">
      <div class="card p-6">
        <h3 class="text-lg mb-4">GOAT Signal</h3>
        <div id="signal" class="text-4xl font-bold text-green-400 mb-4">WAITING</div>
        <button onclick="simulateTrade()" class="w-full bg-green-600 hover:bg-green-700 py-4 rounded-xl font-bold text-lg">EXECUTE PAPER TRADE</button>
      </div>

      <div class="card p-6">
        <h3 class="text-lg mb-4">Active Trade</h3>
        <div id="active-trade" class="text-sm">No active trade</div>
      </div>

      <div class="card p-6">
        <h3 class="text-lg mb-4">Today's Trades</h3>
        <table class="w-full text-sm" id="trade-log"><tr><th>Time</th><th>Action</th><th>P&L</th></tr></table>
      </div>
    </div>

    <div class="text-center text-xs text-gray-500 mt-8">
      GOAT PRO - Virtual Paper Trading • Educational Use Only
    </div>
  </div>

  <script>
    function updateClock() {
      setInterval(() => {
        document.getElementById('clock').textContent = new Date().toLocaleTimeString('en-IN', {hour12:false}) + " IST";
      }, 1000);
    }

    let chart;
    function createChart() {
      const ctx = document.getElementById('priceChart');
      chart = new Chart(ctx, {
        type: 'line',
        data: { labels: [], datasets: [{ label: 'NIFTY', data: [], borderColor: '#22c55e', tension: 0.4 }] },
        options: { plugins: { legend: { display: false } } }
      });
    }

    async function fetchData() {
      try {
        const res = await fetch('/api/data');
        const d = await res.json();
        updateUI(d);
      } catch(e) { console.log(e); }
    }

    function updateUI(d) {
      document.getElementById('mstatus').textContent = d.market_status || 'CLOSED';
      document.getElementById('nifty').textContent = d.spot ? d.spot.toFixed(2) : '--';
      document.getElementById('banknifty').textContent = d.bn_spot ? d.bn_spot.toFixed(2) : '--';
      document.getElementById('vix').textContent = d.vix || '--';
      document.getElementById('pnl').textContent = '₹' + (d.session_pnl_rs || 0);
      document.getElementById('winrate').textContent = (d.win_rate || 0) + '%';
      document.getElementById('signal').textContent = d.signal || 'WAITING';
    }

    function simulateTrade() {
      alert("✅ Paper Trade Executed Successfully!");
    }

    updateClock();
    createChart();
    fetchData();
    setInterval(fetchData, 8000);
  </script>
</body>
</html>"""

@app.route("/")
def index():
    return TEMPLATE

@app.route("/api/data")
def api_data():
    # Placeholder data (tumhara pura logic yahan daal sakte ho)
    return jsonify({
        "spot": 24150.75,
        "bn_spot": 51280,
        "vix": 14.8,
        "session_pnl_rs": 1245,
        "win_rate": 68,
        "signal": "LONG",
        "market_status": "OPEN",
        "direction": "LONG"
    })

@app.route("/api/trades")
def api_trades():
    return jsonify({"open": None, "closed": [], "stats": {"win_rate": 68}})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
