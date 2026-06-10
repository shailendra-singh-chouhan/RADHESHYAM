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
        if df.empty: return None
        
        last_price = df['Close'].iloc[-1]
        typical_price = (df['High'] + df['Low'] + df['Close']) / 3
        total_volume = df['Volume'].sum()
        
        # 🔥 FIX: Zero Volume handling for Nifty Spot Index
        if total_volume > 0:
            vwap = (df['Volume'] * typical_price).sum() / total_volume
        else:
            # Fallback to Typical Price Mean if Volume is 0 (Index data limitation)
            vwap = typical_price.mean()
        
        # Core Indicators
        ema9 = df['Close'].ewm(span=9, adjust=False).mean().iloc[-1]
        ema21 = df['Close'].ewm(span=21, adjust=False).mean().iloc[-1]
        
        # RSI with robust NaN handling
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        
        if not loss.empty and loss.iloc[-1] != 0:
            rs = gain.iloc[-1] / loss.iloc[-1]
            rsi = 100 - (100 / (1 + rs))
        else:
            rsi = 50.0 # Neutral fallback
            
        return {
            "price": round(float(last_price), 2),
            "vwap": round(float(vwap), 2),
            "ema9": round(float(ema9), 2),
            "ema21": round(float(ema21), 2),
            "rsi": round(float(rsi), 2) if not np.isnan(rsi) else 50.0
        }
    except Exception as e:
        print(f"Data Core Error: {e}")
        return None

def get_strategy(data):
    if not data: return "❌ DATA FEED ERROR", "gray"
    
    p, v = data['price'], data['vwap']
    
    # 5-State Quant Logic Engine with Buffer Zone
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
        <meta http-equiv="refresh" content="15">
        <style>
            body {{ background: #0f172a; color: white; font-family: sans-serif; padding: 20px; }}
            .card {{ background: #1e293b; padding: 20px; border-radius: 12px; border: 1px solid #334155; max-width: 500px; margin: auto; }}
            .btn {{ padding: 12px 24px; border-radius: 8px; font-weight: bold; display: inline-block; margin-bottom: 15px; text-transform: uppercase; }}
            .green {{ background: #16a34a; }} .red {{ background: #dc2626; }}
            .orange {{ background: #d97706; }} .gray {{ background: #475569; }}
            .metric {{ font-size: 18px; margin: 10px 0; border-bottom: 1px solid #334155; padding-bottom: 5px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h2>⚡ GOAT PRO — SYSTEM STATUS</h2>
            <div class="btn {color}">{state}</div>
            <div class="metric">Nifty Spot: <strong>₹{data['price'] if data else 'N/A'}</strong></div>
            <div class="metric">Session VWAP: <strong>₹{data['vwap'] if data else 'N/A'}</strong></div>
            <div class="metric">RSI (14): <strong>{data['rsi'] if data else 'N/A'}</strong></div>
            <div class="metric">EMA 9/21: <strong>{data['ema9'] if data else 'N/A'} / {data['ema21'] if data else 'N/A'}</strong></div>
            <br>
            <small style="color: #94a3b8;">Last Infrastructure Pulse: {datetime.now().strftime('%H:%M:%S')} IST</small>
        </div>
    </body>
    </html>
    """
    return render_template_string(html)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
