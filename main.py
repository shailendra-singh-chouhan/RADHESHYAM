import os
import pyotp
import yfinance as yf
from flask import Flask, render_template_string, jsonify
from SmartApi import SmartConnect

app = Flask(__name__)

# Premium Ultra-Clean Quant UI Layout
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <script src="https://cdn.tailwindcss.com"></script>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GOAT PRO QUANT V10</title>
</head>
<body class="bg-slate-950 text-white font-sans p-4">
    <div class="max-w-md mx-auto">
        <div class="flex justify-between items-center border-b border-slate-800 pb-4">
            <div>
                <h1 class="text-lg font-black text-blue-500">GOAT PRO QUANT V10</h1>
                <p class="text-[9px] text-slate-500 font-mono">Feed: <span class="text-amber-400 font-bold">{{ m.feed_source }}</span></p>
            </div>
            <span class="text-[10px] text-emerald-500 font-bold">● LIVE CONNECTION</span>
        </div>
        
        <div class="grid grid-cols-2 gap-4 mt-6">
            <div class="bg-slate-900 border border-slate-800 p-4 rounded-xl">
                <p class="text-[10px] text-slate-400">NIFTY 50 INDEX</p>
                <h2 class="text-2xl font-bold text-white">₹{{ m.spot }}</h2>
            </div>
            <div class="bg-slate-900 border border-slate-800 p-4 rounded-xl">
                <p class="text-[10px] text-slate-400">MCX CRUDE OIL</p>
                <h2 class="text-2xl font-bold text-orange-400">₹{{ m.crude }}</h2>
            </div>
        </div>

        <div class="mt-6 bg-slate-900 border-l-4 border-emerald-500 p-4 rounded-r-xl">
            <p class="text-[10px] text-emerald-400 font-bold uppercase">🎯 Sniper Trade Signal</p>
            <p class="text-sm mt-1 font-bold text-slate-200">{{ m.signal }}</p>
        </div>
    </div>
</body>
</html>
"""

def get_angel_one_data():
    try:
        # Fetching credentials dynamically from Render Environment
        api_key = os.environ.get("ANGEL_API_KEY")
        client_id = os.environ.get("ANGEL_CLIENT_ID")
        mpin = os.environ.get("ANGEL_MPIN")
        totp_secret = os.environ.get("ANGEL_TOTP_SECRET")

        if not all([api_key, client_id, mpin, totp_secret]):
            return None

        # Generating Live 6-digit TOTP
        totp = pyotp.TOTP(totp_secret).now()
        
        # Init SmartConnect
        obj = SmartConnect(api_key=api_key)
        session = obj.generateSession(client_id, mpin, totp)
        
        if session.get('status'):
            # Fetching Nifty 50 Index (Token: 99926000 on NSE)
            n_res = obj.ltpData("NSE", "Nifty 50", "99926000")
            nifty_spot = n_res['data']['ltp'] if n_res.get('status') else None
            
            # Fetching Crude Oil Current Month Contract (Token Example: 436329 or dynamically searchable)
            # NOTE: Update the token ID based on the active MCX contract expiry
            c_res = obj.ltpData("MCX", "CRUDEOIL", "436329") 
            crude_spot = c_res['data']['ltp'] if c_res.get('status') else None
            
            if nifty_spot and crude_spot:
                return {
                    "spot": round(float(nifty_spot), 2),
                    "crude": round(float(crude_spot), 2),
                    "feed_source": "ANGEL ONE API (100% ACCURATE)"
                }
        return None
    except Exception as e:
        print(f"Angel One Connect Error: {str(e)}")
        return None

def get_fallback_data():
    # Keep the system running on yfinance if Angel API limit exhausts or hits a weekend
    try:
        n_ticker = yf.Ticker("^NSEI")
        c_ticker = yf.Ticker("CL=F")
        n_val = n_ticker.history(period="1d")['Close'].iloc[-1]
        c_val = c_ticker.history(period="1d")['Close'].iloc[-1] * 96.50
        return {
            "spot": round(n_val, 2),
            "crude": round(c_val, 2),
            "feed_source": "YFINANCE BACKUP FEED"
        }
    except:
        return {"spot": 23214.95, "crude": 8661.84, "feed_source": "OFFLINE SIMULATOR"}

def process_metrics():
    # Primary check: Try fetching from Angel One Setup
    data = get_angel_one_data()
    
    # Secondary check: Fallback to yfinance if primary fails
    if not data:
        data = get_fallback_data()
        
    # Inject Signal Strategy Engines
    if data['crude'] > 8670:
        data["signal"] = f"⚡ SNIPER: BUY CRUDE ABOVE {round(data['crude'] + 5, 1)} | T1: +40 Pts"
    else:
        data["signal"] = "❌ WAIT FOR BREAKOUT: PRICE IN CPR RANGE"
        
    return data

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, m=process_metrics())

@app.route('/api/refresh')
def api_refresh():
    return jsonify(process_metrics())

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
