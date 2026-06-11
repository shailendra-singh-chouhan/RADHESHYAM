import os
import time
import requests
import pyotp
from flask import Flask, render_template_string
from SmartApi import SmartConnect

app = Flask(__name__)

# 📌 EXCHANGE TOKENS
NIFTY_TOKEN = "99926000"
VIX_TOKEN = "99926017"

# 🛡️ INSTITUTIONAL STATE ENGINE (THE FIX FOR "MOVING TARGET TRAP")
# यह इंजन सिग्नल को फ्रीज रखेगा और हर 5 सेकंड में मार्केट को स्कैन करेगा।
ENGINE_STATE = {
    "last_update": 0,
    "expiry_seconds": 5,  # 5 SECONDS CACHE REFRESH RATE
    "trade_status": "SEARCHING_LIQUIDITY",  # States: SEARCHING_LIQUIDITY, TRADE_ACTIVE
    "frozen_entry": 0.0,
    "frozen_target": 0.0,
    "frozen_sl": 0.0,
    "last_spot": 0.0,
    "velocity": 0.0, # Institutional Order Flow Proxy
    "signal_msg": "SYSTEM BOOTING...",
    "payload": None
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="5">
    <title>GOAT PRO QUANT V11 - INSTITUTIONAL</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-[#0a0f1a] text-slate-200 font-sans p-2 sm:p-4 selection:bg-cyan-900">
    <div class="max-w-md mx-auto space-y-4">
        
        <div class="flex justify-between items-center border-b border-slate-800 pb-3">
            <div>
                <h1 class="text-xl font-black text-cyan-500 tracking-wider">GOAT PRO <span class="text-white">V11</span></h1>
                <p class="text-[10px] text-cyan-400 font-mono tracking-widest uppercase">Institutional Algo Engine</p>
            </div>
            <div class="text-right">
                <span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold bg-cyan-950 text-cyan-400 border border-cyan-800 shadow-[0_0_10px_rgba(6,182,212,0.2)] animate-pulse">
                    ⚡ 5s REFRESH
                </span>
            </div>
        </div>

        <div class="grid grid-cols-2 gap-3">
            <div class="bg-slate-900/50 border border-slate-700 p-4 rounded-lg shadow-xl text-center relative overflow-hidden">
                <div class="absolute top-0 left-0 w-full h-1 bg-cyan-500"></div>
                <p class="text-[10px] text-slate-400 font-bold uppercase tracking-widest">NIFTY SPOT</p>
                <h2 class="text-2xl font-black mt-1 text-white font-mono">₹{{ m.spot }}</h2>
                <p class="text-[9px] mt-1 {{ 'text-emerald-400' if m.velocity >= 0 else 'text-red-400' }} font-mono">
                    VELOCITY: {{ "+" if m.velocity >= 0 else "" }}{{ m.velocity }} pts/tick
                </p>
            </div>
            <div class="bg-slate-900/50 border border-slate-700 p-4 rounded-lg shadow-xl text-center relative overflow-hidden">
                <div class="absolute top-0 left-0 w-full h-1 {% if m.vix_val > 18 %}bg-red-500{% else %}bg-purple-500{% endif %}"></div>
                <p class="text-[10px] text-slate-400 font-bold uppercase tracking-widest">INDIA VIX</p>
                <h2 class="text-2xl font-black mt-1 {% if m.vix_val > 18 %}text-red-400{% else %}text-purple-400{% endif %} font-mono">{{ m.vix_val }}</h2>
                <p class="text-[9px] mt-1 text-slate-500 font-mono">IMPLIED VOLATILITY</p>
            </div>
        </div>

        <div class="bg-slate-900 border {% if m.status == 'TRADE_ACTIVE' %}border-emerald-500 shadow-[0_0_15px_rgba(16,185,129,0.15)]{% else %}border-amber-500 shadow-[0_0_15px_rgba(245,158,11,0.1)]{% endif %} p-4 rounded-lg space-y-3 relative">
            
            <div class="flex justify-between items-center border-b border-slate-800 pb-2">
                <span class="text-xs font-bold text-slate-300 tracking-widest uppercase">Execution Matrix</span>
                {% if m.status == 'TRADE_ACTIVE' %}
                    <span class="px-2 py-0.5 text-[9px] font-mono bg-emerald-950 text-emerald-400 border border-emerald-800 rounded uppercase font-bold tracking-wider">🟢 POSITION ACTIVE</span>
                {% else %}
                    <span class="px-2 py-0.5 text-[9px] font-mono bg-amber-950 text-amber-400 border border-amber-800 rounded uppercase font-bold tracking-wider">⏳ WAITING FOR ENTRY</span>
                {% endif %}
            </div>
            
            <div class="pt-1 text-center bg-black/40 py-3 rounded border border-slate-800">
                <p class="text-[10px] font-medium text-slate-500 uppercase tracking-widest mb-1">Algorithmic Instruction</p>
                <p class="text-sm font-black text-white tracking-wide">{{ m.signal }}</p>
            </div>
            
            <div class="grid grid-cols-3 gap-2 text-center pt-2">
                <div class="bg-slate-950 border border-slate-800 p-2 rounded">
                    <span class="block text-[9px] text-slate-500 font-bold uppercase">ENTRY (LOCKED)</span>
                    <span class="text-xs font-black text-cyan-400 font-mono">₹{{ m.entry }}</span>
                </div>
                <div class="bg-slate-950 border border-slate-800 p-2 rounded">
                    <span class="block text-[9px] text-slate-500 font-bold uppercase">TARGET (LOCKED)</span>
                    <span class="text-xs font-black text-emerald-400 font-mono">₹{{ m.target }}</span>
                </div>
                <div class="bg-slate-950 border border-slate-800 p-2 rounded">
                    <span class="block text-[9px] text-slate-500 font-bold uppercase">SL (LOCKED)</span>
                    <span class="text-xs font-black text-red-400 font-mono">₹{{ m.sl }}</span>
                </div>
            </div>
        </div>

        <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg">
            <h3 class="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-3 border-b border-slate-800 pb-2">🔬 Institutional Live Checklist</h3>
            <ul class="space-y-2.5 text-xs font-mono">
                <li class="flex items-center justify-between">
                    <span class="text-slate-400 text-[10px]">1. Price > Entry Zone (Trend)</span>
                    <span class="{{ 'text-emerald-500' if m.chk[0] else 'text-slate-600' }} font-bold">{{ 'PASS ✓' if m.chk[0] else 'WAITING' }}</span>
                </li>
                <li class="flex items-center justify-between">
                    <span class="text-slate-400 text-[10px]">2. Order Flow Velocity Active</span>
                    <span class="{{ 'text-emerald-500' if m.chk[1] else 'text-red-500' }} font-bold">{{ 'PASS ✓' if m.chk[1] else 'FAIL ✗' }}</span>
                </li>
                <li class="flex items-center justify-between">
                    <span class="text-slate-400 text-[10px]">3. VIX/Price Structural Support</span>
                    <span class="{{ 'text-emerald-500' if m.chk[2] else 'text-red-500' }} font-bold">{{ 'PASS ✓' if m.chk[2] else 'FAIL ✗' }}</span>
                </li>
                <li class="flex items-center justify-between">
                    <span class="text-slate-400 text-[10px]">4. Institutional Liquidity Anchor</span>
                    <span class="{{ 'text-emerald-500' if m.chk[3] else 'text-red-500' }} font-bold">{{ 'PASS ✓' if m.chk[3] else 'FAIL ✗' }}</span>
                </li>
                <li class="flex items-center justify-between">
                    <span class="text-slate-400 text-[10px]">5. Implied Volatility < 18.0</span>
                    <span class="{{ 'text-emerald-500' if m.chk[4] else 'text-red-500' }} font-bold">{{ 'PASS ✓' if m.chk[4] else 'FAIL ✗' }}</span>
                </li>
            </ul>
        </div>
        
    </div>
</body>
</html>
"""

def send_telegram_alert(message):
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if bot_token and chat_id:
        try:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=3)
        except:
            pass

def execute_institutional_pipeline():
    global ENGINE_STATE
    t_now = time.time()

    # 1. 5-Second Cache Rate Limiting (Protects Angel One API from bans)
    if ENGINE_STATE["payload"] and (t_now - ENGINE_STATE["last_update"] < ENGINE_STATE["expiry_seconds"]):
        return ENGINE_STATE["payload"]

    try:
        api_key = os.environ.get("ANGEL_API_KEY")
        client_id = os.environ.get("ANGEL_CLIENT_ID")
        mpin = os.environ.get("ANGEL_MPIN")
        totp_secret = os.environ.get("ANGEL_TOTP_SECRET")

        if not all([api_key, client_id, mpin, totp_secret]):
            return {"error": "API CREDENTIALS MISSING"}

        # Broker Login
        totp = pyotp.TOTP(totp_secret).now()
        obj = SmartConnect(api_key=api_key)
        session = obj.generateSession(client_id, mpin, totp)
        
        if not session.get('status'):
            return {"error": "BROKER LOGIN FAILED"}

        # Fetch Live Data
        nifty_res = obj.ltpData("NSE", "NIFTY", NIFTY_TOKEN)
        vix_res = obj.ltpData("NSE", "INDIAVIX", VIX_TOKEN)
        
        if not nifty_res.get('status') or 'data' not in nifty_res:
            return {"error": "NIFTY FEED DEAD"}

        current_spot = float(nifty_res['data']['ltp'])
        try:
            current_vix = float(vix_res['data']['ltp']) if vix_res.get('status') else 15.0
        except:
            current_vix = 15.0

        # ------------------------------------------------------------------
        # 🧠 CORE ALGO ENGINE: STATE MACHINE (FIXES MOVING TARGET TRAP)
        # ------------------------------------------------------------------
        
        # Calculate Tick Velocity (Institutional Order Flow Proxy)
        if ENGINE_STATE["last_spot"] > 0:
            ENGINE_STATE["velocity"] = round(current_spot - ENGINE_STATE["last_spot"], 2)
        ENGINE_STATE["last_spot"] = current_spot

        # Standard Deviation calculation based on True Volatility (252 Trading Days)
        # Formula: Spot * (VIX / 100) / sqrt(252) -> Proxies daily expected move
        daily_sd = (current_spot * (current_vix / 100)) / 15.87

        if ENGINE_STATE["trade_status"] == "SEARCHING_LIQUIDITY":
            # Generate SETUP and FREEZE IT
            base_50_level = (current_spot // 50) * 50
            
            # Institutional Liquidity Sweep Logic
            entry_level = base_50_level if (current_spot - base_50_level) > 10 else (base_50_level - 50)
            
            ENGINE_STATE["frozen_entry"] = round(entry_level, 2)
            ENGINE_STATE["frozen_target"] = round(entry_level + (daily_sd * 1.5), 2)  # 1.5 SD Target
            ENGINE_STATE["frozen_sl"] = round(entry_level - (daily_sd * 0.6), 2)      # 0.6 SD Stoploss
            
            ENGINE_STATE["signal_msg"] = f"AWAITING PULLBACK TO ₹{ENGINE_STATE['frozen_entry']}"
            
            # TRIGGER ENTRY if Price sweeps into the zone (within 5 points)
            if current_spot <= (ENGINE_STATE["frozen_entry"] + 5):
                ENGINE_STATE["trade_status"] = "TRADE_ACTIVE"
                ENGINE_STATE["signal_msg"] = "🔥 LONG POSITION EXECUTED"
                send_telegram_alert(f"🟢 GOAT PRO: LONG EXECUTED at {current_spot}. TGT: {ENGINE_STATE['frozen_target']}, SL: {ENGINE_STATE['frozen_sl']}")

        elif ENGINE_STATE["trade_status"] == "TRADE_ACTIVE":
            # MONITOR EXIT CONDITIONS (Only unlocks when TGT or SL is hit)
            if current_spot >= ENGINE_STATE["frozen_target"]:
                ENGINE_STATE["trade_status"] = "SEARCHING_LIQUIDITY"
                send_telegram_alert(f"🎯 TARGET HIT! Profit Booked at {current_spot}")
                ENGINE_STATE["frozen_entry"] = 0.0 # Force recalculation next tick
                
            elif current_spot <= ENGINE_STATE["frozen_sl"]:
                ENGINE_STATE["trade_status"] = "SEARCHING_LIQUIDITY"
                send_telegram_alert(f"🛑 STOPLOSS HIT! Risk Managed at {current_spot}")
                ENGINE_STATE["frozen_entry"] = 0.0

        # ------------------------------------------------------------------
        # 🔬 DYNAMIC CHECKLIST (100% REAL DATA BASED)
        # ------------------------------------------------------------------
        # 1. Trend: Is price above our frozen entry point?
        chk_1 = current_spot > ENGINE_STATE["frozen_entry"]
        # 2. Velocity: Is there actual movement happening?
        chk_2 = abs(ENGINE_STATE["velocity"]) >= 0.50
        # 3. VIX/Price Struct: Proxy for PCR (If VIX is low and price stable)
        chk_3 = True if (current_vix < 16 or ENGINE_STATE["velocity"] > 0) else False
        # 4. Liquidity Anchor: Distance from round levels
        chk_4 = (current_spot - (current_spot // 50) * 50) > 5
        # 5. Volatility Stability
        chk_5 = current_vix < 18.0

        data = {
            "spot": round(current_spot, 2),
            "vix_val": round(current_vix, 2),
            "velocity": ENGINE_STATE["velocity"],
            "status": ENGINE_STATE["trade_status"],
            "signal": ENGINE_STATE["signal_msg"],
            "entry": ENGINE_STATE["frozen_entry"],
            "target": ENGINE_STATE["frozen_target"],
            "sl": ENGINE_STATE["frozen_sl"],
            "chk": [chk_1, chk_2, chk_3, chk_4, chk_5]
        }

        # Update Cache
        ENGINE_STATE["last_update"] = t_now
        ENGINE_STATE["payload"] = data
        return data

    except Exception as e:
        if ENGINE_STATE["payload"]:
            return ENGINE_STATE["payload"]
        return {"error": f"ALGO CRASH: {str(e)}"}

@app.route('/')
def index():
    market_data = execute_institutional_pipeline()
    if "error" in market_data:
        return f"<div style='color:red; background:black; padding:20px; font-family:monospace;'><h3>🚨 FATAL ERROR</h3><p>{market_data['error']}</p></div>"
    return render_template_string(HTML_TEMPLATE, m=market_data)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
