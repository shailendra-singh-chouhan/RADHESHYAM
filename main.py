"""
GOAT PRO — Real Angel One + Telegram Bot
Render Deploy Ready — CORS Fixed
"""

import os
import time
import threading
import pyotp
import telebot
import yfinance as yf
from flask import Flask, jsonify
from flask_cors import CORS
from SmartApi import SmartConnect

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
CLIENT_ID   = os.environ.get("ANGEL_CLIENT_ID", "")
API_KEY     = os.environ.get("ANGEL_API_KEY", "")
TOTP_SECRET = os.environ.get("ANGEL_TOTP_SECRET", "")
MPIN        = os.environ.get("ANGEL_MPIN", "")

bot = None
if BOT_TOKEN:
    try:
        bot = telebot.TeleBot(BOT_TOKEN)
        print("✅ Bot initialized")
    except Exception as e:
        print(f"⚠️ Bot error: {e}")
else:
    print("⚠️ BOT_TOKEN not set!")

# ─────────────────────────────────────────
# FLASK + CORS
# ─────────────────────────────────────────
app = Flask(__name__)
CORS(app)

@app.route('/')
def home():
    return jsonify({"status": "ok", "message": "GOAT PRO Running"}), 200

@app.route('/health')
def health():
    return jsonify({"status": "ok", "bot": "running" if bot else "no token"}), 200

@app.route('/api/market')
def market_api():
    try:
        data = get_all_market_data()
        return jsonify(data), 200
    except Exception as e:
        print(f"❌ /api/market error: {e}")
        return jsonify({"error": str(e)}), 500

# ─────────────────────────────────────────
# ANGEL ONE LOGIN
# ─────────────────────────────────────────
angel_obj = None
angel_token = None
angel_last_login = 0

def angel_login():
    global angel_obj, angel_token, angel_last_login
    try:
        totp = pyotp.TOTP(TOTP_SECRET).now()
        obj = SmartConnect(api_key=API_KEY)
        data = obj.generateSession(CLIENT_ID, MPIN, totp)
        if data['status']:
            angel_obj = obj
            angel_token = data['data']['jwtToken']
            angel_last_login = time.time()
            print("✅ Angel One login successful")
            return True
        else:
            print(f"❌ Angel One login failed: {data['message']}")
            return False
    except Exception as e:
        print(f"❌ Angel One error: {e}")
        return False

def ensure_angel_login():
    if not angel_obj or (time.time() - angel_last_login) > 21600:
        angel_login()

# ─────────────────────────────────────────
# NIFTY DATA
# ─────────────────────────────────────────
def get_nifty_live():
    try:
        ensure_angel_login()
        if angel_obj:
            data = angel_obj.ltpData("NSE", "Nifty 50", "99926000")
            ltp = data['data']['ltp']
            yf_nifty = yf.Ticker("^NSEI")
            prev = yf_nifty.fast_info['previous_close']
            change = round(ltp - prev, 2)
            pchg = round((change / prev) * 100, 2)
            return ltp, change, pchg
    except Exception as e:
        print(f"Angel NIFTY error: {e}")
    try:
        t = yf.Ticker("^NSEI")
        info = t.fast_info
        price = round(info['last_price'], 2)
        prev = round(info['previous_close'], 2)
        change = round(price - prev, 2)
        pchg = round((change / prev) * 100, 2)
        return price, change, pchg
    except Exception as e:
        print(f"yfinance NIFTY error: {e}")
        return None, None, None

# ─────────────────────────────────────────
# BANKNIFTY DATA
# ─────────────────────────────────────────
def get_banknifty_live():
    try:
        ensure_angel_login()
        if angel_obj:
            data = angel_obj.ltpData("NSE", "Nifty Bank", "99926009")
            ltp = data['data']['ltp']
            yf_bn = yf.Ticker("^NSEBANK")
            prev = yf_bn.fast_info['previous_close']
            change = round(ltp - prev, 2)
            pchg = round((change / prev) * 100, 2)
            return ltp, change, pchg
    except Exception as e:
        print(f"Angel BankNifty error: {e}")
    try:
        t = yf.Ticker("^NSEBANK")
        info = t.fast_info
        price = round(info['last_price'], 2)
        prev = round(info['previous_close'], 2)
        change = round(price - prev, 2)
        pchg = round((change / prev) * 100, 2)
        return price, change, pchg
    except Exception as e:
        print(f"yfinance BankNifty error: {e}")
        return None, None, None

