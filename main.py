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
        
        if total_volume > 0:
            vwap = (df['Volume'] * typical_price).sum() / total_volume
        else:
            vwap = typical_price.mean()
        
        ema9 = df['Close'].ewm(span=9, adjust=False).mean().iloc[-1]
        ema21 = df['Close'].ewm(span=21, adjust=False).mean().iloc[-1]
        
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        
        if not loss.empty and loss.iloc[-1] != 0:
            rs = gain.iloc[-1] / loss.iloc[-1]
            rsi = 100 - (100 / (1 + rs))
        else:
            rsi = 50.0
            
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

def get_strategy_and_sniper(data):
    if not data: return "❌ DATA ERROR", "gray", "NO SHOT", "gray"
    p, v, rsi = data['price'], data['vwap'], data['rsi']
    
    # Base Trend
    if p > (v + 2) and data['ema9'] > data['ema21'] and rsi < 65:
        trend, color = "🟢 BULLISH TREND", "green"
        # Sniper condition: High momentum breakout
        sniper, s_color = "🚀 SNIPER LONG ACTIVE (BUY CE)", "green" if rsi > 55 else "⏳ WAITING MOMENTUM", "orange"
    elif p < (v - 2) and data['ema9'] < data['ema21'] and rsi > 35:
        trend, color = "🔴 BEARISH TREND", "red"
        sniper, s_color = "💥 SNIPER SHORT ACTIVE (BUY PE)", "red" if rsi < 45 else "⏳ WAITING MOMENTUM", "orange"
    elif abs(p - v) <= 2:
        trend, color = "🟡 VWAP ZONE (NEUTRAL)", "orange"
        sniper, s_color = "😴 NO SHOT (STRICT AVOID)", "gray"
    else:
        trend, color = "😴 SIDEWAYS / CONSOLIDATION", "gray"
        sniper, s_color = "😴 NO SHOT", "gray"
        
    return trend, color, sniper, s_color

@app.route('/')
def index():
    data = get_nifty_data()
    state, color, sniper, sniper_color = get_strategy_and_sniper(data)
    
    # Calculate Dynamic Options Chain Matrix based on current Spot
    atm = round(data['price'] / 50) * 50 if data else 23350
    strikes = [atm + 100, atm + 50, atm, atm - 50, atm - 100]
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>GOAT PRO | QUANT TERMINAL</title>
        <meta http-equiv="refresh" content="15">
        <style>
            body {{ background: #0b0f19; color: #e2e8f0; font-family: 'Segoe UI', sans-serif; padding: 20px; }}
            .container {{ max-width: 900px; margin: auto; }}
            .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 20px; }}
            .card {{ background: #141b2d; padding: 20px; border-radius: 12px; border: 1px solid #1f2a48; }}
            .full-card {{ background: #141b2d; padding: 20px; border-radius: 12px; border: 1px solid #1f2a48; margin-top: 20px; }}
            .btn {{ padding: 10px 20px; border-radius: 6px; font-weight: bold; display: inline-block; text-transform: uppercase; font-size: 14px; }}
            .green {{ background: #10b981; color: white; }} .red {{ background: #ef4444; color: white; }}
            .orange {{ background: #f59e0b; color: white; }} .gray {{ background: #6b7280; color: white; }}
            .metric {{ display: flex; justify-content: space-between; font-size: 16px; margin: 12px 0; border-bottom: 1px solid #1f2a48; padding-bottom: 6px; }}
            .strike-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; text-align: center; }}
            .strike-table th, .strike-table td {{ padding: 10px; border: 1px solid #1f2a48; }}
            .strike-table th {{ background: #1f2a48; color: #94a3b8; }}
            .buy-btn {{ padding: 4px 10px; border-radius: 4px; font-weight: bold; font-size: 12px; cursor: pointer; border: none; }}
            .bg-atm {{ background: #1e293b; font-weight: bold; color: #38bdf8; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1 style="margin-bottom: 5px; color: #f8fafc;">⚡ GOAT PRO <span style="font-size:16px; color:#38bdf8; vertical-align:middle;">QUANT HARDWARE V2.0</span></h1>
            <p style="margin: 0 0 20px 0; color: #64748b;">Premium Algorithmic Decision Support Terminal</p>
            
            <div class="card" style="border-left: 5px solid {'#10b981' if color=='green' else '#ef4444' if color=='red' else '#f59e0b'};">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <span style="color: #94a3b8; font-size: 14px;">SYSTEM BIAS</span>
                        <div style="font-size: 24px; font-weight: bold; margin-top: 5px; color: {'#10b981' if color=='green' else '#ef4444' if color=='red' else '#f59e0b' if color=='orange' else '#94a3b8'};">{state}</div>
                    </div>
                    <div>
                        <span style="color: #94a3b8; font-size: 14px;">🎯 SNIPER SHOT TRIGGER</span>
                        <div class="btn {sniper_color}" style="display: block; margin-top: 5px; text-align: center;">{sniper}</div>
                    </div>
                </div>
            </div>

            <div class="grid">
                <div class="card">
                    <h3 style="margin-top:0; color:#38bdf8; border-bottom: 1px solid #1f2a48; padding-bottom:10px;">📊 Spot Analytics</h3>
                    <div class="metric"><span>Nifty Spot:</span> <strong> can scale ₹{data['price'] if data else 'N/A'}</strong></div>
                    <div class="metric"><span>Session VWAP:</span> <strong>₹{data['vwap'] if data else 'N/A'}</strong></div>
                    <div class="metric"><span>RSI (14):</span> <strong style="color: {'#ef4444' if data and data['rsi']>65 else '#10b981' if data and data['rsi']<35 else '#e2e8f0'}">{data['rsi'] if data else 'N/A'}</strong></div>
                    <div class="metric"><span>EMA 9 / 21:</span> <strong>{data['ema9'] if data else 'N/A'} / {data['ema21'] if data else 'N/A'}</strong></div>
                </div>

                <div class="card" style="grid-column: span 2;">
                    <h3 style="margin-top:0; color:#38bdf8; border-bottom: 1px solid #1f2a48; padding-bottom:10px;">🔔 Live Strike Matrix (ATM ± 2)</h3>
                    <table class="strike-table">
                        <thead>
                            <tr>
                                <th>CALL BUY</th>
                                <th>STRIKE</th>
                                <th>PUT BUY</th>
                            </tr>
                        </thead>
                        <tbody>
                            {"".join([f'<tr class="{"bg-atm" if s==atm else ""}"><td><button class="buy-btn green">BUY CE</button></td><td>{s} {"(ATM)" if s==atm else "(OTM)" if s>atm else "(ITM)" if s<atm else ""}</td><td><button class="buy-btn red">BUY PE</button></td></tr>' for s in strikes])}
                        </tbody>
                    </table>
                </div>
            </div>
            
            <p style="text-align: center; margin-top: 25px; font-size: 12px; color: #475569;">
                Last Infrastructure Pulse: {datetime.now().strftime('%H:%M:%S')} IST | Auto-refresh: 15s
            </p>
        </div>
    </body>
    </html>
    """
    return render_template_string(html)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
