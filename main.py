import os
import telebot
import threading
import time
import yfinance as yf
from flask import Flask, jsonify


bot = None

BOT_TOKEN = "8611386223:AAG-eJynNK-6Bfo_csbHE-KgDN9rp666AUI"
    try:
        bot = telebot.TeleBot(BOT_TOKEN)
        print("✅ Bot initialized successfully")
    except Exception as e:
        print(f"⚠️ Bot init error: {e}")
        bot = None
else:
    print("⚠️ BOT_TOKEN not set!")

app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"status": "ok", "message": "NIFTY Bot running"}), 200

@app.route('/health')
def health():
    return jsonify({"status": "ok", "bot": "running" if bot else "no token"}), 200

def get_nifty_data():
    try:
        ticker = yf.Ticker("^NSEI")
        info = ticker.fast_info
        price = round(info['last_price'], 2)
        prev = round(info['previous_close'], 2)
        change = round(price - prev, 2)
        pchg = round((change / prev) * 100, 2)
        return price, change, pchg
    except Exception as e:
        print(f"NIFTY error: {e}")
        return None, None, None

def get_vix_data():
    try:
        vix = yf.Ticker("^INDIAVIX")
        vix_val = round(vix.fast_info['last_price'], 2)
        if vix_val < 13:
            pcr = 0.6
        elif vix_val > 20:
            pcr = 1.4
        else:
            pcr = round(0.6 + (vix_val - 13) * (0.8 / 7), 3)
        return pcr, vix_val
    except Exception as e:
        print(f"VIX error: {e}")
        return None, None

def get_mcx_data():
    try:
        crude = yf.Ticker("CL=F")
        gas = yf.Ticker("NG=F")
        usd_inr = yf.Ticker("INR=X")
        crude_usd = crude.fast_info['last_price']
        gas_usd = gas.fast_info['last_price']
        inr = usd_inr.fast_info['last_price']
        return {
            'crude_usd': round(crude_usd, 2),
            'crude_inr': round(crude_usd * inr * 0.159, 2),
            'gas_usd': round(gas_usd, 3),
            'gas_inr': round(gas_usd * inr * 0.036, 2),
            'usd_inr': round(inr, 2)
        }
    except Exception as e:
        print(f"MCX error: {e}")
        return None

if bot:

    @bot.message_handler(commands=['start', 'help'])
    def start_cmd(m):
        bot.reply_to(m, """🤖 NIFTY Trading Bot

📊 Commands:
/nifty   — NIFTY 50 price
/signal  — Full signal
/pcr     — VIX based PCR
/mcx     — Crude + Gas
/status  — Market overview""")

    @bot.message_handler(commands=['nifty'])
    def nifty_cmd(m):
        price, change, pchg = get_nifty_data()
        if not price:
            bot.reply_to(m, "🔴 Data unavailable")
            return
        trend = "🟢 UP" if change > 0 else ("🔴 DOWN" if change < 0 else "⚪ FLAT")
        bot.reply_to(m, f"""📈 NIFTY 50

💰 Price : {price}
📊 Change: {'+' if change > 0 else ''}{change} ({pchg}%)
📉 Trend : {trend}""")

    @bot.message_handler(commands=['pcr'])
    def pcr_cmd(m):
        pcr, vix = get_vix_data()
        if not pcr:
            bot.reply_to(m, "🔴 Data unavailable")
            return
        if pcr < 0.7:
            sig = "🚀 BULLISH — CE side"
        elif pcr > 1.3:
            sig = "💀 BEARISH — PE side"
        else:
            sig = "⏸️ NEUTRAL — Wait"
        bot.reply_to(m, f"""📊 PCR (VIX Proxy)

India VIX : {vix}
PCR Proxy : {pcr}
Signal    : {sig}""")

    @bot.message_handler(commands=['signal'])
    def signal_cmd(m):
        price, change, pchg = get_nifty_data()
        pcr, vix = get_vix_data()
        if not price or not pcr:
            bot.reply_to(m, "🔴 Data fetch failed")
            return
        filters = 0
        lines = []
        if pcr < 0.7:
            filters += 1
            lines.append("✅ PCR Bullish")
        elif pcr > 1.3:
            filters += 1
            lines.append("✅ PCR Bearish")
        else:
            lines.append("❌ PCR Neutral")
        if change > 0:
            filters += 1
            lines.append("✅ Price Positive")
        elif change < 0:
            filters += 1
            lines.append("✅ Price Negative")
        else:
            lines.append("❌ Price Flat")
        if abs(pchg) > 0.4:
            filters += 1
            lines.append("✅ Momentum Strong")
        else:
            lines.append("❌ Momentum Weak")
        if filters >= 3:
            if pcr < 0.7 and change > 0:
                direction = "🚀 STRONG BUY — CE खरीदो"
            elif pcr > 1.3 and change < 0:
                direction = "💀 STRONG SELL — PE खरीदो"
            else:
                direction = "⚠️ MIXED — Wait"
        else:
            direction = "⏸️ WAIT — No trade zone"
        msg = f"""📈 NIFTY: {price} ({'+' if change>0 else ''}{change} | {pchg}%)
😨 VIX: {vix} | PCR: {pcr}

🔍 Filters:
""" + "\n".join(lines) + f"""

✅ Passed: {filters}/3
🎯 Signal: {direction}"""
        bot.reply_to(m, msg)

    @bot.message_handler(commands=['mcx'])
    def mcx_cmd(m):
        d = get_mcx_data()
        if not d:
            bot.reply_to(m, "🔴 MCX data unavailable")
            return
        bot.reply_to(m, f"""🏭 MCX Commodities

🛢️ Crude : ${d['crude_usd']} | ₹{d['crude_inr']}
⛽ Gas   : ${d['gas_usd']} | ₹{d['gas_inr']}
💱 USD/INR: {d['usd_inr']}""")

    @bot.message_handler(commands=['status'])
    def status_cmd(m):
        price, change, pchg = get_nifty_data()
        pcr, vix = get_vix_data()
        mcx = get_mcx_data()
        msg = "📊 Market Status\n\n"
        if price:
            t = "🟢" if change > 0 else "🔴"
            msg += f"{t} NIFTY: {price} ({'+' if change>0 else ''}{change})\n"
        if vix:
            msg += f"😨 VIX: {vix}\n"
        if mcx:
            msg += f"🛢️ Crude: ₹{mcx['crude_inr']} | ${mcx['crude_usd']}\n"
            msg += f"💱 USD/INR: {mcx['usd_inr']}\n"
        bot.reply_to(m, msg)

def run_bot():
    if not bot:
        print("⚠️ No bot token")
        return
    while True:
        try:
            print("🤖 Bot polling started...")
            bot.infinity_polling(timeout=30, long_polling_timeout=30)
        except Exception as e:
            print(f"❌ Polling error: {e}")
            time.sleep(5)
            print("🔄 Restarting...")

if __name__ == "__main__":
    print("✅ App starting...")
    if bot:
        t = threading.Thread(target=run_bot, daemon=True)
        t.start()
        print("✅ Bot thread started")
    else:
        print("⚠️ Set BOT_TOKEN in Render Environment!")
    port = int(os.environ.get("PORT", 10000))
    print(f"🌐 Flask on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
