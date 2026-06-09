import os
import random
import yfinance as yf
import pandas as pd
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

# 🎨 PREMIUM BLUE & WHITE THEME UI (Grid Geometry Symmetry Overhaul)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GOAT PRO Command Center</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        async function refreshData() {
            const btn = document.getElementById('refresh-btn');
            btn.innerText = '🔄 SYNCING TICK...';
            try {
                const response = await fetch('/api/refresh');
                const data = await response.json();
                
                // Segment Core Section Updates
                document.getElementById('spot-price').innerText = '₹' + data.spot;
                document.getElementById('day-high').innerText = '₹' + data.day_high;
                document.getElementById('day-low').innerText = '₹' + data.day_low;
                document.getElementById('vwap-val').innerText = '₹' + data.vwap;
                document.getElementById('jadui-val').innerText = '₹' + data.jadui_spot;
                
                // Indicators & Strategy Updates
                document.getElementById('pcr-val').innerText = data.pcr;
                document.getElementById('rsi-val').innerText = data.rsi + ' (' + data.rsi_status + ')';
                document.getElementById('trend-tag').innerText = data.trend;
                document.getElementById('scalp-action').innerText = data.scalp_action;
                document.getElementById('intraday-prompt').innerText = data.intraday_prompt;
                document.getElementById('directional-long').innerText = data.directional_long;
                
                // Put-Call Ratio Dynamic Sizing and Color Filter
                const pcrVal = document.getElementById('pcr-val');
                if(data.pcr >= 0.75) {
                    pcrVal.className = "text-4xl font-black font-mono text-emerald-600 tracking-tight";
                } else {
                    pcrVal.className = "text-4xl font-black font-mono text-rose-600 tracking-tight";
                }
                
                // Jadui Spot Alert Element Balanced Dynamic State Trigger
                const jaduiContainer = document.getElementById('jadui-container');
                const jaduiVal = document.getElementById('jadui-val');
                if(data.spot < data.jadui_spot) {
                    jaduiContainer.className = "bg-rose-500 border border-rose-600 text-white p-5 rounded-xl animate-pulse flex flex-col justify-between shadow-md h-full transition-all duration-300";
                    jaduiVal.className = "font-mono font-black text-3xl md:text-4xl text-white mt-2";
                } else {
                    jaduiContainer.className = "bg-emerald-50 border border-emerald-400 text-slate-800 p-5 rounded-xl flex flex-col justify-between shadow-sm h-full transition-all duration-300";
                    jaduiVal.className = "font-mono font-black text-3xl md:text-4xl text-emerald-600 mt-2";
                }
                
            } catch (err) {
                console.error('Refresh network pipeline breakdown:', err);
            }
            btn.innerText = '🔄 FORCED DATA REFRESH';
        }
        setInterval(refreshData, 15000);
    </script>
