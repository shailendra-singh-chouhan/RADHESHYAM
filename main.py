import os
import yfinance as yf
import pandas as pd
import numpy as np
from flask import Flask, render_template_string, request
import time

# SmartAPI optional for MCX live
try:
    from SmartApi import SmartConnect
    SMARTAPI_AVAILABLE = True
except ImportError:
    SMARTAPI_AVAILABLE = False

app = Flask(__name__)

def get_ticker_data(symbol="^NSEI", max_retries=3):
    """Robust MCX/NSE fetch."""
    for attempt in range(max_retries):
        try:
            if SMARTAPI_AVAILABLE and ("MCX" in symbol.upper() or symbol.endswith("=F")):
                try:
                    # TODO: Configure env vars in Render for API_KEY, CLIENT_CODE, etc.
                    # obj = SmartConnect(api_key=os.getenv("SMARTAPI_KEY"))
                    # ... implement login + market data for true live MCX futures
                    pass
                except Exception as api_e:
                    print(f"SmartAPI fallback: {api_e}")

            ticker = yf.Ticker(symbol)
            df = ticker.history(period="1d", interval="1m")
            if df.empty:
                df = ticker.history(period="5d", interval="5m")
                if df.empty:
                    raise ValueError("Empty DataFrame")

            df = df.dropna(subset=['Close', 'High', 'Low', 'Volume'])
            if len(df) < 5:
                raise ValueError("Insufficient data")

            last_price = float(df['Close'].iloc[-1])
            typical_price = (df['High'] + df['Low'] + df['Close']) / 3
            total_volume = float(df['Volume'].sum() or 1)
            vwap = float((df['Volume'] * typical_price).sum() / total_volume)

            high_day = float(df['High'].max())
            low_day = float(df['Low'].min())
            pivot = (high_day + low_day + last_price) / 3
            r1 = (2 * pivot) - low_day
            s1 = (2 * pivot) - high_day
            r2 = pivot + (high_day - low_day)
            s2 = pivot - (high_day - low_day)

            ema9 = float(df['Close'].ewm(span=9, adjust=False).mean().iloc[-1])
            ema21 = float(df['Close'].ewm(span=21, adjust=False).mean().iloc[-1])

            delta = df['Close'].diff()
            gain = delta.where(delta > 0, 0).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain.iloc[-1] / loss.iloc[-1] if not loss.empty and loss.iloc[-1] != 0 else 1.0
            rsi = 100 - (100 / (1 + rs)) if not np.isnan(rs) else 50.0

            return {
                "symbol": symbol,
                "price": round(last_price, 2),
                "vwap": round(vwap, 2),
                "ema9": round(ema9, 2),
                "ema21": round(ema21, 2),
                "rsi": round(rsi, 2),
                "r1": round(r1, 2), "r2": round(r2, 2),
                "s1": round(s1, 2), "s2": round(s2, 2)
            }
        except Exception as e:
            print(f"Attempt {attempt+1}/{max_retries} failed for {symbol}: {e}")
            time.sleep(1.5)
    return None

def get_strategy_and_sniper(data):
    if not data:
        return "❌ DATA ERROR", "gray", "NO SHOT", "gray", "", []
    p, v, rsi = data['price'], data['vwap'], data['rsi']
    e9, e21 = data['ema9'], data['ema21']
    ema_trend = "Bullish Crossover" if e9 > e21 + 0.5 else "Bearish Crossover" if e9 < e21 - 0.5 else "Sideways/Squeeze"
    checklist = [
        {"name": f"Price vs VWAP ({'Above' if p > v else 'Below'})", "status": "✅" if p > v else "❌"},
        {"name": f"EMA Crossover ({'9 > 21' if e9 > e21 else '9 < 21'})", "status": "✅" if e9 > e21 else "❌"},
        {"name": f"RSI Momentum Zone ({rsi})", "status": "✅" if (55 <= rsi <= 65 if p > v else 35 <= rsi <= 45) else "❌"}
    ]
    if p > (v + 2) and e9 > e21 and rsi < 65:
        trend, color = "🟢 BULLISH TREND", "green"
        sniper, s_color = ("🚀 SNIPER LONG ACTIVE", "green") if rsi > 55 else ("⏳ WAITING MOMENTUM", "orange")
    elif p < (v - 2) and e9 < e21 and rsi > 35:
        trend, color = "🔴 BEARISH TREND", "red"
        sniper, s_color = ("💥 SNIPER SHORT ACTIVE", "red") if rsi < 45 else ("⏳ WAITING MOMENTUM", "orange")
    else:
        trend, color = "🟡 CONSOLIDATION ZONE", "orange"
        sniper, s_color = "😴 NO SHOT (STRICT AVOID)", "gray"
    return trend, color, sniper, s_color, ema_trend, checklist

@app.route('/', methods=['GET', 'POST'])
def index():
    user_asset = request.form.get('asset', '^NSEI').strip().upper() or '^NSEI'
    data = get_ticker_data(user_asset)
    if not data:
        data = get_ticker_data('^NSEI')
        error_msg = f"⚠️ Could not load '{user_asset}'. Reset to Nifty."
        user_asset = '^NSEI'
    else:
        error_msg = ""
    state, color, sniper, sniper_color, ema_trend, checklist = get_strategy_and_sniper(data)
    atm = round(data['price'] / 50) * 50 if data['price'] < 50000 else round(data['price'] / 100) * 100
    step = 50 if data['price'] < 50000 else 100
    strikes = [atm + (2*step), atm + step, atm, atm - step, atm - (2*step)]
    checklist_html = "".join([f'<div style="display:flex; justify-content:space-between; font-size:12px; margin:4px 0; background:#1e293b; padding:5px 8px; border-radius:4px;"><span style="color:#cbd5e1;">{item["name"]}</span><span>{item["status"]}</span></div>' for item in checklist])
    matrix_rows = ""
    for s in strikes:
        is_atm = "bg-atm" if s == atm else ""
        ce_status, ce_style = ("🔥 ITM", "color: #10b981;") if s < atm else ("⚡ ATM", "color: #38bdf8;") if s == atm else ("⏳ OTM", "color: #94a3b8;")
        pe_status, pe_style = ("🔥 ITM", "color: #ef4444;") if s > atm else ("⚡ ATM", "color: #38bdf8;") if s == atm else ("⏳ OTM", "color: #94a3b8;")
        matrix_rows += f'<tr class="{is_atm}"><td style="{ce_style} text-align:left;">{ce_status}</td><td><b>{s}</b></td><td style="{pe_style} text-align:right;">{pe_status}</td></tr>'
    html = f"""
    <!DOCTYPE html>
    <html><head><title>GOAT PRO | QUANT TERMINAL</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="30">
    <style>/* Full styles from previous optimized version */ body{{background:#0b0f19;color:#e2e8f0;font-family:'Segoe UI',sans-serif;}} /* ... (include complete CSS from prior response) */ </style>
    </head><body>
    <div class="container">
    <!-- Full UI from previous optimized version with variables inserted -->
    <!-- ... copy complete HTML structure including cards, matrix, etc. -->
    </div></body></html>
    """
    return render_template_string(html)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
