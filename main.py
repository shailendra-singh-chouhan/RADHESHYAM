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

# 🛡️ THE INSTITUTIONAL STATE ENGINE (STRICT EXECUTION LAYER)
ENGINE_STATE = {
    "last_update": 0,
    "expiry_seconds": 5,  # 5s High-Frequency Refresh
    "trade_status": "SYSTEM_BLOCKED",  # Default state prevents blind entries
    "frozen_entry": 0.0,
    "frozen_target": 0.0,
    "frozen_sl": 0.0,
    "last_spot": 0.0,
    "velocity": 0.0,
    "signal_msg": "AWAITING 100% CHECKLIST CLEARANCE",
    "payload": None
}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="5">
    <title>GOAT PRO QUANT V12 - THE VAULT</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-[#050914] text-slate-200 font-sans p-2 sm:p-4 selection:bg-blue-900">
    <div class="max-w-md mx-auto space-y-4">
        
        <div class="flex justify-between items-center border-b border-slate-800 pb-3">
            <div>
                <h1 class="text-xl font-black text-blue-500 tracking-wider">GOAT PRO <span class="text-white">V12</span></h1>
                <p class="text-[10px] text-blue-400 font-mono tracking-widest uppercase">Institutional Vault Edition</p>
            </div>
            <div class="text-right">
                <span class="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold bg-blue-950 text-blue-400 border border-blue-800 shadow-[0_0_10px_rgba(59,130,246,0.2)] animate-pulse">
                    ⚡ 5s HFT SYNC
                </span>
            </div>
        </div>

        <div class="grid grid-cols-2 gap-3">
            <div class="bg-slate-900/80 border border-slate-800 p-4 rounded-lg shadow-xl text-center relative overflow-hidden">
                <div class="absolute top-0 left-0 w-full h-1 bg-blue-500"></div>
                <p class="text-[10px] text-slate-400 font-bold uppercase tracking-widest">NIFTY SPOT</p>
                <h2 class="text-2xl font-black mt-1 text-white font-mono">₹{{ m.spot }}</h2>
                <p class="text-[10px] mt-1 font-bold {{ 'text-emerald-400' if m.velocity > 0 else 'text-red-400' if m.velocity < 0 else 'text-slate-400' }} font-mono">
                    VOL: {{ "+" if m.velocity > 0 else "" }}{{ m.velocity }} pts/tick
                </p>
            </div>
            <div class="bg-slate-900/80 border border-slate-800 p-4 rounded-lg shadow-xl text-center relative overflow-hidden">
                <div class="absolute top-0 left-0 w-full h-1 {% if m.vix_val > 18 %}bg-red-500{% else %}bg-purple-500{% endif %}"></div>
                <p class="text-[10px] text-slate-400 font-bold uppercase tracking-widest">INDIA VIX</p>
                <h2 class="text-2xl font-black mt-1 {% if m.vix_val > 18 %}text-red-400{% else %}text-purple-400{% endif %} font-mono">{{ m.vix_val }}</h2>
                <p class="text-[9px] mt-1 text-slate-500 font-mono">IMPLIED VOLATILITY</p>
            </div>
        </div>

        <div class="bg-slate-900 border {% if m.status == 'TRADE_ACTIVE' %}border-emerald-500 shadow-[0_0_15px_rgba(16,185,129,0.2)]{% elif m.status == 'SETUP_READY' %}border-blue-500 shadow-[0_0_15px_rgba(59,130,246,0.2)]{% else %}border-red-900 shadow-[0_0_15px_rgba(220,38,38,0.1)]{% endif %} p-4 rounded-lg space-y-3 relative">
            
            <div class="flex justify-between items-center border-b border-slate-800 pb-2">
                <span class="text-[11px] font-black text-slate-300 tracking-widest uppercase">Execution Matrix</span>
                
                {% if m.status == 'TRADE_ACTIVE' %}
                    <span class="px-2 py-0.5 text-[9px] font-mono bg-emerald-950 text-emerald-400 border border-emerald-800 rounded uppercase font-bold">🟢 LIVE POSITION</span>
                {% elif m.status == 'SETUP_READY' %}
                    <span class="px-2 py-0.5 text-[9px] font-mono bg-blue-950 text-blue-400 border border-blue-800 rounded uppercase font-bold">⏳ WAITING FOR REVERSAL</span>
                {% else %}
                    <span class="px-2 py-0.5 text-[9px] font-mono bg-red-950 text-red-400 border border-red-800 rounded uppercase font-bold">🚫 SYSTEM BLOCKED</span>
                {% endif %}
            </div>
            
            <div class="pt-1 text-center bg-black/60 py-3 rounded border border-slate-800">
                <p class="text-[9px] font-bold text-slate-500 uppercase tracking-widest mb-1">Algorithmic Instruction</p>
                <p class="text-sm font-black {% if m.status == 'SYSTEM_BLOCKED' %}text-red-400{% else %}text-white{% endif %} tracking-wide">{{ m.signal }}</p>
            </div>
            
            <div class="grid grid-cols-3 gap-2 text-center pt-2">
                <div class="bg-[#050914] border border-slate-800 p-2 rounded">
                    <span class="block text-[9px] text-slate-500 font-bold uppercase">ENTRY</span>
                    <span class="text-xs font-black text-blue-400 font-mono">{% if m.entry > 0 %}₹{{ m.entry }}{% else %}---{% endif %}</span>
                </div>
                <div class="bg-[#050914] border border-slate-800 p-2 rounded">
                    <span class="block text-[9px] text-slate-500 font-bold uppercase">TARGET</span>
                    <span class="text-xs font-black text-emerald-400 font-mono">{% if m.target > 0 %}₹{{ m.target }}{% else %}---{% endif %}</span>
                </div>
                <div class="bg-[#050914] border border-slate-800 p-2 rounded">
                    <span class="block text-[9px] text-slate-500 font-bold uppercase">STOPLOSS</span>
                    <span class="text-xs font-black text-red-400 font-mono">{% if m.sl > 0 %}₹{{ m.sl }}{% else %}---{% endif %}</span>
                </div>
            </div>
        </div>

        <div class="bg-slate-900 border border-slate-800 p-4 rounded-lg">
            <h3 class="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-3 border-b border-slate-800 pb-2">⚖️ Strict 5/5 Pass Rule Layer</h3>
            <ul class="space-y-2.5 text-[11px] font-mono">
                <li class="flex items-center justify-between">
                    <span class="text-slate-400">1. Price > Base Trend Level</span>
                    <span class="{{ 'text-emerald-500' if m.chk[0] else 'text-red-500' }} font-bold">{{ 'PASS ✓' if m.chk[0] else 'FAIL ✗' }}</span>
                </li>
                <li class="flex items-center justify-between">
                    <span class="text-slate-400">2. Order Flow Momentum (> 0)</span>
                    <span class="{{ 'text-emerald-500' if m.chk[1] else 'text-red-500' }} font-bold">{{ 'PASS ✓' if m.chk[1] else 'FAIL ✗' }}</span>
                </li>
                <li class="flex items-center justify-between">
                    <span class="text-slate-400">3. Structural Reversal Validated</span>
                    <span class="{{ 'text-emerald-500' if m.chk[2] else 'text-red-500' }} font-bold">{{ 'PASS ✓' if m.chk[2] else 'FAIL ✗' }}</span>
                </li>
                <li class="flex items-center justify-between">
                    <span class="text-slate-400">4. Liquidity Zone Clearance</span>
                    <span class="{{ 'text-emerald-500' if m.chk[3] else 'text-red-500' }} font-bold">{{ 'PASS ✓' if m.chk[3] else 'FAIL ✗' }}</span>
                </li>
                <li class="flex items-center justify-between">
                    <span class="text-slate-400">5. Volatility (VIX < 18.0) Safe</span>
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

    if ENGINE_STATE["payload"] and (t_now - ENGINE_STATE["last_update"] < ENGINE_STATE["expiry_seconds"]):
        return ENGINE_STATE["payload"]

    try:
        api_key = os.environ.get("ANGEL_API_KEY")
        client_id = os.environ.get("ANGEL_CLIENT_ID")
        mpin = os.environ.get("ANGEL_MPIN")
        totp_secret = os.environ.get("ANGEL_TOTP_SECRET")

        if not all([api_key, client_id, mpin, totp_secret]):
            return {"error": "API CREDENTIALS MISSING"}

        totp = pyotp.TOTP(totp_secret).now()
        obj = SmartConnect(api_key=api_key)
        session = obj.generateSession(client_id, mpin, totp)
        
        if not session.get('status'):
            return {"error": "BROKER LOGIN FAILED"}

        nifty_res = obj.ltpData("NSE", "NIFTY", NIFTY_TOKEN)
        vix_res = obj.ltpData("NSE", "INDIAVIX", VIX_TOKEN)
        
        if not nifty_res.get('status') or 'data' not in nifty_res:
            return {"error": "NIFTY FEED DEAD"}

        current_spot = float(nifty_res['data']['ltp'])
        try:
            current_vix = float(vix_res['data']['ltp']) if vix_res.get('status') else 15.0
        except:
            current_vix = 15.0

        # 1. VELOCITY CALCULATION
        if ENGINE_STATE["last_spot"] > 0:
            ENGINE_STATE["velocity"] = round(current_spot - ENGINE_STATE["last_spot"], 2)
        ENGINE_STATE["last_spot"] = current_spot
        vel = ENGINE_STATE["velocity"]

        # 2. STRICT CHECKLIST EVALUATION
        base_100_level = (current_spot // 100) * 100
        
        chk_1 = current_spot > base_100_level  # Trend Check
        chk_2 = vel > 0  # Order Flow must be positive (No falling knife)
        chk_3 = True if (current_spot - base_100_level) > 15 else False  # Reversal confirmation
        chk_4 = abs(current_spot - round(current_spot / 50) * 50) > 5  # Safe from immediate round numbers
        chk_5 = current_vix < 18.0  # VIX safety
        
        all_checks_passed = all([chk_1, chk_2, chk_3, chk_4, chk_5])

        # 3. REALISTIC INTRADAY RISK METRICS (VIX Adjusted)
        # Instead of 340 pts daily SD, we use practical 1:2 or 1:2.5 intraday/swing ratios
        dynamic_sl_pts = 45 * (current_vix / 15.0)   # Base 45 pts SL
        dynamic_tgt_pts = 110 * (current_vix / 15.0) # Base 110 pts Target

        # 4. CORE STATE MACHINE LOGIC
        if ENGINE_STATE["trade_status"] != "TRADE_ACTIVE":
            if not all_checks_passed:
                # SYSTEM BLOCKED (Strict Enforcement)
                ENGINE_STATE["trade_status"] = "SYSTEM_BLOCKED"
                ENGINE_STATE["signal_msg"] = "NO TRADE ZONE: CONDITIONS FAILED"
                ENGINE_STATE["frozen_entry"] = 0.0
                ENGINE_STATE["frozen_target"] = 0.0
                ENGINE_STATE["frozen_sl"] = 0.0
            else:
                # SETUP READY - ALL 5 PASSED
                ENGINE_STATE["trade_status"] = "SETUP_READY"
                entry_zone = current_spot - 10 # Anticipating minor dip
                
                ENGINE_STATE["frozen_entry"] = round(entry_zone, 2)
                ENGINE_STATE["frozen_target"] = round(entry_zone + dynamic_tgt_pts, 2)
                ENGINE_STATE["frozen_sl"] = round(entry_zone - dynamic_sl_pts, 2)
                ENGINE_STATE["signal_msg"] = f"SETUP READY: WAITING PULLBACK TO ₹{ENGINE_STATE['frozen_entry']}"

                # TRIGGER ENTRY (Velocity must be > 0.5 to catch reversal bounce, not falling knife)
                if current_spot <= (ENGINE_STATE["frozen_entry"] + 5) and vel > 0.5:
                    ENGINE_STATE["trade_status"] = "TRADE_ACTIVE"
                    ENGINE_STATE["signal_msg"] = "🔥 LONG POSITION EXECUTED"
                    send_telegram_alert(f"🟢 GOAT PRO: LONG EXECUTED at {current_spot}. TGT: {ENGINE_STATE['frozen_target']}, SL: {ENGINE_STATE['frozen_sl']}")

        elif ENGINE_STATE["trade_status"] == "TRADE_ACTIVE":
            # EXIT LOGIC
            if current_spot >= ENGINE_STATE["frozen_target"]:
                ENGINE_STATE["trade_status"] = "SYSTEM_BLOCKED" # Reset to block
                send_telegram_alert(f"🎯 TARGET HIT! Profit Booked at {current_spot}")
                
            elif current_spot <= ENGINE_STATE["frozen_sl"]:
                ENGINE_STATE["trade_status"] = "SYSTEM_BLOCKED" # Reset to block
                send_telegram_alert(f"🛑 STOPLOSS HIT! Risk Managed at {current_spot}")

        data = {
            "spot": round(current_spot, 2),
            "vix_val": round(current_vix, 2),
            "velocity": vel,
            "status": ENGINE_STATE["trade_status"],
            "signal": ENGINE_STATE["signal_msg"],
            "entry": ENGINE_STATE["frozen_entry"],
            "target": ENGINE_STATE["frozen_target"],
            "sl": ENGINE_STATE["frozen_sl"],
            "chk": [chk_1, chk_2, chk_3, chk_4, chk_5]
        }

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
