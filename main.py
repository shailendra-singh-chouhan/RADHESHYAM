"""
GOAT PRO — Real Angel One + Telegram Bot
Render Deploy Ready
"""

import os
import json
import time
import threading
import pyotp
import telebot
import yfinance as yf
from flask import Flask, jsonify
from SmartApi import SmartConnect

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
BOT_TOKEN      = os.environ.get("BOT_TOKEN", ""8976682125:AAHHlimA_5-OyYYSShL_cKacdrVvfHKjjtE)
CLIENT_ID      = os.environ.get("ANGEL_CLIENT_ID", "S430269")
API_KEY        = os.environ.get("ANGEL_API_KEY", "Z3MfL8Os")
TOTP_SECRET    = os.environ.get("ANGEL_TOTP_SECRET", "HPA3VQOM2HT3WI74RF2RQPDKIY")
MPIN           = os.environ.get("ANGEL_MPIN", "2580")

bot = None
if BOT_TOKEN:
    try:
        bot = telebot.TeleBot(BOT_TOKEN)
        print("✅ Bot initialized")
    except Exception as e:
        print(f"⚠️ Bot error: {e}")

# ─────────────────────────────────────────
# FLASK
# ─────────────────────────────────────────
app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"status": "ok", "message": "GOAT PRO Running"}), 200

@app.route('/health')
def health():
    return jsonify({"status": "ok", "bot": "running" if bot else "no token"}), 200

@app.route('/api/market')
def market_api():
    """Dashboard ke liye live data endpoint"""
    data = get_all_market_data()
    return jsonify(data), 200

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
    global angel_last_login
    # Re-login every 6 hours
    if not angel_obj or (time.time() - angel_last_login) > 21600:
        angel_login()

# ─────────────────────────────────────────
# NIFTY DATA — Angel One
# ─────────────────────────────────────────
def get_nifty_live():
    try:
        ensure_angel_login()
        if angel_obj:
            data = angel_obj.ltpData("NSE", "Nifty 50", "99926000")
            ltp = data['data']['ltp']
            # Get prev close via yfinance
            yf_nifty = yf.Ticker("^NSEI")
            prev = yf_nifty.fast_info['previous_close']
            change = round(ltp - prev, 2)
            pchg = round((change / prev) * 100, 2)
            return ltp, change, pchg
    except Exception as e:
        print(f"Angel NIFTY error: {e}")
    # Fallback to yfinance
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
    except:
        return None, None, None

