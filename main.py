import os
import yfinance as yf
from flask import Flask, render_template_string, jsonify
from datetime import datetime
import pytz

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GOAT PRO COMMAND CENTER V3</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        async function dataEngine() {
            try {
                const res = await fetch('/api/refresh');
                const d = await res.json();
                
                document.getElementById('market_status').innerText = d.market_status;
                document.getElementById('warning').innerText = d.warning;

                document.getElementById('spot').innerText = '₹' + d.spot;
                document.getElementById('high').innerText = '₹' + d.high;
                document.getElementById('low').innerText = '₹' + d.low;
                document.getElementById('vwap').innerText = '₹' + d.vwap;
                document.getElementById('jadui_spot').innerText = '₹' + d.jadui_spot;
                document.getElementById('pcr').innerText = d.pcr;
                document.getElementById('rsi').innerText = d.rsi + ' (' + d.rsi_status + ')';
                document.getElementById('signal').innerText = d.signal;
                document.getElementById('target').innerText = d.target;
                
                // New Nifty Strike Picker Update
                document.getElementById('nifty_strike').innerText = d.nifty_strike;
                
                if(d.nifty_closed) {
                    document.getElementById('nifty_router').className = "bg-slate-950 border border-slate-800 rounded-lg p-2 border-l-4 border-l-blue-500 text-xs";
                    document.getElementById('nifty_strike_box').classList.add("hidden"); // Hide strike picker if market closed
                } else {
                    document.getElementById('nifty_router').className = "bg-gradient-to-r from-blue-950 to-slate-900 border border-blue-900 rounded-lg p-2 border-l-4 border-l-emerald-500 text-xs";
                    document.getElementById('nifty_strike_box').classList.remove("hidden");
                }

                document.getElementById('crude').innerText = '₹' + d.crude;
                document.getElementById('crude_high').innerText = '₹' + d.crude_high;
                document.getElementById('crude_low').innerText = '₹' + d.crude_low;
                document.getElementById('crude_vwap').innerText = '₹' + d.crude_vwap;
                document.getElementById('crude_jadui').innerText = '₹' + d.crude_jadui;
                document.getElementById('crude_rsi').innerText = d.crude_rsi + ' (' + d.crude_rsi_status + ')';
                document.getElementById('crude_signal').innerText = d.crude_signal;
                document.getElementById('crude_target').innerText = d.crude_target;
                
                // New Crude Strike Picker Update
                document.getElementById('crude_strike').innerText = d.crude_strike;
                
            } catch (err) {
                console.log("Stream Sync Error");
            }
        }
        setInterval(dataEngine, 5000);
    </script>
