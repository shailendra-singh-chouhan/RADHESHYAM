import os
import yfinance as yf
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

# Reverting to the "Pro-Quant" Look but keeping it lightweight
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <script src="https://cdn.tailwindcss.com"></script>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body class="bg-slate-950 text-white font-sans p-4">
    <div class="max-w-md mx-auto">
        <div class="flex justify-between items-center border-b border-slate-800 pb-4">
            <h1 class="text-lg font-black text-blue-500">GOAT PRO QUANT</h1>
            <span class="text-[10px] text-emerald-500 font-bold">● LIVE</span>
        </div>
        
        <div class="grid grid-cols-2 gap-4 mt-6">
            <div class="bg-slate-900 border border-slate-800 p-4 rounded-xl">
                <p class="text-[10px] text-slate-400">NIFTY 50</p>
                <h2 class="text-2xl font-bold">{{ m.spot }}</h2>
            </div>
            <div class="bg-slate-900 border border-slate-800 p-4 rounded-xl">
                <p class="text-[10px] text-slate-400">CRUDE OIL</p>
                <h2 class="text-2xl font-bold text-orange-400">{{ m.crude }}</h2>
            </div>
        </div>

        <div class="mt-6 bg-slate-900 border-l-4 border-emerald-500 p-4 rounded-r-xl">
            <p class="text-[10px] text-emerald-400 font-bold uppercase">Sniper Signal</p>
            <p class="text-sm mt-1 font-bold">{{ m.signal }}</p>
        </div>
    </div>
</body>
</html>
"""

def get_data():
    try:
        n_ticker = yf.Ticker("^NSEI")
        c_ticker = yf.Ticker("CL=F")
        n_val = n_ticker.history(period="1d")['Close'].iloc[-1]
        c_val = c_ticker.history(period="1d")['Close'].iloc[-1] * 96.50
        return {
            "spot": round(n_val, 2),
            "crude": round(c_val, 2),
            "signal": "BUY CRUDE ABOVE 8675.00" if c_val > 8670 else "WAIT FOR BREAKOUT"
        }
    except:
        return {"spot": "Loading...", "crude": "Loading...", "signal": "Syncing..."}

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, m=get_data())

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