# ─────────────────────────────────────────
# OI + PCR — Angel One Option Chain
# ─────────────────────────────────────────
def get_oi_pcr():
    try:
        ensure_angel_login()
        if angel_obj:
            # Get option chain for NIFTY
            data = angel_obj.optionGreeks({
                "name": "NIFTY",
                "expirydate": get_nearest_expiry()
            })
            if data and data.get('data'):
                records = data['data']
                total_ce_oi = sum(r.get('openInterest', 0) for r in records if r.get('optionType') == 'CE')
                total_pe_oi = sum(r.get('openInterest', 0) for r in records if r.get('optionType') == 'PE')
                pcr = round(total_pe_oi / total_ce_oi, 3) if total_ce_oi else None
                return pcr, total_ce_oi, total_pe_oi
    except Exception as e:
        print(f"OI/PCR error: {e}")
    # Fallback — VIX proxy
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
    # Next Thursday
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
        crude = yf.Ticker("CL=F")
        gold  = yf.Ticker("GC=F")
        silver= yf.Ticker("SI=F")
        gas   = yf.Ticker("NG=F")
        usd_inr = yf.Ticker("INR=X")
        inr = usd_inr.fast_info['last_price']
        return {
            'crude_usd': round(crude.fast_info['last_price'], 2),
            'crude_inr': round(crude.fast_info['last_price'] * inr * 0.159, 2),
            'gold_usd':  round(gold.fast_info['last_price'], 2),
            'gold_inr':  round(gold.fast_info['last_price'] * inr * 0.0321, 2),
            'silver_usd':round(silver.fast_info['last_price'], 2),
            'silver_inr':round(silver.fast_info['last_price'] * inr * 0.0321, 2),
            'gas_usd':   round(gas.fast_info['last_price'], 3),
            'gas_inr':   round(gas.fast_info['last_price'] * inr * 0.036, 2),
            'usd_inr':   round(inr, 2)
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

    # PCR filter
    if pcr < 0.7:
        score += 1
        reasons.append("✅ PCR Bullish")
    elif pcr > 1.3:
        score += 1
        reasons.append("✅ PCR Bearish")
    else:
        reasons.append("❌ PCR Neutral")

    # Price filter
    if change > 0:
        score += 1
        reasons.append("✅ Price +ve")
    elif change < 0:
        score += 1
        reasons.append("✅ Price -ve")
    else:
        reasons.append("❌ Price Flat")

    # Momentum
    if abs(pchg) > 0.4:
        score += 1
        reasons.append("✅ Momentum Strong")
    else:
        reasons.append("❌ Momentum Weak")

    # VIX filter
    if vix:
        if vix < 15:
            score += 1
            reasons.append("✅ VIX Low (stable)")
        elif vix > 22:
            reasons.append("⚠️ VIX High (risky)")

    # Direction
    if score >= 3:
        if pcr < 0.7 and change > 0:
            direction = "🚀 STRONG BUY — CE खरीदो"
        elif pcr > 1.3 and change < 0:
            direction = "💀 STRONG SELL — PE खरीदो"
        else:
            direction = "⚠️ MIXED — Wait"
    else:
        direction = "⏸️ WAIT — No trade zone"

    return {
        'direction': direction,
        'score': score,
        'reasons': reasons
    }

# ─────────────────────────────────────────
# ALL MARKET DATA — Dashboard API
# ─────────────────────────────────────────
def get_all_market_data():
    nifty_price, nifty_chg, nifty_pchg = get_nifty_live()
    bn_price, bn_chg, bn_pchg = get_banknifty_live()
    pcr, ce_oi, pe_oi = get_oi_pcr()
    vix = get_vix()
    mcx = get_mcx_data()
    signal = generate_signal(nifty_price, nifty_chg, nifty_pchg, pcr, vix)

    return {
        'nifty': {
            'price': nifty_price,
            'change': nifty_chg,
            'pchg': nifty_pchg
        },
        'banknifty': {
            'price': bn_price,
            'change': bn_chg,
            'pchg': bn_pchg
        },
        'pcr': pcr,
        'ce_oi': ce_oi,
        'pe_oi': pe_oi,
        'vix': vix,
        'mcx': mcx,
        'signal': signal,
        'timestamp': time.strftime("%H:%M:%S")
    }

# ─────────────────────────────────────────
# TELEGRAM BOT COMMANDS
# ─────────────────────────────────────────
if bot:

    @bot.message_handler(commands=['start', 'help'])
    def start_cmd(m):
        bot.reply_to(m, """⚡ GOAT PRO Trading Bot

📊 Commands:
/nifty    — NIFTY 50 live price
/banknifty — BankNifty live price
/signal   — Full F&O signal
/pcr      — OI + PCR analysis
/mcx      — Crude, Gold, Silver
/vix      — India VIX
/status   — Full market overview
/alert ON/OFF — Auto alerts""")

    @bot.message_handler(commands=['nifty'])
    def nifty_cmd(m):
        bot.reply_to(m, "⏳ Fetching live data...")
        price, change, pchg = get_nifty_live()
        if not price:
            bot.reply_to(m, "🔴 Data unavailable")
            return
        trend = "🟢 UP" if change > 0 else ("🔴 DOWN" if change < 0 else "⚪ FLAT")
        bot.reply_to(m, f"""📈 NIFTY 50 — LIVE

💰 Price : {price:,.2f}
📊 Change: {'+' if change > 0 else ''}{change} ({pchg}%)
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
📊 Change: {'+' if change > 0 else ''}{change} ({pchg}%)
📉 Trend : {trend}""")

    @bot.message_handler(commands=['pcr'])
    def pcr_cmd(m):
        pcr, ce_oi, pe_oi = get_oi_pcr()
        vix = get_vix()
        if not pcr:
            bot.reply_to(m, "🔴 PCR data unavailable")
            return
        sig = "🚀 BULLISH" if pcr < 0.7 else ("💀 BEARISH" if pcr > 1.3 else "⏸️ NEUTRAL")
        oi_text = ""
        if ce_oi and pe_oi:
            oi_text = f"\n📊 CE OI: {ce_oi/100000:.1f}L\n📊 PE OI: {pe_oi/100000:.1f}L"
        bot.reply_to(m, f"""📊 OI + PCR Analysis

PCR     : {pcr} {sig}
VIX     : {vix if vix else 'N/A'}{oi_text}

{'🔴 Bearish — PE side' if pcr > 1.3 else '🟢 Bullish — CE side' if pcr < 0.7 else '⏸️ Wait for clarity'}""")

    @bot.message_handler(commands=['signal'])
    def signal_cmd(m):
        bot.reply_to(m, "⏳ Analyzing market...")
        price, change, pchg = get_nifty_live()
        pcr, _, _ = get_oi_pcr()
        vix = get_vix()
        if not price:
            bot.reply_to(m, "🔴 Data fetch failed")
            return
        sig = generate_signal(price, change, pchg, pcr, vix)
        if isinstance(sig, str):
            bot.reply_to(m, sig)
            return
        reasons_text = "\n".join(sig['reasons'])
        bot.reply_to(m, f"""⚡ GOAT PRO SIGNAL

📈 NIFTY : {price:,.2f} ({'+' if change > 0 else ''}{change} | {pchg}%)
😨 VIX   : {vix if vix else 'N/A'}
📊 PCR   : {pcr if pcr else 'N/A'}

🔍 Filters:
{reasons_text}

✅ Score : {sig['score']}/4
🎯 Signal: {sig['direction']}

⚠️ Educational only — Apni risk pe trade karo!""")

    @bot.message_handler(commands=['mcx'])
    def mcx_cmd(m):
        d = get_mcx_data()
        if not d:
            bot.reply_to(m, "🔴 MCX unavailable")
            return
        bot.reply_to(m, f"""🏭 MCX COMMODITIES — LIVE

🛢️ Crude Oil
   ${d['crude_usd']} USD | ₹{d['crude_inr']} INR

🥇 Gold
   ${d['gold_usd']} USD | ₹{d['gold_inr']} INR/g

🥈 Silver
   ${d['silver_usd']} USD | ₹{d['silver_inr']} INR/g

⛽ Natural Gas
   ${d['gas_usd']} USD | ₹{d['gas_inr']} INR

💱 USD/INR: {d['usd_inr']}""")

    @bot.message_handler(commands=['vix'])
    def vix_cmd(m):
        vix = get_vix()
        if not vix:
            bot.reply_to(m, "🔴 VIX unavailable")
            return
        level = "😌 LOW" if vix < 13 else ("😨 HIGH" if vix > 20 else "😐 NORMAL")
        tip = "✅ Options buy karo" if vix < 15 else ("⚠️ Risky — chhota size lo" if vix > 20 else "👍 Normal trading")
        bot.reply_to(m, f"""😨 INDIA VIX

VIX   : {vix}
Level : {level}
Tip   : {tip}""")

    @bot.message_handler(commands=['status'])
    def status_cmd(m):
        bot.reply_to(m, "⏳ Full market scan...")
        data = get_all_market_data()
        n = data['nifty']
        bn = data['banknifty']
        mcx = data['mcx']
        msg = f"""📊 GOAT PRO — MARKET STATUS
⏰ {data['timestamp']}

{'🟢' if n['change'] and n['change'] > 0 else '🔴'} NIFTY   : {n['price']:,.2f} ({'+' if n['change'] and n['change'] > 0 else ''}{n['change']})
{'🟢' if bn['change'] and bn['change'] > 0 else '🔴'} BANKNIFTY: {bn['price']:,.2f}
😨 VIX     : {data['vix']}
📊 PCR     : {data['pcr']}
"""
        if mcx:
            msg += f"""
🛢️ Crude  : ₹{mcx['crude_inr']}
🥇 Gold   : ₹{mcx['gold_inr']}
💱 USD/INR: {mcx['usd_inr']}
"""
        if isinstance(data['signal'], dict):
            msg += f"\n🎯 Signal: {data['signal']['direction']}"
        bot.reply_to(m, msg)

# ─────────────────────────────────────────
# AUTO ALERT SYSTEM
# ─────────────────────────────────────────
alert_users = {}
last_signal = None

def auto_alert_worker():
    global last_signal
    while True:
        try:
            if not alert_users:
                time.sleep(60)
                continue
            price, change, pchg = get_nifty_live()
            pcr, _, _ = get_oi_pcr()
            vix = get_vix()
            sig = generate_signal(price, change, pchg, pcr, vix)
            if isinstance(sig, dict) and sig['direction'] != last_signal:
                if sig['score'] >= 3:
                    last_signal = sig['direction']
                    msg = f"""🚨 GOAT PRO AUTO ALERT!

📈 NIFTY: {price:,.2f} ({'+' if change > 0 else ''}{change})
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
        time.sleep(300)  # Check every 5 min

if bot:
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
            bot.reply_to(m, "🔔 Auto alerts ON! Har 5 min mein signal check hoga.")
        elif args[1].upper() == 'OFF':
            alert_users.pop(chat_id, None)
            bot.reply_to(m, "🔕 Auto alerts OFF.")

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

    # Angel One login
    if all([CLIENT_ID, API_KEY, TOTP_SECRET, MPIN]):
        print("🔑 Angel One credentials found — logging in...")
        angel_login()
    else:
        print("⚠️ Angel One credentials missing!")

    # Bot thread
    if bot:
        t1 = threading.Thread(target=run_bot, daemon=True)
        t1.start()
        print("✅ Bot thread started")

    # Auto alert thread
    if bot:
        t2 = threading.Thread(target=auto_alert_worker, daemon=True)
        t2.start()
        print("✅ Alert thread started")

    port = int(os.environ.get("PORT", 10000))
    print(f"🌐 Flask on port {port}")
    from flask import Flask
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
