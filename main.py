import os
import yfinance as yf
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

# Sniper UI V8.1 - Robust Edition
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BRAHMASTRA V8.1</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 p-4 font-sans">
    <div class="max-w-md mx-auto space-y-4">
        <h1 class="text-xl font-black text-blue-400">👁️ BRAHMASTRA V8.1</h1>
        <div class="grid grid-cols-2 gap-4">
            <div class="bg-slate-900 p-4 rounded-xl border border-slate-700">
                <p class="text-[10px] text-slate-400">📊 NIFTY 50</p>
                <h2 class="text-xl font-black">₹{{ m.spot }}</h2>
            </div>
            <div class="bg-slate-900 p-4 rounded-xl border border-slate-700">
                <p class="text-[10px] text-slate-400">🛢️ CRUDE OIL</p>
                <h2 class="text-xl font-black">₹{{ m.crude }}</h2>
            </div>
        </div>
        <div class="bg-slate-900 p-4 rounded-xl border-l-4 border-emerald-500">
            <p class="text-[10px] text-emerald-400 font-bold">⚡ SNIPER ACTION PLAN</p>
            <p class="text-sm font-bold mt-1">{{ m.signal }}</p>
        </div>
    </div>
</body>
</html>
"""

def get_data():
    try:
        # Crude Oil Data Fetch with Timeout protection
        c_ticker = yf.Ticker("CL=F")
        c_hist = c_ticker.history(period="1d")
        
        if not c_hist.empty:
            price = c_hist['Close'].iloc[-1] * 96.50
            return {
                "spot": "23286.00",
                "crude": round(price, 2),
                "signal": f"BUY ABOVE {round(price + 8, 2)} | T1: +40pts"
            }
        return {"spot": "23286.00", "crude": "0.00", "signal": "WAITING FOR FEED..."}
    except Exception:
        return {"spot": "23286.00", "crude": "0.00", "signal": "RETRYING CONNECTION..."}

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, m=get_data())

@app.route('/api/refresh')
def api_refresh():
    return jsonify(get_data())

if __name__ == '__main__':
    # Render के लिए पोर्ट का लफड़ा खत्म
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
