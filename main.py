import os, yfinance as yf
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-white p-6">
    <div class="max-w-4xl mx-auto space-y-6">
        <h1 class="text-3xl font-black text-blue-400">⚡ GOAT PRO DUAL CORE</h1>
        <div class="grid grid-cols-2 gap-4">
            <div class="bg-blue-900 p-6 rounded-xl border border-blue-500">
                <p class="text-gray-400 font-bold text-sm">NIFTY 50</p>
                <p class="text-4xl font-black">₹{{ m.spot }}</p>
            </div>
            <div class="bg-orange-900 p-6 rounded-xl border border-orange-500">
                <p class="text-gray-400 font-bold text-sm">CRUDE OIL</p>
                <p class="text-4xl font-black">₹{{ m.crude }}</p>
            </div>
        </div>
        <div class="bg-slate-800 p-6 rounded-xl border border-slate-700">
            <h2 class="text-lg font-bold mb-4">🧠 मार्किट इंडिकेटर्स</h2>
            <p class="text-gray-400">PCR: <span class="text-white font-bold">{{ m.pcr }}</span> | RSI: <span class="text-white font-bold">{{ m.rsi }}</span></p>
            <p class="mt-4 text-emerald-400 font-black">⚡ सिग्नल: {{ m.signal }}</p>
        </div>
    </div>
</body>
</html>
"""

def get_data():
    try:
        nifty = yf.Ticker("^NSEI").history(period="1d")
        crude = yf.Ticker("CL=F").history(period="1d")
        spot = round(nifty['Close'].iloc[-1], 2)
        crude_val = round(crude['Close'].iloc[-1], 2)
        # Mock logic for indicators to keep it stable
        return {
            "spot": spot, 
            "crude": crude_val,
            "pcr": 0.78, 
            "rsi": 54.2,
            "signal": "BUY CALL ABOVE 23200"
        }
    except:
        return {"spot": 0, "crude": 0, "pcr": 0, "rsi": 0, "signal": "Data Error"}

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, m=get_data())

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
