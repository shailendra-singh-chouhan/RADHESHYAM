"""
NIFTY + MCX Trading Signal Bot
Full Clean Version â€” Render Deploy Ready
"""

import os
import telebot
import requests
import threading
import time
import datetime
import pytz
import yfinance as yf
from flask import Flask, jsonify, render_template

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ðŸ”‘ CONFIG â€” Environment Variable à¤¸à¥‡ à¤²à¥‡à¤—à¤¾
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TOKEN_HERE")
bot = telebot.TeleBot(BOT_TOKEN)

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ðŸŒ FLASK SERVER â€” Render ke liye zaroori
# (UptimeRobot isko ping karega, bot jaaga rahega)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
app = Flask(__name__)

@app.route('/')
def home():
    return render_template("dashboard.html")

@app.route('/health')
def health():
    return "OK", 200


def get_index_quote(symbol):
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        price = round(float(info['last_price']), 2)
        previous = float(info.get('previous_close') or price)
        change = round(price - previous, 2)
        pchg = round((change / previous) * 100, 2) if previous else 0
        return price, change, pchg
    except:
        return None, None, None


def get_precious_metals_data(usd_inr):
    data = {'gold_inr': None, 'silver_inr': None}
    try:
        gold_usd = yf.Ticker('GC=F').fast_info['last_price']
        data['gold_inr'] = round(float(gold_usd) * float(usd_inr) / 10, 2) if usd_inr else None
    except:
        pass

    try:
        silver_usd = yf.Ticker('SI=F').fast_info['last_price']
        data['silver_inr'] = round(float(silver_usd) * float(usd_inr), 2) if usd_inr else None
    except:
        pass

    return data


def get_signal_summary(change, pchg, pcr):
    score = 0

    try:
        if pcr < 0.7 or pcr > 1.3:
            score += 1
        if change > 0 or change < 0:
            score += 1
        if abs(pchg) > 0.4:
            score += 1
    except:
        return {'direction': 'WAIT - No trade zone', 'score': 0}

    if score >= 3 and pcr < 0.7 and change > 0:
        direction = 'STRONG BUY - CE zone'
    elif score >= 3 and pcr > 1.3 and change < 0:
        direction = 'STRONG SELL - PE zone'
    elif score >= 3:
        direction = 'MIXED - Wait for clarity'
    else:
        direction = 'WAIT - No trade zone'

    return {'direction': direction, 'score': score}


@app.route('/api/market')
def api_market():
    nifty_price, nifty_change, nifty_pchg = get_nifty_data()
    bank_price, bank_change, bank_pchg = get_index_quote('^NSEBANK')
    vix_price, _, _ = get_index_quote('^INDIAVIX')
    pcr, _, _ = get_pcr_data()
    mcx = get_mcx_data() or {}
    metals = get_precious_metals_data(mcx.get('usd_inr'))
    mcx.update(metals)

    ist = pytz.timezone('Asia/Kolkata')
    timestamp = datetime.datetime.now(ist).strftime('%H:%M:%S')

    return jsonify({
        'nifty': {
            'price': nifty_price,
            'change': nifty_change,
            'pchg': nifty_pchg
        },
        'banknifty': {
            'price': bank_price,
            'change': bank_change,
            'pchg': bank_pchg
        },
        'pcr': pcr,
        'vix': vix_price,
        'mcx': mcx,
        'signal': get_signal_summary(nifty_change, nifty_pchg, pcr),
        'timestamp': timestamp
    })
def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