# ─────────────────────────────────────────
# PCR + OI
# ─────────────────────────────────────────
def get_oi_pcr():
    try:
        ensure_angel_login()
        if angel_obj:
            data = angel_obj.optionGreeks({
                "name": "NIFTY",
                "expirydate": get_nearest_expiry()
            })
            if data and data.get('data'):
                records = data['data']
                total_ce = sum(r.get('openInterest', 0) for r in records if r.get('optionType') == 'CE')
                total_pe = sum(r.get('openInterest', 0) for r in records if r.get('optionType') == 'PE')
                pcr = round(total_pe / total_ce, 3) if total_ce else None
                return pcr, total_ce, total_pe
    except Exception as e:
        print(f"OI/PCR error: {e}")
    try:
        vix = yf.Ticker("^INDIAVIX")
        vix_val = round(vix.fast_info['last_price'], 2)
        if vix_val < 13:
            pcr = 0.6
        elif vix_val > 20:
            pcr = 1.4
        else:
            pcr = round(0.6 + (vix_val - 13) * (0.8 / 7), 3)
        return pcr, None, None
    except:
        return None, None, None

def get_nearest_expiry():
    from datetime import datetime, timedelta
    today = datetime.now()
    days_ahead = 3 - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    expiry = today + timedelta(days=days_ahead)
    return expiry.strftime("%d%b%Y").upper()

# ─────────────────────────────────────────
# MCX DATA
# ─────────────────────────────────────────
def get_mcx_data():
    try:
        crude   = yf.Ticker("CL=F")
        gold    = yf.Ticker("GC=F")
        silver  = yf.Ticker("SI=F")
        gas     = yf.Ticker("NG=F")
        usd_inr = yf.Ticker("INR=X")
        inr = usd_inr.fast_info['last_price']
        return {
            'crude_usd':  round(crude.fast_info['last_price'], 2),
            'crude_inr':  round(crude.fast_info['last_price'] * inr * 0.159, 2),
            'gold_usd':   round(gold.fast_info['last_price'], 2),
            'gold_inr':   round(gold.fast_info['last_price'] * inr * 0.0321, 2),
            'silver_usd': round(silver.fast_info['last_price'], 2),
            'silver_inr': round(silver.fast_info['last_price'] * inr * 0.0321, 2),
            'gas_usd':    round(gas.fast_info['last_price'], 3),
            'gas_inr':    round(gas.fast_info['last_price'] * inr * 0.036, 2),
            'usd_inr':    round(inr, 2)
        }
    except Exception as e:
        print(f"MCX error: {e}")
        return None

# ─────────────────────────────────────────
# VIX
# ─────────────────────────────────────────
def get_vix():
    try:
        vix = yf.Ticker("^INDIAVIX")
        return round(vix.fast_info['last_price'], 2)
    except:
        return None

# ─────────────────────────────────────────
# SIGNAL ENGINE
# ─────────────────────────────────────────
def generate_signal(price, change, pchg, pcr, vix):
    if not all([price, change is not None, pchg is not None, pcr]):
        return "⏳ DATA LOADING..."
    score = 0
    reasons = []
    if pcr < 0.7:
        score += 1
        reasons.append("✅ PCR Bullish")
    elif pcr > 1.3:
        score += 1
        reasons.append("✅ PCR Bearish")
    else:
        reasons.append("❌ PCR Neutral")
    if change > 0:
        score += 1
        reasons.append("✅ Price +ve")
    elif change < 0:
        score += 1
        reasons.append("✅ Price -ve")
    else:
        reasons.append("❌ Price Flat")
    if abs(pchg) > 0.4:
        score += 1
        reasons.append("✅ Momentum Strong")
    else:
        reasons.append("❌ Momentum Weak")
    if vix:
        if vix < 15:
            score += 1
            reasons.append("✅ VIX Low")
        elif vix > 22:
            reasons.append("⚠️ VIX High")
    if score >= 3:
        if pcr < 0.7 and change > 0:
            direction = "🚀 STRONG BUY — CE खरीदो"
        elif pcr > 1.3 and change < 0:
            direction = "💀 STRONG SELL — PE खरीदो"
        else:
            direction = "⚠️ MIXED — Wait"
    else:
        direction = "⏸️ WAIT — No trade zone"
    return {'direction': direction, 'score': score, 'reasons': reasons}

