import os
import yfinance as yf
import pandas as pd
from flask import Flask, render_template_string, jsonify
from datetime import datetime

app = Flask(__name__)

# --- 🧠 CORE ENGINE LOGIC ---
def calculate_state(price, vwap, rsi, pcr, ema9, ema21):
    # Buffer zones to reduce noise
    is_above_vwap = price > (vwap * 1.0005) # 0.05% buffer
    is_below_vwap = price < (vwap * 0.9995)
    
    # State Machine
    if rsi < 30:
        return "🔴 OVERSOLD - NO SHORT", "orange"
    elif rsi > 70:
        return "🟢 OVERBOUGHT - NO LONG", "orange"
    
    if is_above_vwap and ema9 > ema21 and pcr > 0.8:
        return "🚀 BULLISH CONFLUENCE", "green"
    elif is_below_vwap and ema9 < ema21 and pcr < 0.8:
        return "📉 BEARISH CONFLUENCE", "red"
    else:
        return "😴 SIDEWAYS / WAIT", "gray"

@app.route('/')
def index():
    # Real-time simulation of data fetching
    # In production, swap with your real API logic
    ticker = yf.Ticker("^NSEI")
    data = ticker.history(period="1d", interval="1m")
    last_price = data['Close'].iloc[-1]
    
    # Logic Mockup for demonstration
    state, color = calculate_state(last_price, 23300, 45, 0.75, 23310, 23305)
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <style>
            body {{ background: #0f172a; color: white; font-family: 'Segoe UI', sans-serif; }}
            .card {{ background: #1e293b; padding: 20px; border-radius: 12px; margin: 10px; border: 1px solid #334155; }}
            .status-btn {{ padding: 10px 20px; border-radius: 8px; font-weight: bold; display: inline-block; }}
            .green {{ background: #16a34a; }}
            .red {{ background: #dc2626; }}
            .orange {{ background: #d97706; }}
            .gray {{ background: #475569; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>⚡ GOAT PRO — SYSTEM STATUS</h1>
            <div class="status-btn {color}">{state}</div>
            <p>Nifty Spot: ₹{last_price:.2f}</p>
            <p>Updated: {datetime.now().strftime('%H:%M:%S')}</p>
        </div>
    </body>
    </html>
    """
    return render_template_string(html)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
