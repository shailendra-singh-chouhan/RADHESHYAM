import os, yfinance as yf
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

# Template wahi hai, bas function ke variables ke sath matched hai
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
        nifty = yf.Ticker("^NSEI").history(period="1d")
        crude = yf.Ticker("CL=F").history(period="1d")
        
        spot = round(nifty['Close'].iloc[-1], 2) if not nifty.empty else 0.0
        crude_val = round(crude['Close'].iloc[-1] * 83.5, 2) if not crude.empty else 0.0
        
        return {
            "spot": spot, 
            "crude": crude_val, 
            "pcr": 0.78, 
            "rsi": 54.2, 
            "trend": "BULLISH" if spot > 23200 else "BEARISH", 
            "signal": "BUY CALL ABOVE 23200"
        }
    except:
        return {"spot": 0, "crude": 0, "pcr": 0, "rsi": 0, "trend": "Error", "signal": "Data Error"}

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, m=get_data())

@app.route('/api/refresh')
def api_refresh():
    return jsonify(get_data())

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
