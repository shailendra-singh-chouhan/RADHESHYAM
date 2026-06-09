import os
import yfinance as yf
from flask import Flask, render_template_string, jsonify
from datetime import datetime
import pytz

app = Flask(__name__)

# Ultra-Compact Symmetrical Decision UI
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GOAT PRO DUAL CORE COMPACT</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background-color: #0b0f19; color: #e2e8f0; font-family: sans-serif; }
    </style>
</head>
<body class="p-2 text-xs">

    <div class="flex justify-between items-center border-b border-gray-800 pb-1 mb-2">
        <h1 class="text-sm font-bold text-cyan-400 tracking-wider">BRAHMASTRA DUAL CORE v2.0</h1>
        <div id="clock" class="text-gray-400 font-mono">LOADING TIME...</div>
    </div>

    <div class="grid grid-cols-2 gap-2">
        
        <div class="border border-gray-800 bg-slate-900/50 rounded p-2 flex flex-col justify-between">
            <div class="flex justify-between items-baseline border-b border-gray-800 pb-1">
                <span class="text-sm font-black text-white">NIFTY 50</span>
                <span id="nifty-price" class="text-sm font-mono font-bold text-green-400">00000.00</span>
            </div>
            
            <div class="mt-1.5 flex justify-between items-center">
                <span class="text-gray-400">Regime: <b id="nifty-regime" class="text-cyan-400">TREND DAY</b></span>
                <span id="nifty-verdict" class="bg-green-900/40 text-green-400 px-1 rounded font-bold">STRONG BULLISH</span>
            </div>

            <div class="grid grid-cols-2 gap-1 my-1.5 bg-slate-950 p-1 rounded border border-gray-800/60">
                <div class="text-center">
                    <p class="text-gray-500 text-[10px] uppercase">Trade Score</p>
                    <p id="nifty-score" class="text-base font-black text-yellow-400 font-mono">84%</p>
                </div>
                <div class="text-center border-l border-gray-800">
                    <p class="text-gray-500 text-[10px] uppercase">Grade</p>
                    <p id="nifty-grade" class="text-base font-black text-green-400">A+</p>
                </div>
            </div>

            <div class="bg-blue-950/30 border border-blue-900/50 p-1.5 rounded">
                <p class="font-bold text-blue-400">⚡ ACTION: <span id="nifty-action">BUY NIFTY 23300 CE</span></p>
                <div class="flex justify-between text-[11px] text-gray-400 mt-0.5 font-mono">
                    <span>Entry: >23250</span>
                    <span>Tgt: 23350</span>
                    <span>SL: 23180</span>
                </div>
            </div>

            <div class="mt-1.5 pt-1.5 border-t border-gray-800/50 grid grid-cols-3 text-center text-gray-400 font-mono text-[10px]">
                <div>VWAP: <span id="nifty-vwap" class="text-white">---</span></div>
                <div>PCR: <span id="nifty-pcr" class="text-white">---</span></div>
                <div>RSI: <span id="nifty-rsi" class="text-white">---</span></div>
            </div>
        </div>

        <div class="border border-gray-800 bg-slate-900/50 rounded p-2 flex flex-col justify-between">
            <div class="flex justify-between items-baseline border-b border-gray-800 pb-1">
                <span class="text-sm font-black text-white">CRUDE OIL</span>
                <span id="crude-price" class="text-sm font-mono font-bold text-red-400">0000.00</span>
            </div>
            
            <div class="mt-1.5 flex justify-between items-center">
                <span class="text-gray-400">Regime: <b id="crude-regime" class="text-yellow-500">RANGE DAY</b></span>
                <span id="crude-verdict" class="bg-gray-800 text-gray-400 px-1 rounded font-bold">NO TRADE</span>
            </div>

            <div class="grid grid-cols-2 gap-1 my-1.5 bg-slate-950 p-1 rounded border border-gray-800/60">
                <div class="text-center">
                    <p class="text-gray-500 text-[10px] uppercase">Trade Score</p>
                    <p id="crude-score" class="text-base font-black text-gray-400 font-mono">40%</p>
                </div>
                <div class="text-center border-l border-gray-800">
                    <p class="text-gray-500 text-[10px] uppercase">Grade</p>
                    <p id="crude-grade" class="text-base font-black text-red-400">Avoid</p>
                </div>
            </div>

            <div class="bg-gray-900/40 border border-gray-800 p-1.5 rounded text-gray-500">
                <p class="font-bold">⚡ ACTION: <span id="crude-action">CAPITAL PROTECTION ALERT</span></p>
                <div class="flex justify-between text-[11px] mt-0.5 font-mono">
                    <span>Confidence Too Low For Execution</span>
                </div>
            </div>

            <div class="mt-1.5 pt-1.5 border-t border-gray-800/50 grid grid-cols-3 text-center text-gray-400 font-mono text-[10px]">
                <div>VWAP: <span id="crude-vwap" class="text-white">---</span></div>
                <div>PCR: <span id="crude-pcr" class="text-white">---</span></div>
                <div>RSI: <span id="crude-rsi" class="text-white">---</span></div>
            </div>
        </div>

    </div>

    <script>
        function updateClock() {
            const now = new Date();
            document.getElementById('clock').innerText = now.toLocaleTimeString('en-US', { hour12: false });
        }
        setInterval(updateClock, 1000);

        async function dataEngine() {
            try {
                const res = await fetch('/api/refresh');
                const d = await res.json();
                
                // Nifty DOM Updates
                document.getElementById('nifty-price').innerText = d.nifty.price;
                document.getElementById('nifty-vwap').innerText = d.nifty.vwap;
                document.getElementById('nifty-pcr').innerText = d.nifty.pcr;
                document.getElementById('nifty-rsi').innerText = d.nifty.rsi;

                // Crude DOM Updates
                document.getElementById('crude-price').innerText = d.crude.price;
                document.getElementById('crude-vwap').innerText = d.crude.vwap;
                document.getElementById('crude-pcr').innerText = d.crude.pcr;
                document.getElementById('crude-rsi').innerText = d.crude.rsi;
                
            } catch (err) { console.error("Engine Error:", err); }
        }
        setInterval(dataEngine, 2000); // Polls every 2 seconds
        window.onload = dataEngine;
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/refresh')
def refresh():
    # Real Upgraded Mock Data Framework (Replace with actual Broker API / Math Engine logic)
    try:
        nifty_ticker = yf.Ticker("^NSEI")
        crude_ticker = yf.Ticker("CL=F")
        
        n_hist = nifty_ticker.history(period="1d")
        c_hist = crude_ticker.history(period="1d")
        
        n_price = round(n_hist['Close'].iloc[-1], 2) if not n_hist.empty else 23200.00
        c_price = round(c_hist['Close'].iloc[-1], 2) if not c_hist.empty else 75.50
    except Exception:
        n_price, c_price = 23250.45, 74.80  # Fallback

    return jsonify({
        "nifty": {
            "price": n_price,
            "vwap": round(n_price - 12, 1),
            "pcr": 1.15,
            "rsi": 62
        },
        "crude": {
            "price": c_price,
            "vwap": round(c_price + 0.4, 2),
            "pcr": 0.85,
            "rsi": 45
        }
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)