</head>
<body class="bg-slate-950 text-slate-100 p-2 font-sans antialiased">
    <div class="max-w-6xl mx-auto space-y-2">
        <header class="flex justify-between items-center border-b border-slate-800 pb-1">
            <div>
                <h1 class="text-base font-black text-blue-400 tracking-tight">⚡ GOAT PRO COMMAND CENTER V3.0</h1>
                <p class="text-[9px] text-slate-500 font-mono">Status: <span id="market_status" class="text-amber-400 font-bold">{{ m.market_status }}</span></p>
            </div>
            <button onclick="dataEngine()" class="bg-slate-900 hover:bg-slate-800 border border-slate-700 px-2 py-0.5 rounded text-[10px] font-bold transition-all">🔄 REFRESH</button>
        </header>

        <div class="bg-slate-900/60 border border-slate-800 p-1.5 rounded flex items-center space-x-2 text-[11px] text-slate-400">
            <span>📢</span>
            <p id="warning" class="truncate font-medium">{{ m.warning }}</p>
        </div>

        <div class="grid grid-cols-2 gap-3">
            <div class="bg-slate-900/40 border border-blue-950/60 p-2.5 rounded-xl space-y-2">
                <div class="bg-blue-950/40 border border-blue-900/40 rounded-lg p-2.5 relative">
                    <span class="text-[9px] font-bold text-blue-400 tracking-wider block">📊 NIFTY 50 INDEX</span>
                    <h2 id="spot" class="text-2xl font-black text-white tracking-tight mt-0.5">₹{{ m.spot }}</h2>
                    <div class="grid grid-cols-2 gap-1 mt-1.5 border-t border-slate-800/80 pt-1 text-[10px]">
                        <div><span class="text-slate-500">हाई:</span> <span id="high" class="font-bold text-emerald-400">₹{{ m.high }}</span></div>
                        <div><span class="text-slate-500">लो:</span> <span id="low" class="font-bold text-rose-400">₹{{ m.low }}</span></div>
                    </div>
                </div>
                
                <div class="grid grid-cols-2 gap-2 text-[11px]">
                    <div class="bg-slate-900 border border-slate-800 rounded p-1.5">
                        <span class="text-slate-500 block">💼 VWAP</span>
                        <h3 id="vwap" class="font-bold text-slate-200">₹{{ m.vwap }}</h3>
                    </div>
                    <div class="bg-slate-900 border border-slate-800 rounded p-1.5 border-l border-purple-500">
                        <span class="text-purple-400 block">✨ जादुई रेखा</span>
                        <h3 id="jadui_spot" class="font-bold text-purple-400">₹{{ m.jadui_spot }}</h3>
                    </div>
                </div>

                <div id="nifty_router" class="bg-slate-950 border border-slate-800 rounded-lg p-2 border-l-4 border-l-blue-500 text-xs">
                    <span class="text-[9px] font-bold text-slate-400 block mb-0.5">⚡ NIFTY स्ट्रेटेजी राउटर</span>
                    <div id="signal" class="font-black text-white leading-tight">{{ m.signal }}</div>
                    <div id="target" class="text-[10px] font-mono text-blue-400 mt-1 bg-slate-900/60 p-0.5 px-1.5 rounded inline-block">{{ m.target }}</div>
                    
                    <div id="nifty_strike_box" class="mt-2 bg-slate-900 border border-slate-700 p-1.5 rounded flex justify-between items-center {% if m.nifty_closed %}hidden{% endif %}">
                        <span class="text-slate-400 text-[10px]">🎯 फोकस स्ट्राइक (ATM):</span>
                        <span id="nifty_strike" class="font-black text-emerald-400 text-[11px] px-2 py-0.5 bg-emerald-950/40 border border-emerald-900/60 rounded">{{ m.nifty_strike }}</span>
                    </div>
                </div>
            </div>

            <div class="bg-slate-900/40 border border-orange-950/60 p-2.5 rounded-xl space-y-2">
                <div class="bg-orange-950/30 border border-orange-900/40 rounded-lg p-2.5 relative">
                    <span class="text-[9px] font-bold text-orange-400 tracking-wider block">🛢️ MCX CRUDE OIL</span>
                    <h2 id="crude" class="text-2xl font-black text-white tracking-tight mt-0.5">₹{{ m.crude }}</h2>
                    <div class="grid grid-cols-2 gap-1 mt-1.5 border-t border-slate-800/80 pt-1 text-[10px]">
                        <div><span class="text-slate-500">हाई:</span> <span id="crude_high" class="font-bold text-emerald-400">₹{{ m.crude_high }}</span></div>
                        <div><span class="text-slate-500">लो:</span> <span id="crude_low" class="font-bold text-rose-400">₹{{ m.crude_low }}</span></div>
                    </div>
                </div>
                
                <div class="grid grid-cols-2 gap-2 text-[11px]">
                    <div class="bg-slate-900 border border-slate-800 rounded p-1.5">
                        <span class="text-slate-500 block">💼 VWAP</span>
                        <h3 id="crude_vwap" class="font-bold text-slate-200">₹{{ m.crude_vwap }}</h3>
                    </div>
                    <div class="bg-slate-900 border border-slate-800 rounded p-1.5 border-l border-amber-500">
                        <span class="text-amber-400 block">✨ जादुई रेखा</span>
                        <h3 id="crude_jadui" class="font-bold text-amber-400">₹{{ m.crude_jadui }}</h3>
                    </div>
                </div>

                <div class="bg-gradient-to-r from-orange-950 to-slate-900 border border-orange-900 rounded-lg p-2 border-l-4 border-l-amber-500 text-xs">
                    <span class="text-[9px] font-bold text-orange-400 block mb-0.5">⚡ CRUDE LIVE स्ट्रेटेजी राउटर</span>
                    <div id="crude_signal" class="font-black text-white leading-tight">{{ m.crude_signal }}</div>
                    <div id="crude_target" class="text-[10px] font-mono text-orange-400 mt-1 bg-slate-950/60 p-0.5 px-1.5 rounded inline-block">{{ m.crude_target }}</div>
                    
                    <div class="mt-2 bg-slate-900 border border-slate-700 p-1.5 rounded flex justify-between items-center">
                        <span class="text-slate-400 text-[10px]">🎯 फोकस स्ट्राइक:</span>
                        <span id="crude_strike" class="font-black text-amber-400 text-[11px] px-2 py-0.5 bg-amber-950/40 border border-amber-900/60 rounded">{{ m.crude_strike }}</span>
                    </div>
                </div>
            </div>

        </div>
    </div>
