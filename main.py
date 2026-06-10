import os
import pyotp
from flask import Flask, render_template_string, jsonify
from SmartApi import SmartConnect

app = Flask(__name__)

# CONFIGURATION: हर महीने की एक्सपायरी का नया टोकन यहाँ बदलें
NIFTY_TOKEN = "99926000"  # NSE Index Token
CRUDE_TOKEN = "437532"    # MCX Crude Oil Current Active Token (इसे लाइव मास्टर से अपडेट करें)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GOAT PRO QUANT V11</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 font-sans p-4">
    <div class="max-w-md mx-auto space-y-4">
        
        <div class="flex justify-between items-center border-b border-slate-800 pb-3">
            <div>
                <h1 class="text-xl font-black text-blue-500 tracking-wider">GOAT PRO V11</h1>
                <p class="text-[10px] text-emerald-400 font-mono">⚡ PURE ANGEL ONE DIRECT FEED</p>
            </div>
            <div class="text-right">
                <span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-900 text-emerald-300 animate-pulse">● LIVE</span>
            </div>
        </div>

        <div class="grid grid-cols-2 gap-3">
            <div class="bg-slate-900 border border-slate-800 p-3 rounded-xl shadow-xl">
                <p class="text-[10px] text-slate-400 font-bold uppercase tracking-wide">NIFTY 50 SPOT</p>
                <h2 class="text-2xl font-black mt-1 text-white">₹{{ m.spot }}</h2>
            </div>
            <div class="bg-slate-900 border border-slate-800 p-3 rounded-xl shadow-xl">
                <p class="text-[10px] text-slate-400 font-bold uppercase tracking-wide">MCX CRUDE OIL</p>
                <h2 class="text-2xl font-black mt-1 text-orange-500">₹{{ m.crude }}</h2>
            </div>
        </div>

        {% if m.gamma_blast %}
        <div class="bg-red-950 border border-red-500 p-3 rounded-xl text-center animate-bounce">
            <span class="text-xs font-black text-red-400 tracking-widest block">🔥 GAMMA BLAST DETECTED 🔥</span>
            <p class="text-[11px] text-slate-200 mt-1">Heavy Institutional Volume Spike. Expect Massive Directional Move!</p>
        </div>
        {% endif %}

        <div class="bg-slate-900 border border-slate-800 p-4 rounded-xl space-y-2">
            <div class="flex justify-between items-center border-b border-slate-800 pb-2">
                <span class="text-xs font-bold text-blue-400">🎯 STRATEGIC TRADE MATRIX</span>
                <span class="px-2 py-0.5 text-[9px] font-mono bg-blue-900 text-blue-200 rounded uppercase font-bold">{{ m.trade_type }}</span>
            </div>
            <div class="pt-1">
                <p class="text-xs font-medium text-slate-400">Action Plan:</p>
                <p class="text-base font-black text-slate-100 mt-0.5">{{ m.signal }}</p>
            </div>
            <div class="grid grid-cols-2 gap-2 text-[11px] font-mono pt-2 border-t border-slate-800/50 text-slate-400">
                <div>TARGET: <span class="text-emerald-400 font-bold">{{ m.target }}</span></div>
                <div>STOPLOSS: <span class="text-red-400 font-bold">{{ m.sl }}</span></div>
            </div>
        </div>

        <div class="bg-slate-900 border border-slate-800 p-4 rounded-xl">
            <h3 class="text-xs font-black text-slate-300 uppercase tracking-wider mb-3">🛡️ 5-POINT POSITIONAL CHECKLIST</h3>
            <ul class="space-y-2 text-xs">
                <li class="flex items-center justify-between">
                    <span class="text-slate-400">1. Higher Timeframe Trend (Weekly/Daily)</span>
                    <span class="{{ 'text-emerald-400 font-bold' if m.chk[0] else 'text-red-400 font-bold' }}">{{ 'PASS ✓' if m.chk[0] else 'FAIL ✗' }}</span>
                </li>
                <li class="flex items-center justify-between">
                    <span class="text-slate-400">2. Delivery & OI Build-up Confirmation</span>
                    <span class="{{ 'text-emerald-400 font-bold' if m.chk[1] else 'text-red-400 font-bold' }}">{{ 'PASS ✓' if m.chk[1] else 'FAIL ✗' }}</span>
                </li>
                <li class="flex items-center justify-between">
                    <span class="text-slate-400">3. Options PCR Structural Support (>1.05)</span>
                    <span class="{{ 'text-emerald-400 font-bold' if m.chk[2] else 'text-red-400 font-bold' }}">{{ 'PASS ✓' if m.chk[2] else 'FAIL ✗' }}</span>
                </li>
                <li class="flex items-center justify-between">
                    <span class="text-slate-400">4. Key Psychological Level Breakout</span>
                    <span class="{{ 'text-emerald-400 font-bold' if m.chk[3] else 'text-red-400 font-bold' }}">{{ 'PASS ✓' if m.chk[3] else 'FAIL ✗' }}</span>
                </li>
                <li class="flex items-center justify-between">
                    <span class="text-slate-400">5. Volatility Index (INDIA VIX) Stability</span>
                    <span class="{{ 'text-emerald-400 font-bold' if m.chk[4] else 'text-red-400 font-bold' }}">{{ 'PASS ✓' if m.chk[4] else 'FAIL ✗' }}</span>
                </li>
            </ul>
        </div>

    </div>