# Flask ko background thread mein chalao
threading.Thread(target=run_flask, daemon=True).start()

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ðŸ“¦ GLOBAL STATE
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
alerts = {}          # chat_id: {'status': 'ON'}
schedule_status = {} # chat_id: 'ON' / 'OFF'

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ðŸ› ï¸ HELPER: NSE Session
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
NSE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Accept': '*/*',
    'Referer': 'https://www.nseindia.com/'
}

def get_nse_session():
    session = requests.Session()
    session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=6)
    return session

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ðŸ“ˆ HELPER: NIFTY Data
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ðŸ“Š HELPER: PCR Data
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ðŸ›¢ï¸ HELPER: MCX Data (via yfinance)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def get_mcx_data():
    try:
        crude = yf.Ticker("CL=F")
        gas   = yf.Ticker("NG=F")
        usd_inr = yf.Ticker("INR=X")

        crude_usd = crude.fast_info['last_price']
        gas_usd   = gas.fast_info['last_price']
        inr_rate  = usd_inr.fast_info['last_price']

        crude_inr = round(crude_usd * inr_rate * 0.159, 2)  # per barrel â†’ per litre approx
        gas_inr   = round(gas_usd  * inr_rate * 0.036, 2)   # mmBtu â†’ approx MCX unit

        return {
            'crude_usd': crude_usd,
            'crude_inr': crude_inr,
            'gas_usd':   gas_usd,
            'gas_inr':   gas_inr,
            'usd_inr':   round(inr_rate, 2)
        }
    except:
        return None

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ðŸ§  HELPER: Signal Logic
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def generate_signal_text(price, change, pChange, pcr):
    filters_pass = 0
    reasons = []

    if pcr < 0.7:
        filters_pass += 1
        reasons.append("âœ… PCR Bullish <0.7")
    elif pcr > 1.3:
        filters_pass += 1
        reasons.append("âœ… PCR Bearish >1.3")
    else:
        reasons.append("âŒ PCR Neutral")

    if change > 0:
        filters_pass += 1
        reasons.append("âœ… Price Positive")
    elif change < 0:
        filters_pass += 1
        reasons.append("âœ… Price Negative")
    else:
        reasons.append("âŒ Price Flat")

    if abs(pChange) > 0.4:
        filters_pass += 1
        reasons.append("âœ… Momentum Strong")
    else:
        reasons.append("âŒ Momentum Weak")

    # Direction
    if filters_pass >= 3:
        if pcr < 0.7 and change > 0:
            direction = "ðŸš€ STRONG BUY â€” CE à¤–à¤°à¥€à¤¦à¥‹"
        elif pcr > 1.3 and change < 0:
            direction = "ðŸ’€ STRONG SELL â€” PE à¤–à¤°à¥€à¤¦à¥‹"
        else:
            direction = "âš ï¸ MIXED â€” Wait for clarity"
    else:
        direction = "â¸ï¸ WAIT â€” No trade zone"

    lines = [
        f"ðŸ“ˆ NIFTY: {price}  ({'+' if change>0 else ''}{change} | {pChange}%)",
        f"ðŸ“Š PCR: {pcr}",
        "",
        "ðŸ” Filters:",
    ] + reasons + [
        "",
        f"âœ… Passed: {filters_pass}/3",
        f"ðŸŽ¯ Signal: {direction}"
    ]
    return "\n".join(lines)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# ðŸ“Œ COMMANDS
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

# â”€â”€â”€ /start â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@bot.message_handler(commands=['start', 'help'])
def start_command(m):
    msg = """ðŸ¤– NIFTY Trading Bot â€” Command List

ðŸ“Š Market Data:
/nifty     â†’ NIFTY 50 live price
/pcr       â†’ Put-Call Ratio + signal
/top5      â†’ Top 5 CE/PE OI strikes
/signal    â†’ Full multi-filter signal

ðŸ›¢ï¸ MCX:
/mcx       â†’ Crude Oil + Natural Gas

ðŸ“¦ Combined:
/status    â†’ NIFTY + Crude à¤à¤• à¤¸à¤¾à¤¥

ðŸ”” Alerts:
/alert ON  â†’ Auto alert (PCR extreme)
/alert OFF â†’ Alert à¤¬à¤‚à¤¦ à¤•à¤°à¥‹

ðŸ“… Schedule:
/schedule ON  â†’ 9:20 AM + 12 PM auto signal
/schedule OFF â†’ Schedule à¤¬à¤‚à¤¦

ðŸ’¡ Tip: /signal à¤¸à¤¬à¤¸à¥‡ powerful à¤¹à¥ˆà¥¤"""
    bot.reply_to(m, msg)


# â”€â”€â”€ /nifty â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@bot.message_handler(commands=['nifty'])
def nifty_command(m):
    price, change, pChange = get_nifty_data()
    if not price:
        bot.reply_to(m, "ðŸ”´ NIFTY data à¤¨à¤¹à¥€à¤‚ à¤®à¤¿à¤²à¤¾\nNSE block à¤¯à¤¾ Market à¤¬à¤‚à¤¦ à¤¹à¥ˆ")
        return
    trend = "ðŸŸ¢ UP" if change > 0 else ("ðŸ”´ DOWN" if change < 0 else "âšª FLAT")
    msg = f"""ðŸ“ˆ NIFTY 50 Live

ðŸ’° Price : {price}
ðŸ“Š Change: {'+' if change>0 else ''}{change} ({pChange}%)
ðŸ“‰ Trend : {trend}"""
    bot.reply_to(m, msg)


# â”€â”€â”€ /pcr â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@bot.message_handler(commands=['pcr'])
def pcr_command(m):
    pcr, total_pe, total_ce = get_pcr_data()
    if not pcr:
        bot.reply_to(m, "ðŸ”´ PCR data à¤¨à¤¹à¥€à¤‚ à¤®à¤¿à¤²à¤¾\nNSE block à¤¯à¤¾ Market à¤¬à¤‚à¤¦ à¤¹à¥ˆ")
        return

    if pcr < 0.7:
        signal = "ðŸš€ STRONG BUY"
        reason = "PCR < 0.7 â€” Call writers trapped"
        action = "CE à¤–à¤°à¥€à¤¦à¥‹"
    elif pcr > 1.3:
        signal = "ðŸ’€ STRONG SELL"
        reason = "PCR > 1.3 â€” Put writers trapped"
        action = "PE à¤–à¤°à¥€à¤¦à¥‹"
    else:
        signal = "â¸ï¸ WAIT"
        reason = "PCR 0.7â€“1.3 â€” No clear direction"
        action = "Side à¤®à¥‡à¤‚ à¤°à¤¹à¥‹"

    msg = f"""ðŸ“Š NIFTY PCR Live

PCR   : {pcr}
Put OI: {total_pe/100000:.1f}L
Call OI: {total_ce/100000:.1f}L

ðŸŽ¯ Signal : {signal}
ðŸ“ Reason : {reason}
âš¡ Action : {action}

Rule: Extreme PCR à¤ªà¤° à¤¹à¥€ 100% confirm"""
    bot.reply_to(m, msg)


# â”€â”€â”€ /top5 â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

        msg  = "ðŸ“Š NIFTY Top 5 OI Strikes\n\n"
        msg += "ðŸ”´ CE Top 5 â€” Resistance\n"
        msg += f"{'Strike':<8} {'OI':>8} {'ChgOI':>8}\n"
        msg += "â”€" * 26 + "\n"
        for strike, oi, chng in top5_ce:
            msg += f"{strike:<8} {oi/100000:>6.1f}L {chng/100000:>+6.1f}L\n"

        msg += "\nðŸŸ¢ PE Top 5 â€” Support\n"
        msg += f"{'Strike':<8} {'OI':>8} {'ChgOI':>8}\n"
        msg += "â”€" * 26 + "\n"
        for strike, oi, chng in top5_pe:
            msg += f"{strike:<8} {oi/100000:>6.1f}L {chng/100000:>+6.1f}L\n"

        msg += "\nðŸ’¡ à¤œà¤¹à¤¾à¤ OI à¤¸à¤¬à¤¸à¥‡ à¤œà¥à¤¯à¤¾à¤¦à¤¾ = à¤¬à¤¡à¤¼à¥€ Support/Resistance\nBreakout à¤µà¤¹à¥€à¤‚ à¤¸à¥‡ à¤¹à¥‹à¤—à¤¾"
    except Exception as e:
        msg = f"ðŸ”´ Top5 Failed\nNSE block à¤¯à¤¾ Market à¤¬à¤‚à¤¦\n{str(e)[:60]}"

    bot.reply_to(m, msg)


# â”€â”€â”€ /signal â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@bot.message_handler(commands=['signal'])
def signal_command(m):
    price, change, pChange = get_nifty_data()
    pcr, _, _ = get_pcr_data()

    if not price or not pcr:
        bot.reply_to(m, "ðŸ”´ Data Failed\nNSE block à¤¯à¤¾ Market à¤¬à¤‚à¤¦")
        return

    header = "ðŸŽ¯ NIFTY Signal Report\n" + "â”€"*28 + "\n"
    body   = generate_signal_text(price, change, pChange, pcr)
    bot.reply_to(m, header + body)


# â”€â”€â”€ /mcx â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@bot.message_handler(commands=['mcx'])
def mcx_command(m):
    args = m.text.split()
    data = get_mcx_data()

    if not data:
        bot.reply_to(m, "ðŸ”´ MCX data à¤¨à¤¹à¥€à¤‚ à¤®à¤¿à¤²à¤¾\nNetwork issue à¤¯à¤¾ Market à¤¬à¤‚à¤¦")
        return

    # Filter by arg if given
    if len(args) > 1:
        arg = args[1].upper()
        if arg in ('CRUDE', 'OIL'):
            msg = f"""ðŸ›¢ï¸ MCX Crude Oil

ðŸ’µ WTI (USD) : ${data['crude_usd']:.2f}/bbl
ðŸ’° MCX (INR) : â‚¹{data['crude_inr']:.0f}/bbl (approx)
ðŸ’± USD/INR   : {data['usd_inr']}

ðŸ“¡ Source: Yahoo Finance (WTI Futures)"""
        elif arg in ('GAS', 'NG', 'NATURAL'):
            msg = f"""â›½ MCX Natural Gas

ðŸ’µ NG (USD)  : ${data['gas_usd']:.3f}/mmBtu
ðŸ’° MCX (INR) : â‚¹{data['gas_inr']:.0f} (approx)
ðŸ’± USD/INR   : {data['usd_inr']}

ðŸ“¡ Source: Yahoo Finance (NG Futures)"""
        else:
            msg = "â“ Unknown: /mcx CRUDE à¤¯à¤¾ /mcx GAS à¤²à¤¿à¤–à¥‹"
    else:
        msg = f"""ðŸ­ MCX Live Prices

ðŸ›¢ï¸ Crude Oil
   WTI     : ${data['crude_usd']:.2f}/bbl
   MCX~    : â‚¹{data['crude_inr']:.0f}/bbl

â›½ Natural Gas
   NG      : ${data['gas_usd']:.3f}/mmBtu
   MCX~    : â‚¹{data['gas_inr']:.0f}

ðŸ’± USD/INR  : {data['usd_inr']}

ðŸ“¡ Source: Yahoo Finance
âš ï¸ MCX prices approximate (conversion based)"""

    bot.reply_to(m, msg)


# â”€â”€â”€ /status â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@bot.message_handler(commands=['status'])
def status_command(m):
    price, change, pChange = get_nifty_data()
    pcr, _, _ = get_pcr_data()
    mcx = get_mcx_data()

    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist).strftime("%d %b %Y  %I:%M %p IST")

    msg = f"ðŸ“Š Market Status â€” {now}\n" + "â•"*32 + "\n\n"

    # NIFTY
    if price:
        trend = "ðŸŸ¢" if change > 0 else ("ðŸ”´" if change < 0 else "âšª")
        msg += f"ðŸ“ˆ NIFTY 50\n"
        msg += f"   Price : {price}\n"
        msg += f"   Change: {'+' if change>0 else ''}{change} ({pChange}%) {trend}\n"
        msg += f"   PCR   : {pcr if pcr else 'N/A'}\n\n"
    else:
        msg += "ðŸ“ˆ NIFTY: ðŸ”´ Unavailable\n\n"

    # MCX
    if mcx:
        msg += f"ðŸ›¢ï¸ MCX Crude  : â‚¹{mcx['crude_inr']:.0f} (~${mcx['crude_usd']:.2f})\n"
        msg += f"â›½ MCX Gas    : â‚¹{mcx['gas_inr']:.0f} (~${mcx['gas_usd']:.3f})\n"
        msg += f"ðŸ’± USD/INR   : {mcx['usd_inr']}\n"
    else:
        msg += "ðŸ›¢ï¸ MCX: ðŸ”´ Unavailable\n"

    bot.reply_to(m, msg)


# â”€â”€â”€ /alert â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def alert_worker(chat_id):
    while chat_id in alerts and alerts[chat_id] == 'ON':
        try:
            pcr, _, _ = get_pcr_data()
            price, change, _ = get_nifty_data()
            if pcr and price:
                if pcr < 0.65:
                    bot.send_message(chat_id,
                        f"ðŸš¨ ALERT: PCR Extreme Bullish!\nðŸ“Š PCR: {pcr} < 0.65\nðŸš€ STRONG BUY Zone â€” CE à¤šà¥‡à¤• à¤•à¤°à¥‹\nðŸ’° NIFTY: {price}")
                    time.sleep(300)
                elif pcr > 1.35:
                    bot.send_message(chat_id,
                        f"ðŸš¨ ALERT: PCR Extreme Bearish!\nðŸ“Š PCR: {pcr} > 1.35\nðŸ’€ STRONG SELL Zone â€” PE à¤šà¥‡à¤• à¤•à¤°à¥‹\nðŸ’° NIFTY: {price}")
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
        bot.reply_to(m, f"ðŸ”” Alert Status: {status}\nà¤šà¤¾à¤²à¥‚: /alert ON\nà¤¬à¤‚à¤¦: /alert OFF")
        return

    action = args[1].upper()
    if action == 'ON':
        if alerts.get(chat_id) == 'ON':
            bot.reply_to(m, "âœ… Alert à¤ªà¤¹à¤²à¥‡ à¤¸à¥‡ ON à¤¹à¥ˆ")
            return
        alerts[chat_id] = 'ON'
        threading.Thread(target=alert_worker, args=(chat_id,), daemon=True).start()
        bot.reply_to(m, """ðŸ”” Alert ON!

âš¡ PCR < 0.65 à¤¯à¤¾ > 1.35 à¤¹à¥‹à¤¤à¥‡ à¤¹à¥€ message à¤†à¤à¤—à¤¾
â° Check: à¤¹à¤° 2 à¤®à¤¿à¤¨à¤Ÿ
ðŸ”• à¤¬à¤‚à¤¦ à¤•à¤°à¤¨à¤¾ à¤¹à¥‹: /alert OFF""")

    elif action == 'OFF':
        alerts[chat_id] = 'OFF'
        alerts.pop(chat_id, None)
        bot.reply_to(m, "ðŸ”• Alert OFF à¤•à¤° à¤¦à¤¿à¤¯à¤¾")
    else:
        bot.reply_to(m, "à¤—à¤²à¤¤ command\n/alert ON  à¤¯à¤¾  /alert OFF à¤²à¤¿à¤–à¥‹")


# â”€â”€â”€ /schedule â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
                        msg = "ðŸŒ… Market Open Signal â€” 9:20 AM\n" + "â”€"*28 + "\n"
                        msg += generate_signal_text(price, change, pChange, pcr)
                        bot.send_message(chat_id, msg)
                        sent_times.add(key)
                elif t == "12:00":
                    price, change, pChange = get_nifty_data()
                    pcr, _, _ = get_pcr_data()
                    if price and pcr:
                        msg = "â˜€ï¸ Mid Session Signal â€” 12:00 PM\n" + "â”€"*28 + "\n"
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
        bot.reply_to(m, f"ðŸ“… Schedule Status: {status}\nà¤šà¤¾à¤²à¥‚: /schedule ON\nà¤¬à¤‚à¤¦: /schedule OFF")
        return

    action = args[1].upper()
    if action == 'ON':
        if schedule_status.get(chat_id) == 'ON':
            bot.reply_to(m, "âœ… Schedule à¤ªà¤¹à¤²à¥‡ à¤¸à¥‡ ON à¤¹à¥ˆ")
            return
        schedule_status[chat_id] = 'ON'
        threading.Thread(target=schedule_worker, args=(chat_id,), daemon=True).start()
        bot.reply_to(m, """ðŸ“… Schedule ON!

â° Auto Signal Times:
   ðŸŒ… 9:20 AM  â€” Market Open
   â˜€ï¸ 12:00 PM â€” Mid Session

à¤¦à¥‹à¤¨à¥‹à¤‚ time bot à¤–à¥à¤¦ signal à¤­à¥‡à¤œà¥‡à¤—à¤¾
à¤¬à¤‚à¤¦ à¤•à¤°à¤¨à¤¾ à¤¹à¥‹: /schedule OFF""")

    elif action == 'OFF':
        schedule_status[chat_id] = 'OFF'
        schedule_status.pop(chat_id, None)
        bot.reply_to(m, "ðŸ“… Schedule OFF à¤•à¤° à¤¦à¤¿à¤¯à¤¾")
    else:
        bot.reply_to(m, "à¤—à¤²à¤¤ command\n/schedule ON  à¤¯à¤¾  /schedule OFF à¤²à¤¿à¤–à¥‹")


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# ðŸš€ RUN
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
if __name__ == "__main__":
    print("âœ… Bot à¤šà¤¾à¤²à¥‚ à¤¹à¥‹ à¤—à¤¯à¤¾...")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)


