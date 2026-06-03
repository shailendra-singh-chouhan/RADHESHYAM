"""
NIFTY + MCX Trading Signal Bot
Full Clean Version — Render Deploy Ready
"""

import os
import telebot
import requests
import threading
import time
import datetime
import pytz
import yfinance as yf
from flask import Flask

# ─────────────────────────────────────────
# 🔑 CONFIG — Environment Variable से लेगा
# ─────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TOKEN_HERE")
bot = telebot.TeleBot(BOT_TOKEN)

# ─────────────────────────────────────────
# 🌐 FLASK SERVER — Render ke liye zaroori
# (UptimeRobot isko ping karega, bot jaaga rahega)
# ─────────────────────────────────────────
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ NIFTY Bot is running!", 200

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# Flask ko background thread mein chalao
threading.Thread(target=run_flask, daemon=True).start()

# ─────────────────────────────────────────
# 📦 GLOBAL STATE
# ─────────────────────────────────────────
alerts = {}          # chat_id: {'status': 'ON'}
schedule_status = {} # chat_id: 'ON' / 'OFF'

# ─────────────────────────────────────────
# 🛠️ HELPER: NSE Session
# ─────────────────────────────────────────
NSE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Accept': '*/*',
    'Referer': 'https://www.nseindia.com/'
}

def get_nse_session():
    session = requests.Session()
    session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=6)
    return session

# ─────────────────────────────────────────
# 📈 HELPER: NIFTY Data
# ─────────────────────────────────────────
def get_nifty_data():
    try:
        session = get_nse_session()
        url = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050"
        res = session.get(url, headers=NSE_HEADERS, timeout=6)
        data = res.json()
        for item in data['data']:
            if item['indexSymbol'] == 'NIFTY 50':
                price  = item['last']
                change = round(item['variation'], 2)
                pchg   = round(item['percentChange'], 2)
                return price, change, pchg
    except:
        pass
    return None, None, None

# ─────────────────────────────────────────
# 📊 HELPER: PCR Data
# ─────────────────────────────────────────
def get_pcr_data():
    try:
        session = get_nse_session()
        url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
        res = session.get(url, headers=NSE_HEADERS, timeout=6)
        data = res.json()
        records = data['records']['data']
        total_pe = sum(i['PE']['openInterest'] for i in records if 'PE' in i)
        total_ce = sum(i['CE']['openInterest'] for i in records if 'CE' in i)
        pcr = round(total_pe / total_ce, 3) if total_ce else None
        return pcr, total_pe, total_ce
    except:
        pass
    return None, None, None

# ─────────────────────────────────────────
# 🛢️ HELPER: MCX Data (via yfinance)
# ─────────────────────────────────────────
def get_mcx_data():
    try:
        crude = yf.Ticker("CL=F")
        gas   = yf.Ticker("NG=F")
        usd_inr = yf.Ticker("INR=X")

        crude_usd = crude.fast_info['last_price']
        gas_usd   = gas.fast_info['last_price']
        inr_rate  = usd_inr.fast_info['last_price']

        crude_inr = round(crude_usd * inr_rate * 0.159, 2)  # per barrel → per litre approx
        gas_inr   = round(gas_usd  * inr_rate * 0.036, 2)   # mmBtu → approx MCX unit

        return {
            'crude_usd': crude_usd,
            'crude_inr': crude_inr,
            'gas_usd':   gas_usd,
            'gas_inr':   gas_inr,
            'usd_inr':   round(inr_rate, 2)
        }
    except:
        return None

# ─────────────────────────────────────────
# 🧠 HELPER: Signal Logic
# ─────────────────────────────────────────
def generate_signal_text(price, change, pChange, pcr):
    filters_pass = 0
    reasons = []

    if pcr < 0.7:
        filters_pass += 1
        reasons.append("✅ PCR Bullish <0.7")
    elif pcr > 1.3:
        filters_pass += 1
        reasons.append("✅ PCR Bearish >1.3")
    else:
        reasons.append("❌ PCR Neutral")

    if change > 0:
        filters_pass += 1
        reasons.append("✅ Price Positive")
    elif change < 0:
        filters_pass += 1
        reasons.append("✅ Price Negative")
    else:
        reasons.append("❌ Price Flat")

    if abs(pChange) > 0.4:
        filters_pass += 1
        reasons.append("✅ Momentum Strong")
    else:
        reasons.append("❌ Momentum Weak")

    # Direction
    if filters_pass >= 3:
        if pcr < 0.7 and change > 0:
            direction = "🚀 STRONG BUY — CE खरीदो"
        elif pcr > 1.3 and change < 0:
            direction = "💀 STRONG SELL — PE खरीदो"
        else:
            direction = "⚠️ MIXED — Wait for clarity"
    else:
        direction = "⏸️ WAIT — No trade zone"

    lines = [
        f"📈 NIFTY: {price}  ({'+' if change>0 else ''}{change} | {pChange}%)",
        f"📊 PCR: {pcr}",
        "",
        "🔍 Filters:",
    ] + reasons + [
        "",
        f"✅ Passed: {filters_pass}/3",
        f"🎯 Signal: {direction}"
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════
# 📌 COMMANDS
# ═══════════════════════════════════════════

# ─── /start ───────────────────────────────
@bot.message_handler(commands=['start', 'help'])
def start_command(m):
    msg = """🤖 NIFTY Trading Bot — Command List

📊 Market Data:
/nifty     → NIFTY 50 live price
/pcr       → Put-Call Ratio + signal
/top5      → Top 5 CE/PE OI strikes
/signal    → Full multi-filter signal

🛢️ MCX:
/mcx       → Crude Oil + Natural Gas

📦 Combined:
/status    → NIFTY + Crude एक साथ

🔔 Alerts:
/alert ON  → Auto alert (PCR extreme)
/alert OFF → Alert बंद करो

📅 Schedule:
/schedule ON  → 9:20 AM + 12 PM auto signal
/schedule OFF → Schedule बंद

💡 Tip: /signal सबसे powerful है।"""
    bot.reply_to(m, msg)


# ─── /nifty ───────────────────────────────
@bot.message_handler(commands=['nifty'])
def nifty_command(m):
    price, change, pChange = get_nifty_data()
    if not price:
        bot.reply_to(m, "🔴 NIFTY data नहीं मिला\nNSE block या Market बंद है")
        return
    trend = "🟢 UP" if change > 0 else ("🔴 DOWN" if change < 0 else "⚪ FLAT")
    msg = f"""📈 NIFTY 50 Live

💰 Price : {price}
📊 Change: {'+' if change>0 else ''}{change} ({pChange}%)
📉 Trend : {trend}"""
    bot.reply_to(m, msg)


# ─── /pcr ─────────────────────────────────
@bot.message_handler(commands=['pcr'])
def pcr_command(m):
    pcr, total_pe, total_ce = get_pcr_data()
    if not pcr:
        bot.reply_to(m, "🔴 PCR data नहीं मिला\nNSE block या Market बंद है")
        return

    if pcr < 0.7:
        signal = "🚀 STRONG BUY"
        reason = "PCR < 0.7 — Call writers trapped"
        action = "CE खरीदो"
    elif pcr > 1.3:
        signal = "💀 STRONG SELL"
        reason = "PCR > 1.3 — Put writers trapped"
        action = "PE खरीदो"
    else:
        signal = "⏸️ WAIT"
        reason = "PCR 0.7–1.3 — No clear direction"
        action = "Side में रहो"

    msg = f"""📊 NIFTY PCR Live

PCR   : {pcr}
Put OI: {total_pe/100000:.1f}L
Call OI: {total_ce/100000:.1f}L

🎯 Signal : {signal}
📝 Reason : {reason}
⚡ Action : {action}

Rule: Extreme PCR पर ही 100% confirm"""
    bot.reply_to(m, msg)


# ─── /top5 ────────────────────────────────
@bot.message_handler(commands=['top5'])
def top5_command(m):
    try:
        session = get_nse_session()
        url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
        res = session.get(url, headers=NSE_HEADERS, timeout=6)
        data = res.json()

        ce_list, pe_list = [], []
        for item in data['records']['data']:
            strike = item['strikePrice']
            if 'CE' in item:
                ce_list.append([strike, item['CE']['openInterest'], item['CE']['changeinOpenInterest']])
            if 'PE' in item:
                pe_list.append([strike, item['PE']['openInterest'], item['PE']['changeinOpenInterest']])

        top5_ce = sorted(ce_list, key=lambda x: x[1], reverse=True)[:5]
        top5_pe = sorted(pe_list, key=lambda x: x[1], reverse=True)[:5]

        msg  = "📊 NIFTY Top 5 OI Strikes\n\n"
        msg += "🔴 CE Top 5 — Resistance\n"
        msg += f"{'Strike':<8} {'OI':>8} {'ChgOI':>8}\n"
        msg += "─" * 26 + "\n"
        for strike, oi, chng in top5_ce:
            msg += f"{strike:<8} {oi/100000:>6.1f}L {chng/100000:>+6.1f}L\n"

        msg += "\n🟢 PE Top 5 — Support\n"
        msg += f"{'Strike':<8} {'OI':>8} {'ChgOI':>8}\n"
        msg += "─" * 26 + "\n"
        for strike, oi, chng in top5_pe:
            msg += f"{strike:<8} {oi/100000:>6.1f}L {chng/100000:>+6.1f}L\n"

        msg += "\n💡 जहाँ OI सबसे ज्यादा = बड़ी Support/Resistance\nBreakout वहीं से होगा"
    except Exception as e:
        msg = f"🔴 Top5 Failed\nNSE block या Market बंद\n{str(e)[:60]}"

    bot.reply_to(m, msg)


# ─── /signal ──────────────────────────────
@bot.message_handler(commands=['signal'])
def signal_command(m):
    price, change, pChange = get_nifty_data()
    pcr, _, _ = get_pcr_data()

    if not price or not pcr:
        bot.reply_to(m, "🔴 Data Failed\nNSE block या Market बंद")
        return

    header = "🎯 NIFTY Signal Report\n" + "─"*28 + "\n"
    body   = generate_signal_text(price, change, pChange, pcr)
    bot.reply_to(m, header + body)


# ─── /mcx ─────────────────────────────────
@bot.message_handler(commands=['mcx'])
def mcx_command(m):
    args = m.text.split()
    data = get_mcx_data()

    if not data:
        bot.reply_to(m, "🔴 MCX data नहीं मिला\nNetwork issue या Market बंद")
        return

    # Filter by arg if given
    if len(args) > 1:
        arg = args[1].upper()
        if arg in ('CRUDE', 'OIL'):
            msg = f"""🛢️ MCX Crude Oil

💵 WTI (USD) : ${data['crude_usd']:.2f}/bbl
💰 MCX (INR) : ₹{data['crude_inr']:.0f}/bbl (approx)
💱 USD/INR   : {data['usd_inr']}

📡 Source: Yahoo Finance (WTI Futures)"""
        elif arg in ('GAS', 'NG', 'NATURAL'):
            msg = f"""⛽ MCX Natural Gas

💵 NG (USD)  : ${data['gas_usd']:.3f}/mmBtu
💰 MCX (INR) : ₹{data['gas_inr']:.0f} (approx)
💱 USD/INR   : {data['usd_inr']}

📡 Source: Yahoo Finance (NG Futures)"""
        else:
            msg = "❓ Unknown: /mcx CRUDE या /mcx GAS लिखो"
    else:
        msg = f"""🏭 MCX Live Prices

🛢️ Crude Oil
   WTI     : ${data['crude_usd']:.2f}/bbl
   MCX~    : ₹{data['crude_inr']:.0f}/bbl

⛽ Natural Gas
   NG      : ${data['gas_usd']:.3f}/mmBtu
   MCX~    : ₹{data['gas_inr']:.0f}

💱 USD/INR  : {data['usd_inr']}

📡 Source: Yahoo Finance
⚠️ MCX prices approximate (conversion based)"""

    bot.reply_to(m, msg)


# ─── /status ──────────────────────────────
@bot.message_handler(commands=['status'])
def status_command(m):
    price, change, pChange = get_nifty_data()
    pcr, _, _ = get_pcr_data()
    mcx = get_mcx_data()

    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist).strftime("%d %b %Y  %I:%M %p IST")

    msg = f"📊 Market Status — {now}\n" + "═"*32 + "\n\n"

    # NIFTY
    if price:
        trend = "🟢" if change > 0 else ("🔴" if change < 0 else "⚪")
        msg += f"📈 NIFTY 50\n"
        msg += f"   Price : {price}\n"
        msg += f"   Change: {'+' if change>0 else ''}{change} ({pChange}%) {trend}\n"
        msg += f"   PCR   : {pcr if pcr else 'N/A'}\n\n"
    else:
        msg += "📈 NIFTY: 🔴 Unavailable\n\n"

    # MCX
    if mcx:
        msg += f"🛢️ MCX Crude  : ₹{mcx['crude_inr']:.0f} (~${mcx['crude_usd']:.2f})\n"
        msg += f"⛽ MCX Gas    : ₹{mcx['gas_inr']:.0f} (~${mcx['gas_usd']:.3f})\n"
        msg += f"💱 USD/INR   : {mcx['usd_inr']}\n"
    else:
        msg += "🛢️ MCX: 🔴 Unavailable\n"

    bot.reply_to(m, msg)


# ─── /alert ───────────────────────────────
def alert_worker(chat_id):
    while chat_id in alerts and alerts[chat_id] == 'ON':
        try:
            pcr, _, _ = get_pcr_data()
            price, change, _ = get_nifty_data()
            if pcr and price:
                if pcr < 0.65:
                    bot.send_message(chat_id,
                        f"🚨 ALERT: PCR Extreme Bullish!\n📊 PCR: {pcr} < 0.65\n🚀 STRONG BUY Zone — CE चेक करो\n💰 NIFTY: {price}")
                    time.sleep(300)
                elif pcr > 1.35:
                    bot.send_message(chat_id,
                        f"🚨 ALERT: PCR Extreme Bearish!\n📊 PCR: {pcr} > 1.35\n💀 STRONG SELL Zone — PE चेक करो\n💰 NIFTY: {price}")
                    time.sleep(300)
        except:
            pass
        time.sleep(120)

@bot.message_handler(commands=['alert'])
def alert_command(m):
    chat_id = m.chat.id
    args = m.text.split()

    if len(args) == 1:
        status = alerts.get(chat_id, 'OFF')
        bot.reply_to(m, f"🔔 Alert Status: {status}\nचालू: /alert ON\nबंद: /alert OFF")
        return

    action = args[1].upper()
    if action == 'ON':
        if alerts.get(chat_id) == 'ON':
            bot.reply_to(m, "✅ Alert पहले से ON है")
            return
        alerts[chat_id] = 'ON'
        threading.Thread(target=alert_worker, args=(chat_id,), daemon=True).start()
        bot.reply_to(m, """🔔 Alert ON!

⚡ PCR < 0.65 या > 1.35 होते ही message आएगा
⏰ Check: हर 2 मिनट
🔕 बंद करना हो: /alert OFF""")

    elif action == 'OFF':
        alerts[chat_id] = 'OFF'
        alerts.pop(chat_id, None)
        bot.reply_to(m, "🔕 Alert OFF कर दिया")
    else:
        bot.reply_to(m, "गलत command\n/alert ON  या  /alert OFF लिखो")


# ─── /schedule ────────────────────────────
def schedule_worker(chat_id):
    ist = pytz.timezone('Asia/Kolkata')
    sent_times = set()

    while chat_id in schedule_status and schedule_status[chat_id] == 'ON':
        try:
            now = datetime.datetime.now(ist)
            t   = now.strftime("%H:%M")
            key = f"{now.date()}_{t}"

            if now.weekday() < 5 and key not in sent_times:
                if t == "09:20":
                    price, change, pChange = get_nifty_data()
                    pcr, _, _ = get_pcr_data()
                    if price and pcr:
                        msg = "🌅 Market Open Signal — 9:20 AM\n" + "─"*28 + "\n"
                        msg += generate_signal_text(price, change, pChange, pcr)
                        bot.send_message(chat_id, msg)
                        sent_times.add(key)
                elif t == "12:00":
                    price, change, pChange = get_nifty_data()
                    pcr, _, _ = get_pcr_data()
                    if price and pcr:
                        msg = "☀️ Mid Session Signal — 12:00 PM\n" + "─"*28 + "\n"
                        msg += generate_signal_text(price, change, pChange, pcr)
                        bot.send_message(chat_id, msg)
                        sent_times.add(key)
        except:
            pass
        time.sleep(30)

@bot.message_handler(commands=['schedule'])
def schedule_command(m):
    chat_id = m.chat.id
    args = m.text.split()

    if len(args) == 1:
        status = schedule_status.get(chat_id, 'OFF')
        bot.reply_to(m, f"📅 Schedule Status: {status}\nचालू: /schedule ON\nबंद: /schedule OFF")
        return

    action = args[1].upper()
    if action == 'ON':
        if schedule_status.get(chat_id) == 'ON':
            bot.reply_to(m, "✅ Schedule पहले से ON है")
            return
        schedule_status[chat_id] = 'ON'
        threading.Thread(target=schedule_worker, args=(chat_id,), daemon=True).start()
        bot.reply_to(m, """📅 Schedule ON!

⏰ Auto Signal Times:
   🌅 9:20 AM  — Market Open
   ☀️ 12:00 PM — Mid Session

दोनों time bot खुद signal भेजेगा
बंद करना हो: /schedule OFF""")

    elif action == 'OFF':
        schedule_status[chat_id] = 'OFF'
        schedule_status.pop(chat_id, None)
        bot.reply_to(m, "📅 Schedule OFF कर दिया")
    else:
        bot.reply_to(m, "गलत command\n/schedule ON  या  /schedule OFF लिखो")


# ═══════════════════════════════════════════
# 🚀 RUN
# ═══════════════════════════════════════════
if __name__ == "__main__":
    print("✅ Bot चालू हो गया...")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)