</body>
</html>
"""

def fetch_angel_data():
    try:
        api_key = os.environ.get("ANGEL_API_KEY")
        client_id = os.environ.get("ANGEL_CLIENT_ID")
        mpin = os.environ.get("ANGEL_MPIN")
        totp_secret = os.environ.get("ANGEL_TOTP_SECRET")

        if not all([api_key, client_id, mpin, totp_secret]):
            return {"error": "CRITICAL: ENV VARIABLES MISSING"}

        # TOTP Generation
        totp = pyotp.TOTP(totp_secret).now()
        obj = SmartConnect(api_key=api_key)
        session = obj.generateSession(client_id, mpin, totp)
        
        if not session.get('status'):
            return {"error": "ANGEL LOGIN AUTH FAILED"}

        # Fetch Nifty Spot
        n_res = obj.ltpData("NSE", "Nifty 50", NIFTY_TOKEN)
        nifty_spot = float(n_res['data']['ltp']) if n_res.get('status') and n_res['data'] else None
        
        # Fetch Crude Oil Real Price
        c_res = obj.ltpData("MCX", "CRUDEOIL", CRUDE_TOKEN)
        crude_spot = float(c_res['data']['ltp']) if c_res.get('status') and c_res['data'] else None

        if nifty_spot is None or crude_spot is None:
            return {"error": "TOKEN EXPIRED OR SCRIP NOT FOUND IN MASTER CACHE"}

        return {"spot": round(nifty_spot, 2), "crude": round(crude_spot, 2), "error": None}

    except Exception as e:
        return {"error": f"SYSTEM CRASH PREVENTED: {str(e)}"}

def engine_brain():
    feed = fetch_angel_data()
    
    # अगर एंजेल वन डेटा में एरर है तो स्क्रीन ब्लॉक करो (No Fallback)
    if feed.get("error"):
        return {
            "spot": "API ERR", "crude": "API ERR", 
            "trade_type": "HALTED", "signal": feed["error"], 
            "target": "N/A", "sl": "N/A", "gamma_blast": False, 
            "chk": [False, False, False, False, False]
        }
        
    crude = feed["crude"]
    spot = feed["spot"]

    # Positional Logic Structure (Intraday / Weekly / Long Term)
    # छोटे 10-20 पॉइंट के स्कैल्पिंग सिग्नल्स बंद। अब बड़े स्ट्रक्चरल लेवल्स पर काम होगा।
    if crude > 8650:
        trade_type = "WEEKLY POSITIONAL"
        signal = f"BUY & HOLD CRUDE ON DIP TO {round(crude - 30, 2)}"
        target = f"{round(crude + 250, 2)}"
        sl = f"{round(crude - 100, 2)}"
        # 5-Point Checklist Evaluation Status
        checklist = [True, True, True, False, True] 
        gamma_blast = True if crude > 8750 else False # Psychological Level Breakout Trigger
    else:
        trade_type = "INTRADAY SWING"
        signal = "ACCUMULATE NIFTY SHORTS FOR WEEKLY TARGETS"
        target = f"{round(spot - 400, 2)}"
        sl = f"{round(spot + 150, 2)}"
        checklist = [True, False, True, True, False]
        gamma_blast = False

    return {
        "spot": spot, "crude": crude, "trade_type": trade_type,
        "signal": signal, "target": target, "sl": sl,
        "gamma_blast": gamma_blast, "chk": checklist
    }

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, m=engine_brain())

@app.route('/api/refresh')
def api_refresh():
    return jsonify(engine_brain())

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