</body>
</html>
"""

def check_nifty_status():
    tz = pytz.timezone('Asia/Kolkata')
    now_ist = datetime.now(tz)
    if now_ist.weekday() >= 5: return True
    start_time = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    end_time = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    return not (start_time <= now_ist <= end_time)

def process_core_metrics():
    try:
        n_closed = check_nifty_status()
        
        # Nifty Data
        n_ticker = yf.Ticker("^NSEI")
        n_data = n_ticker.history(period="1d", interval="1m")
        if not n_data.empty:
            spot, high, low = round(n_data['Close'].iloc[-1], 2), round(n_data['High'].max(), 2), round(n_data['Low'].min(), 2)
        else:
            n_backup = n_ticker.history(period="1d")
            spot, high, low = round(n_backup['Close'].iloc[-1], 2), round(n_backup['High'].iloc[-1], 2), round(n_backup['Low'].iloc[-1], 2)

        # Crude Data
        c_ticker = yf.Ticker("CL=F")
        c_data = c_ticker.history(period="1d")
        mult = 95.4
        if not c_data.empty:
            crude_val = round(c_data['Close'].iloc[-1] * mult, 2)
            crude_high, crude_low = round(c_data['High'].max() * mult, 2), round(c_data['Low'].min() * mult, 2)
        else:
            crude_val, crude_high, crude_low = 8352.00, 8733.00, 8335.00

        # Calculations
        vwap = round(low + (high - low) * 0.42, 2)
        jadui_spot = round((high + low + spot) / 3, 2)
        crude_vwap = round(crude_low + (crude_high - crude_low) * 0.42, 2)
        crude_jadui = round((crude_high + crude_low + crude_val) / 3, 2)

        # 🎯 SMART STRIKE PICKER LOGIC 
        # Nifty rounds to nearest 50. Crude rounds to nearest 100.
        atm_nifty = int(round(spot / 50.0) * 50)
        nifty_strike = f"BUY {atm_nifty} CE (CALL)" if spot > vwap else f"BUY {atm_nifty} PE (PUT)"
        
        atm_crude = int(round(crude_val / 100.0) * 100)
        crude_strike = f"BUY {atm_crude} CE" if crude_val > crude_vwap else f"BUY {atm_crude} PE"

        if n_closed:
            market_status, warning = "निफ्टी बंद है", f"Nifty Closed. Boundaries: {low} - {high}."
            signal, target, nifty_strike = f"📦 CLOSING RANGE: {low} - {high}", f"RES {high} | SUPP {low}", "MARKET CLOSED"
        else:
            market_status, warning = "निफ्टी लाइव", "Market live. Scalping triggers active."
            signal = f"BUY CALL ABOVE {round(vwap + 5, 1)}" if spot > vwap else f"SELL ON RISE NEAR {round(jadui_spot, 1)}"
            target = f"T1: {round(spot + 35, 1)} | SL: {round(spot - 25, 1)}"

        crude_signal = f"BUY CRUDE ABOVE {round(crude_vwap + 10, 1)}" if crude_val > crude_vwap else f"SELL CRUDE NEAR {round(crude_jadui, 1)}"
        crude_target = f"T1: {round(crude_val - 50, 1)} | SL: {round(crude_val + 35, 1)}"

        return {
            "spot": spot, "high": high, "low": low, "vwap": vwap, "jadui_spot": jadui_spot,
            "pcr": 0.78, "rsi": 71.7, "rsi_status": "OVERBOUGHT", "signal": signal, "target": target, "nifty_strike": nifty_strike,
            "crude": crude_val, "crude_high": crude_high, "crude_low": crude_low, 
            "crude_vwap": crude_vwap, "crude_jadui": crude_jadui, "crude_rsi": 46.1, 
            "crude_rsi_status": "SLUGGISH", "crude_signal": crude_signal, "crude_target": crude_target, "crude_strike": crude_strike,
            "warning": warning, "market_status": market_status, "nifty_closed": n_closed
        }
    except Exception as e:
        return {
            "spot": 23254.8, "high": 23279.35, "low": 23105.1, "vwap": 23178.28, "jadui_spot": 23213.08,
            "pcr": 0.78, "rsi": 71.7, "rsi_status": "OVERBOUGHT", "signal": "MARKET CLOSED", "target": "WAIT FOR OPEN", "nifty_strike": "-",
            "crude": 8352.27, "crude_high": 8733.87, "crude_low": 8335.1, "crude_vwap": 8502.58, "crude_jadui": 8473.75,
            "crude_rsi": 46.1, "crude_rsi_status": "SLUGGISH", "crude_signal": "SELL CRUDE NEAR 8473.8", "crude_target": "T1: 8397.3 | SL: 8473", "crude_strike": "BUY 8400 PE",
            "warning": "Data Fallback", "market_status": "OFFLINE", "nifty_closed": True
        }

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, m=process_core_metrics())

@app.route('/api/refresh')
def api_refresh():
    return jsonify(process_core_metrics())

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
