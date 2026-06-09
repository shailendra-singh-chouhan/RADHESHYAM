import os, yfinance as yf
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

# Surgical Blue Theme Layout
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-white p-6 font-sans">
    <div class="max-w-4xl mx-auto space-y-6">
        <header class="flex justify-between items-center border-b border-blue-800 pb-4">
            <h1 class="text-2xl font-black text-blue-400">⚡ GOAT PRO COMMAND CENTER</h1>
            <a href="/api/refresh" class="text-xs bg-blue-900 px-3 py-1 rounded">🔄 Refresh</a>
        </header>

        <div class="grid grid-cols-2 gap-4">
            <div class="bg-blue-950 p-6 rounded-xl border border-blue-700">
                <p class="text-gray-400 text-sm font-bold">NIFTY 50</p>
                <p class="text-4xl font-black">₹{{ m.spot }}</p>
            </div>
            <div class="bg-orange-950 p-6 rounded-xl border border-orange-700">
                <p class="text-gray-400 text-sm font-bold">CRUDE OIL (MCX)</p>
                <p class="text-4xl font-black text-orange-400">₹{{ m.crude }}</p>
            </div>
        </div>

        <div class="grid grid-cols-3 gap-4">
            <div class="bg-slate-900 p-4 rounded border border-slate-700">
                <p class="text-gray-500 text-xs font-bold">PCR</p>
                <p class="text-xl font-black">{{ m.pcr }}</p>
            </div>
            <div class="bg-slate-900 p-4 rounded border border-slate-700">
                <p class="text-gray-500 text-xs font-bold">RSI</p>
                <p class="text-xl font-black">{{ m.rsi }}</p>
            </div>
            <div class="bg-slate-900 p-4 rounded border border-slate-700">
                <p class="text-gray-500 text-xs font-bold">TREND</p>
                <p class="text-xl font-black">{{ m.trend }}</p>
            </div>
        </div>

        <div class="bg-blue-900 p-6 rounded-xl border-l-4 border-emerald-400">
            <p class="text-emerald-300 font-black text-lg">⚡ STRATEGY ROUTER: {{ m.signal }}</p>
        </div>
    </div>
</body>
</html>
"""

def get_data():
    try:
        # Fetching Nifty and Global Crude
        nifty = yf.Ticker("^NSEI").history(period="1d")
        crude = yf.Ticker("CL=F").history(period="1d")
        
        spot = round(nifty['Close'].iloc[-1], 2)
        # Using a multiplier if needed for local price alignment, else keeping raw
        crude_val = round(crude['Close'].iloc[-1] * 83.5, 2) # Approximation for INR conversion if needed
        
        # Indicators Logic
        pcr = 0.78 
        rsi = 54.2
        trend = "BULLISH" if spot > 23200 else "BEARISH"
        signal = "BUY CALL ABOVE 23200"
        
        return {
            "spot": spot, 
            "crude": crude_val, 
            "pcr": pcr, 
            "rsi": rsi, 
            "trend": trend,
            "signal": signal
        }
    except Exception as e:
        return {"spot": 0, "crude": 0, "pcr": 0, "rsi": 0, "trend": "Error", "signal": "Data Error"}

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, m=get_data())

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
