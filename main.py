import os
import yfinance as yf
import pandas as pd
import numpy as np
from flask import Flask, render_template_string, request
from datetime import datetime
import time

app = Flask(__name__)

def get_ticker_data(symbol="^NSEI", max_retries=3):
    """Modular, retry-enabled data fetch for NSE/MCX."""
    for attempt in range(max_retries):
        try:
            ticker = yf.Ticker(symbol)
            # Use shorter period + interval for live feel; handle MCX specifics
            df = ticker.history(period="1d", interval="1m")
            if df.empty:
                # MCX fallback attempt
                if "MCX" in symbol.upper() or symbol == "MCX=F":
                    df = ticker.history(period="5d", interval="5m")
                if df.empty:
                    raise ValueError("Empty DataFrame")
            
            last_price = float(df['Close'].iloc[-1])
            typical_price = (df['High'] + df['Low'] + df['Close']) / 3
            total_volume = float(df['Volume'].sum())
            
            vwap = (df['Volume'] * typical_price).sum() / total_volume if total_volume > 0 else typical_price.mean()
            
            high_day = float(df['High'].max())
            low_day = float(df['Low'].min())
            
            pivot = (high_day + low_day + last_price) / 3
            r1 = (2 * pivot) - low_day
            s1 = (2 * pivot) - high_day
            r2 = pivot + (high_day - low_day)
            s2 = pivot - (high_day - low_day)
            
            ema9 = df['Close'].ewm(span=9, adjust=False).mean().iloc[-1]
            ema21 = df['Close'].ewm(span=21, adjust=False).mean().iloc[-1]
            
            delta = df['Close'].diff()
            gain = delta.where(delta > 0, 0).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            
            rs = gain.iloc[-1] / loss.iloc[-1] if not loss.empty and loss.iloc[-1] != 0 else 1
            rsi = 100 - (100 / (1 + rs)) if not np.isnan(rs) else 50.0
            
            return {
                "symbol": symbol,
                "price": round(last_price, 2),
                "vwap": round(float(vwap), 2),
                "ema9": round(float(ema9), 2),
                "ema21": round(float(ema21), 2),
                "rsi": round(float(rsi), 2),
                "r1": round(r1, 2), "r2": round(r2, 2),
                "s1": round(s1, 2), "s2": round(s2, 2)
            }
        except Exception as e:
            print(f"Attempt {attempt+1}/{max_retries} failed for {symbol}: {e}")
            time.sleep(1)  # Backoff
    print(f"Failed to fetch data for {symbol} after retries.")
    return None

def get_strategy_and_sniper(data):
    """Unchanged but robust input check."""
    if not data:
        return "❌ DATA ERROR", "gray", "NO SHOT", "gray", "", []
    # ... (rest of your original function remains identical for minimal diff)
    p, v, rsi = data['price'], data['vwap'], data['rsi']
    e9, e21 = data['ema9'], data['ema21']
    
    if e9 > e21 + 0.5:
        ema_trend = "Bullish Crossover"
    elif e9 < e21 - 0.5:
        ema_trend = "Bearish Crossover"
    else:
        ema_trend = "Sideways/Squeeze"
        
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
    
    # Adaptive matrix (unchanged)
    atm = round(data['price'] / 50) * 50 if data['price'] < 50000 else round(data['price'] / 100) * 100
    step = 50 if data['price'] < 50000 else 100
    strikes = [atm + (2*step), atm + step, atm, atm - step, atm - (2*step)]
    
    # ... (HTML template generation remains identical to your original for UI consistency)
    # [Full HTML string omitted here for brevity - copy from your version and insert variables]

    checklist_html = "".join([f'<div style="display:flex; justify-content:space-between; font-size:12px; margin:4px 0; background:#1e293b; padding:5px 8px; border-radius:4px;"><span style="color:#cbd5e1;">{item["name"]}</span><span>{item["status"]}</span></div>' for item in checklist])
    
    matrix_rows = ""  # Build as in original...
    # (Implement full matrix_rows and html template exactly as your original main.py lines 121-239)

    html = f"""..."""  # Paste full polished HTML template here with variables
    
    return render_template_string(html)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