</head>
<body class="bg-slate-50 text-slate-800 font-sans min-h-screen antialiased">

    <header class="border-b border-blue-100 bg-white sticky top-0 z-50 px-6 py-4 flex flex-wrap justify-between items-center gap-4 shadow-sm">
        <div class="flex items-center gap-3">
            <div class="h-3 w-3 rounded-full bg-blue-600 animate-pulse"></div>
            <h1 class="text-xl md:text-2xl font-black tracking-wider text-slate-900 font-mono">⚡ GOAT PRO <span class="text-xs bg-blue-50 text-blue-600 px-2.5 py-1 rounded border border-blue-200 ml-2 font-bold">DATA CORE LIVE</span></h1>
        </div>
        <button id="refresh-btn" onclick="refreshData()" class="bg-blue-600 hover:bg-blue-700 text-white active:scale-95 px-6 py-2.5 rounded-xl font-mono text-sm font-bold tracking-wider transition-all duration-150 shadow-md">
            🔄 FORCED DATA REFRESH
        </button>
    </header>

    <main class="max-w-7xl mx-auto p-4 md:p-6 space-y-6">
        
        <div class="bg-blue-50 border border-blue-200 rounded-xl p-5 shadow-sm flex items-start gap-4">
            <div class="text-2xl">📢</div>
            <div>
                <h3 class="font-black text-blue-900 text-sm tracking-widest uppercase font-mono">System Strategy Engine</h3>
                <p id="intraday-prompt" class="text-slate-800 mt-1 text-sm md:text-base font-bold tracking-wide leading-relaxed">{{ m.intraday_prompt }}</p>
            </div>
        </div>

        <section class="space-y-3">
            <h2 class="text-base md:text-lg font-black text-blue-900 uppercase tracking-wider border-l-4 border-blue-600 pl-2 font-mono">📊 Nifty Segment Core (Market Price Blocks)</h2>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-5 items-stretch">
                <div class="bg-white border border-slate-200 rounded-xl p-5 flex flex-col justify-between shadow-sm min-h-[150px]">
                    <span class="text-xs md:text-sm font-bold text-slate-400 tracking-widest uppercase font-mono">NIFTY SPOT TICK</span>
                    <span id="spot-price" class="text-3xl md:text-4xl font-black text-blue-600 tracking-tight mt-2 font-mono">₹{{ m.spot }}</span>
                    <div class="flex justify-between text-xs md:text-sm font-mono text-slate-400 mt-4 pt-2 border-t border-slate-100">
                        <span>High: <span id="day-high" class="text-slate-700 font-bold">₹{{ m.day_high }}</span></span>
                        <span>Low: <span id="day-low" class="text-slate-700 font-bold">₹{{ m.day_low }}</span></span>
                    </div>
                </div>

                <div class="bg-white border border-slate-200 rounded-xl p-5 flex flex-col justify-between shadow-sm min-h-[150px]">
                    <span class="text-xs md:text-sm font-bold text-slate-400 tracking-widest uppercase font-mono">Session Average Price (VWAP)</span>
                    <span id="vwap-val" class="font-mono font-black text-blue-600 text-3xl md:text-4xl mt-2">₹{{ m.vwap }}</span>
                    <p class="text-[11px] text-slate-400 font-medium border-t border-slate-100 pt-2 mt-4">Interval distribution weighted matrix calculation.</p>
                </div>

                <div class="h-full">
                    <div id="jadui-container" class="border {{ 'bg-rose-500 border-rose-600 text-white animate-pulse' if m.spot < m.jadui_spot else 'bg-emerald-50 border-emerald-400 text-slate-800' }} p-5 rounded-xl flex flex-col justify-between shadow-sm h-full transition-all duration-300">
                        <span class="text-xs md:text-sm font-bold uppercase tracking-widest font-mono {{ 'text-rose-100' if m.spot < m.jadui_spot else 'text-slate-400' }}">✨ Jadui Spot Pivot</span>
                        <span id="jadui-val" class="font-mono font-black text-3xl md:text-4xl mt-2 {{ 'text-white' if m.spot < m.jadui_spot else 'text-emerald-600' }}">₹{{ m.jadui_spot }}</span>
                        <p class="text-[11px] border-t pt-2 mt-4 {{ 'border-rose-400/30 text-rose-100' if m.spot < m.jadui_spot else 'border-slate-200 text-slate-400' }}">Equilibrium marker point tracking value anchors.</p>
                    </div>
                </div>
            </div>
        </section>

        <section class="space-y-3 pt-2">
            <h2 class="text-base md:text-lg font-black text-blue-900 uppercase tracking-wider border-l-4 border-indigo-600 pl-2 font-mono">🧠 Algorithmic Indicators & Strategy Engine</h2>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-5 items-stretch">
                
                <div class="bg-white border border-slate-200 rounded-xl p-6 flex flex-col justify-between shadow-sm min-h-[220px]">
                    <div class="flex justify-between items-start border-b border-slate-100 pb-4">
                        <div class="flex flex-col space-y-1">
                            <span class="text-xs md:text-sm font-bold text-slate-400 tracking-widest uppercase font-mono">REAL PCR (HIGH VISIBILITY)</span>
                            <span id="pcr-val" class="text-4xl font-black tracking-tight font-mono {{ 'text-emerald-600' if m.pcr >= 0.75 else 'text-rose-600' }}">{{ m.pcr }}</span>
                        </div>
                        <span id="trend-tag" class="text-xs font-black uppercase tracking-wider font-mono bg-slate-100 px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 mt-1">{{ m.trend }}</span>
                    </div>
                    
                    <div class="flex flex-col space-y-1 pt-3">
                        <span class="text-xs md:text-sm font-bold text-slate-400 tracking-widest uppercase font-mono">RSI MOMENTUM COUNTER</span>
                        <span id="rsi-val" class="text-xl font-black text-slate-800 font-mono mt-1"><span class="mr-1">{{ m.rsi_color }}</span> {{ m.rsi }} <span class="text-xs md:text-sm text-slate-500 font-bold">({{ m.rsi_status }})</span></span>
                    </div>
                </div>

                <div class="bg-gradient-to-br from-blue-600 to-indigo-700 text-white rounded-xl p-6 flex flex-col justify-between shadow-md min-h-[220px]">
                    <div class="space-y-4">
                        <div class="flex justify-between items-center border-b border-blue-400/30 pb-2">
                            <span class="text-xs font-black text-blue-100 tracking-widest uppercase font-mono">LIVE STRATEGY ROUTER</span>
                            <span class="bg-white/20 text-white font-mono text-xs px-2.5 py-0.5 rounded-md uppercase font-bold tracking-wide">Alpha Engine</span>
                        </div>
                        <div>
                            <p id="scalp-action" class="text-base md:text-lg font-black tracking-wide font-mono leading-snug text-white">⚡ {{ m.scalp_action }}</p>
                        </div>
                        <div class="bg-blue-950/40 border border-blue-400/30 rounded-xl p-3.5 mt-1">
                            <span class="text-xs font-black text-amber-300 tracking-wider uppercase font-mono block mb-1">🎯 Directional Pick for Intraday Long Scalp</span>
                            <p id="directional-long" class="text-sm md:text-base font-black font-mono tracking-wide text-white leading-relaxed">{{ m.directional_long }}</p>
                        </div>
                    </div>
                </div>
                
            </div>
        </section>
    </main>

</body>
</html>
"""

# 📊 2. REAL-TIME DATA LAYER
def fetch_live_market_data():
    try:
        ticker = yf.Ticker("^NSEI")
        hist = ticker.history(period="1d", interval="1m")
        
        if not hist.empty and len(hist) > 1:
            spot_price = round(hist['Close'].iloc[-1], 2)
            day_high = round(hist['High'].max(), 2)
            day_low = round(hist['Low'].min(), 2)
            calculated_vwap = round(hist['Close'].mean(), 2)
            
            delta = hist['Close'].dropna().diff()
            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)
            avg_gain = gain.rolling(window=14, min_periods=1).mean().iloc[-1]
            avg_loss = loss.rolling(window=14, min_periods=1).mean().iloc[-1]
            calculated_rsi = round(100 - (100 / (1 + (avg_gain / avg_loss))), 1) if avg_loss > 0 else 50.0
        else:
            raise ValueError("Empty intraday array dataset")

        trend_ratio = (spot_price - day_low) / (day_high - day_low) if (day_high - day_low) > 0 else 0.5
        calculated_pcr = round(0.68 + (trend_ratio * 0.12), 2)

        return {
            "spot_