# ─────────────────────────────────────────
# ALL MARKET DATA
# ─────────────────────────────────────────
def get_all_market_data():
    nifty_price, nifty_chg, nifty_pchg = get_nifty_live()
    bn_price, bn_chg, bn_pchg = get_banknifty_live()
    pcr, ce_oi, pe_oi = get_oi_pcr()
    vix = get_vix()
    mcx = get_mcx_data()
    signal = generate_signal(nifty_price, nifty_chg, nifty_pchg, pcr, vix)
    return {
        'nifty':     {'price': nifty_price, 'change': nifty_chg, 'pchg': nifty_pchg},
        'banknifty': {'price': bn_price,    'change': bn_chg,    'pchg': bn_pchg},
        'pcr':       pcr,
        'ce_oi':     ce_oi,
        'pe_oi':     pe_oi,
        'vix':       vix,
        'mcx':       mcx,
        'signal':    signal,
        'timestamp': time.strftime("%H:%M:%S")
    }

# ─────────────────────────────────────────
# ALERT USERS
# ─────────────────────────────────────────
alert_users = {}
last_signal = None

# ─────────────────────────────────────────
# TELEGRAM COMMANDS
# ─────────────────────────────────────────
if bot:

    @bot.message_handler(commands=['start', 'help'])
    def start_cmd(m):
        bot.reply_to(m, """⚡ GOAT PRO Trading Bot

📊 Commands:
/nifty      — NIFTY 50 live
/banknifty  — BankNifty live
/signal     — Full F&O signal
/pcr        — OI + PCR
/mcx        — Crude Gold Silver Gas
/vix        — India VIX
/status     — Full market overview
/alert ON/OFF — Auto alerts""")

    @bot.message_handler(commands=['nifty'])
    def nifty_cmd(m):
        bot.reply_to(m, "⏳ Fetching...")
        price, change, pchg = get_nifty_live()
        if not price:
            bot.reply_to(m, "🔴 Data unavailable")
            return
        trend = "🟢 UP" if change > 0 else ("🔴 DOWN" if change < 0 else "⚪ FLAT")
        bot.reply_to(m, f"""📈 NIFTY 50 — LIVE
💰 Price : {price:,.2f}
📊 Change: {'+' if change>0 else ''}{change} ({pchg}%)
📉 Trend : {trend}
⏰ Time  : {time.strftime("%H:%M:%S")}""")

    @bot.message_handler(commands=['banknifty'])
    def bn_cmd(m):
        bot.reply_to(m, "⏳ Fetching...")
        price, change, pchg = get_banknifty_live()
        if not price:
            bot.reply_to(m, "🔴 Data unavailable")
            return
        trend = "🟢 UP" if change > 0 else "🔴 DOWN"
        bot.reply_to(m, f"""🏦 BANKNIFTY — LIVE
💰 Price : {price:,.2f}
📊 Change: {'+' if change>0 else ''}{change} ({pchg}%)
📉 Trend : {trend}""")

    @bot.message_handler(commands=['pcr'])
    def pcr_cmd(m):
        pcr, ce_oi, pe_oi = get_oi_pcr()
        vix = get_vix()
        if not pcr:
            bot.reply_to(m, "🔴 PCR unavailable")
            return
        sig = "🚀 BULLISH" if pcr < 0.7 else ("💀 BEARISH" if pcr > 1.3 else "⏸️ NEUTRAL")
        oi_text = f"\n📊 CE OI: {ce_oi/100000:.1f}L\n📊 PE OI: {pe_oi/100000:.1f}L" if ce_oi and pe_oi else ""
        bot.reply_to(m, f"""📊 OI + PCR
PCR : {pcr} {sig}
VIX : {vix or 'N/A'}{oi_text}""")

    @bot.message_handler(commands=['signal'])
    def signal_cmd(m):
        bot.reply_to(m, "⏳ Analyzing...")
        price, change, pchg = get_nifty_live()
        pcr, _, _ = get_oi_pcr()
        vix = get_vix()
        if not price:
            bot.reply_to(m, "🔴 Data failed")
            return
        sig = generate_signal(price, change, pchg, pcr, vix)
        if isinstance(sig, str):
            bot.reply_to(m, sig)
            return
        bot.reply_to(m, f"""⚡ GOAT PRO SIGNAL
📈 NIFTY : {price:,.2f} ({'+' if change>0 else ''}{change} | {pchg}%)
😨 VIX   : {vix or 'N/A'}
📊 PCR   : {pcr or 'N/A'}

🔍 Filters:
{chr(10).join(sig['reasons'])}

✅ Score : {sig['score']}/4
🎯 Signal: {sig['direction']}

⚠️ Educational only!""")

    @bot.message_handler(commands=['mcx'])
    def mcx_cmd(m):
        d = get_mcx_data()
        if not d:
            bot.reply_to(m, "🔴 MCX unavailable")
            return
        bot.reply_to(m, f"""🏭 MCX LIVE
🛢️ Crude : ${d['crude_usd']} | ₹{d['crude_inr']}
🥇 Gold  : ${d['gold_usd']} | ₹{d['gold_inr']}/g
🥈 Silver: ${d['silver_usd']}
⛽ Gas   : ${d['gas_usd']}
💱 USD/INR: {d['usd_inr']}""")

    @bot.message_handler(commands=['vix'])
    def vix_cmd(m):
        vix = get_vix()
        if not vix:
            bot.reply_to(m, "🔴 VIX unavailable")
            return
        level = "😌 LOW" if vix < 13 else ("😨 HIGH" if vix > 20 else "😐 NORMAL")
        tip = "✅ Options buy karo" if vix < 15 else ("⚠️ Risky — chhota size" if vix > 20 else "👍 Normal")
        bot.reply_to(m, f"""😨 INDIA VIX
VIX   : {vix}
Level : {level}
Tip   : {tip}""")

    @bot.message_handler(commands=['status'])
    def status_cmd(m):
        bot.reply_to(m, "⏳ Full scan...")
        data = get_all_market_data()
        n = data['nifty']
        bn = data['banknifty']
        mcx = data['mcx']
        msg = f"""📊 GOAT PRO STATUS
⏰ {data['timestamp']}
{'🟢' if (n.get('change') or 0) > 0 else '🔴'} NIFTY    : {n.get('price') or '--'}
{'🟢' if (bn.get('change') or 0) > 0 else '🔴'} BANKNIFTY: {bn.get('price') or '--'}
😨 VIX      : {data['vix'] or '--'}
📊 PCR      : {data['pcr'] or '--'}
"""
        if mcx:
            msg += f"""🛢️ Crude  : ₹{mcx['crude_inr']}
🥇 Gold   : ₹{mcx['gold_inr']}/g
💱 USD/INR: {mcx['usd_inr']}
"""
        if isinstance(data['signal'], dict):
            msg += f"\n🎯 {data['signal']['direction']}"
        bot.reply_to(m, msg)

    @bot.message_handler(commands=['alert'])
    def alert_cmd(m):
        chat_id = m.chat.id
        args = m.text.split()
        if len(args) < 2:
            status = "ON" if chat_id in alert_users else "OFF"
            bot.reply_to(m, f"🔔 Alert: {status}\n/alert ON  or  /alert OFF")
            return
        if args[1].upper() == 'ON':
            alert_users[chat_id] = True
            bot.reply_to(m, "🔔 Auto alerts ON!")
        elif args[1].upper() == 'OFF':
            alert_users.pop(chat_id, None)
            bot.reply_to(m, "🔕 Alerts OFF.")

