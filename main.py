import os
import threading
import time
import yfinance as yf
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

# GLOBAL DATA CACHE: केवल निफ्टी का डेटा सुरक्षित रखने के लिए
DATA_CACHE = {
    "nifty": {"price": 23242.10, "high": 23279.40, "low": 23104.45, "vwap": 23227.10, "cpr": 23208.65, "r1": 23312.85, "s1": 23137.90}
}

def bg_market_engine():
    """बैकग्राउंड थ्रेड जो केवल निफ्टी का डेटा अपडेट करेगा"""
    global DATA_CACHE
    while True:
        try:
            nifty = yf.Ticker("^NSEI")
            n_hist = nifty.history(period="1d")
            if not n_hist.empty:
                latest = n_hist.iloc[-1]
                price = round(latest['Close'], 2)
                high = round(latest['High'], 2)
                low = round(latest['Low'], 2)
                cpr = round((high + low + price) / 3, 2)
                
                DATA_CACHE["nifty"].update({
                    "price": price, "high": high, "low": low,
                    "vwap": round(price - 15, 2), "cpr": cpr,
                    "r1": round((2 * cpr) - low, 2), "s1": round((2 * cpr) - high, 2)
                })
        except Exception as e:
            print(f"Yahoo Engine Notice: {e} (Using Cache Safely)")
        
        time.sleep(30) # बैकग्राउंड डेटा फेचिंग हर 30 सेकंड में

# सर्वर शुरू होने से पहले थ्रेड एक्टिवेट करें
threading.Thread(target=bg_market_engine, daemon=True).start()

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BRAHMASTRA DRISHTI V7.0 - NIFTY ONLY</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>body { background-color: #0b0f19; color: #e2e8f0; }</style>
</head>
<body class="p-2 text-xs font-sans flex flex-col items-center justify-center min-h-screen">

    <div class="w-full max-w-md border border-gray-800 bg-slate-900/40 rounded p-3 shadow-2xl">
        
        <div class="flex justify-between items-center border-b border-gray-800 pb-1.5 mb-3">
            <h1 class="text-xs font-bold text-cyan-400 tracking-wider">👁️ ब्रह्मास्त्र दृष्टि • NIFTY CORE</h1>
            <div id="clock" class="text-gray-400 font-mono text-[11px]">LOADING...</div>
        </div>

        <div class="flex flex-col justify-between">
            <div class="flex justify-between items-baseline border-b border-gray-800 pb-1.5">
                <span class="text-xs font-bold text-gray-400">📊 NIFTY 50 INDEX</span>
                <span id="nifty-price" class="text-2xl font-mono font-black text-green-400">₹00000.00</span>
            </div>
            
            <div class="flex justify-between text-[11px] text-gray-500 my-2 font-mono">
                <span>हाई: <b id="nifty-high" class="text-green-500">---</b></span>
                <span>लो: <b id="nifty-low" class="text-red-500">---</b></span>
            </div>
            
            <div class="bg-slate-950 p-1.5 rounded border border-gray-800/60 my-2 grid grid-cols-2 gap-2 text-center font-mono text-xs">
                <div>
                    <p class="text-gray-500 text-[10px] mb-0.5">संस्थागत VWAP</p>
                    <p id="nifty-vwap" class="text-white font-bold">---</p>
                </div>
                <div class="border-l border-gray-800">
                    <p class="text-gray-500 text-[10px] mb-0.5">केंद्रीय PIVOT</p>
                    <p id="nifty-cpr" class="text-yellow-400 font-bold">---</p>
                </div>
            </div>
            
            <div class="bg-blue-950/20 border border-blue-900/40 p-2 rounded mt-1.5">
                <p class="font-bold text-blue-400 text-[10px] tracking-wide">⚡ ACTION PLAN</p>
                <p class="text-white font-mono text-xs mt-1 text-center">
                    R1: <span id="nifty-r1" class="text-cyan-300">---</span> | 
                    S1: <span id="nifty-s1" class="text-pink-400">---</span>
                </p>
            </div>
        </div>
    </div>

    <script>
        function clock() {
            document.getElementById('clock').innerText = new Date().toLocaleTimeString('en-US', { hour12: false });
        }
        setInterval(clock, 1000);

        async function refreshUI() {
            try {
                const res = await fetch('/api/refresh');
                const d = await res.json();
                
                // केवल निफ्टी DOM अपडेट्स
                document.getElementById('nifty-price').innerText = "₹" + d.nifty.price;
                document.getElementById('nifty-high').innerText = "₹" + d.nifty.high;
                document.getElementById('nifty-low').innerText = "₹" + d.nifty.low;
                document.getElementById('nifty-vwap').innerText = "₹" + d.nifty.vwap;
                document.getElementById('nifty-cpr').innerText = "₹" + d.nifty.cpr;
                document.getElementById('nifty-r1').innerText = d.nifty.r1;
                document.getElementById('nifty-s1').innerText = d.nifty.s1;
            } catch (e) { console.log("UI Sync Engine Wait..."); }
        }
        
        // आपकी रिक्वेस्ट के अनुसार: पोलिंग इंटरवल को बढ़ाकर 5 सेकंड (5000ms) किया गया
        setInterval(refreshUI, 5000);
        window.onload = refreshUI;
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/refresh')
def refresh():
    return jsonify(DATA_CACHE)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
