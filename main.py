import yfinance as yf
import pandas as pd
import numpy as np
from flask import Flask, render_template_string
from datetime import datetime

app = Flask(__name__)

def get_nifty_data():
    try:
        ticker = yf.Ticker("^NSEI")
        df = ticker.history(period="1d", interval="1m")
        if df.empty or 'Volume' not in df.columns: return None
        
        # Defensive Programming: Ensure volume isn't zero
        if df['Volume'].sum() == 0: return None
        
        last_price = df['Close'].iloc[-1]
        # Robust VWAP calculation
        vwap = (df['Volume'] * (df['High'] + df['Low'] + df['Close']) / 3).sum() / df['Volume'].sum()
        
        ema9 = df['Close'].ewm(span=9, adjust=False).mean().iloc[-1]
        ema21 = df['Close'].ewm(span=21, adjust=False).mean().iloc[-1]
        
        # RSI with NaN handling
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs.iloc[-1]))
        
        return {
            "price": round(float(last_price), 2),
            "vwap": round(float(vwap), 2),
            "ema9": round(float(ema9), 2),
            "ema21": round(float(ema21), 2),
            "rsi": round(float(rsi), 2) if not np.isnan(rsi) else 50.0
        }
    except Exception as e:
        print(f"Data Error: {e}")
        return None

def get_strategy(data):
    if not data: return "❌ DATA FEED ERROR", "gray"
    
    p, v = data['price'], data['vwap']
    
    # 5-State Logic with VWAP Buffer
    if p > (v + 2) and data['ema9'] > data['ema21'] and data['rsi'] < 65:
        return "🟢 BULLISH TREND", "green"
    elif p < (v - 2) and data['ema9'] < data['ema21'] and data['rsi'] > 35:
        return "🔴 BEARISH TREND", "red"
    elif abs(p - v) <= 2:
        return "🟡 VWAP ZONE (NEUTRAL)", "orange"
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
        <title>GOAT PRO | SYSTEM STATUS</title>
        <meta http-equiv="refresh" content="30">
        <style>
            body {{ background: #0f172a; color: white; font-family: sans-serif; padding: 20px; }}
            .card {{ background: #1e293b; padding: 20px; border-radius: 12px; border: 1px solid #334155; }}
            .btn {{ padding: 12px 24px; border-radius: 8px; font-weight: bold; display: inline-block; }}
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