# ─────────────────────────────────────────
# AUTO ALERTS
# ─────────────────────────────────────────
def auto_alert_worker():
    global last_signal
    while True:
        try:
            if alert_users:
                price, change, pchg = get_nifty_live()
                pcr, _, _ = get_oi_pcr()
                vix = get_vix()
                sig = generate_signal(price, change, pchg, pcr, vix)
                if isinstance(sig, dict) and sig['direction'] != last_signal and sig['score'] >= 3:
                    last_signal = sig['direction']
                    msg = f"""🚨 GOAT PRO ALERT!
📈 NIFTY: {price:,.2f}
📊 PCR  : {pcr}
😨 VIX  : {vix}
🎯 {sig['direction']}
⚠️ Educational only!"""
                    for chat_id in list(alert_users.keys()):
                        try:
                            bot.send_message(chat_id, msg)
                        except:
                            pass
        except Exception as e:
            print(f"Alert error: {e}")
        time.sleep(300)

# ─────────────────────────────────────────
# BOT POLLING
# ─────────────────────────────────────────
def run_bot():
    if not bot:
        return
    while True:
        try:
            print("🤖 Bot polling started...")
            bot.infinity_polling(timeout=30, long_polling_timeout=30)
        except Exception as e:
            print(f"❌ Polling error: {e}")
            time.sleep(5)

# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────
if __name__ == "__main__":
    print("✅ GOAT PRO starting...")
    if all([CLIENT_ID, API_KEY, TOTP_SECRET, MPIN]):
        print("🔑 Angel One credentials found — logging in...")
        angel_login()
    else:
        print("⚠️ Angel One credentials missing!")
    if bot:
        threading.Thread(target=run_bot, daemon=True).start()
        print("✅ Bot thread started")
        threading.Thread(target=auto_alert_worker, daemon=True).start()
        print("✅ Alert thread started")
    port = int(os.environ.get("PORT", 10000))
    print(f"🌐 Flask on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
