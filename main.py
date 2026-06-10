import os
import yfinance as yf
import pandas as pd
from flask import Flask, render_template_string, jsonify
from datetime import datetime

app = Flask(__name__)

# --- CONFIGURATION (Ensure these are in Render Env Vars) ---
# SMART_API_KEY, SMART_CLIENT_ID, SMART_PWD, SMART_TOKEN (TOTP)

def get_nifty_data():
    try:
        ticker = yf.Ticker("^NSEI")
        df = ticker.history(period="1d", interval="1m")
        if df.empty: return None
        
        # Calculate Indicators
        last_price = df['Close'].iloc[-1]
        vwap = (df['Volume'] * (df['High'] + df['Low'] + df['Close']) / 3).sum() / df['Volume'].sum()
        ema9 = df['Close'].ewm(span=9, adjust=False).mean().iloc[-1]
        ema21 = df['Close'].ewm(span=21, adjust=False).mean().iloc[-1]
        rsi = 100 - (100 / (1 + df['Close'].diff().clip(lower=0).mean() / df['Close'].diff().clip(upper=0).abs().mean()))
        
        return {
            "price": round(last_price, 2),
            "vwap": round(vwap, 2),
            "ema9": round(ema9, 2),
            "ema21": round(ema21, 2),
            "rsi": round(rsi, 2)
        }
    except Exception:
        return None

def get_strategy(data):
    if not data: return "😴 NO DATA", "gray"
    
    p = data['price']
    v = data['vwap']
    
    # 5-State Logic Engine
    if p > (v + 5) and data['ema9'] > data['ema21'] and data['rsi'] < 65:
        return "🟢 STRONG BULLISH (LONG)", "green"
    elif p < (v - 5) and data['ema9'] < data['ema21'] and data['rsi'] > 35:
        return "🔴 STRONG BEARISH (SHORT)", "red"
    elif abs(p - v) <= 5:
        return "🟡 VWAP ZONE (WAIT)", "orange"
    elif data['rsi'] >= 65:
        return "⚠️ OVERSOLD - WAIT PULLBACK", "orange"
    else:
        return "😴 SIDEWAYS / CONSOLIDATION", "gray"

@app.route('/')
def index():
    data = get_nifty_data()
    state, color = get_strategy(data)
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>GOAT PRO | Command Center</title>
        <style>
            body {{ background: #0f172a; color: white; font-family: sans-serif; padding: 20px; }}
            .card {{ background: #1e293b; padding: 20px; border-radius: 15px; border: 1px solid #334155; }}
            .btn {{ padding: 10px 20px; border-radius: 8px; font-weight: bold; display: inline-block; margin-top: 10px; }}
            .green {{ background: #16a34a; }} .red {{ background: #dc2626; }}
            .orange {{ background: #d97706; }} .gray {{ background: #475569; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>⚡ GOAT PRO — SYSTEM STATUS</h1>
            <div class="btn {color}">{state}</div>
            <p>Nifty Spot: ₹{data['price'] if data else 'N/A'}</p>
            <p>VWAP: ₹{data['vwap'] if data else 'N/A'}</p>
            <p>RSI: {data['rsi'] if data else 'N/A'}</p>
            <small>Updated: {datetime.now().strftime('%H:%M:%S')}</small>
        </div>
    </body>
    </html>
    """
    return render_template_string(html)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
