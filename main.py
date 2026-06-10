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
    
    if p > (v + 2) and data['ema9'] > data['ema21'] and rsi < 65:
        trend, color = "🟢 BULLISH TREND", "green"
        if rsi > 55:
            sniper, s_color = "🚀 SNIPER LONG ACTIVE (CE)", "green"
        else:
            sniper, s_color = "⏳ WAITING MOMENTUM", "orange"
            
    elif p < (v - 2) and data['ema9'] < data['ema21'] and rsi > 35:
        trend, color = "🔴 BEARISH TREND", "red"
        if rsi < 45:
            sniper, s_color = "💥 SNIPER SHORT ACTIVE (PE)", "red"
        else:
            sniper, s_color = "⏳ WAITING MOMENTUM", "orange"
            
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
    if not data:
        return "<h1>⚠️ Waiting for Exchange Data Stream...</h1><p>Refresh in a few seconds.</p><meta http-equiv='refresh' content='5'>"
        
    state, color, sniper, sniper_color = get_strategy_and_sniper(data)
    
    atm = round(data['price'] / 50) * 50
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
            .grid {{ display: grid; grid-template-columns: 1fr; gap: 20px; margin-top: 20px; }}
            @media(min-width: 600px) {{ .grid {{ grid-template-columns: 1fr 1fr; }} }}
            .card {{ background: #141b2d; padding: 20px; border-radius: 12px; border: 1px solid #1f2a48; }}
            .btn {{ padding: 10px 20px; border-radius: 6px; font-weight: bold; display: inline-block; text-transform: uppercase; font-size: 14px; }}
            .green {{ background: #10b981; color: white; }} .red {{ background: #ef4444; color: white; }}
            .orange {{ background: #f59e0b; color: white; }} .gray {{ background: #6b7280; color: white; }}
            .metric {{ display: flex; justify-content: space-between; font-size: 16px; margin: 12px 0; border-bottom: 1px solid #1f2a48; padding-bottom: 6px; }}
            .strike-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; text-align: center; }}
            .strike-table th, .strike-table td {{ padding: 10px; border: 1px solid #1f2a48; }}
            .strike-table th {{ background: #1f2a48; color: #94a3b8; }}
            .zone-tag {{ font-size: 12px; font-weight: bold; padding: 4px 8px; border-radius: 4px; display: inline-block; }}
            .ce-zone {{ background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); }}
            .pe-zone {{ background: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.3); }}
            .bg-atm {{ background: #1e293b; font-weight: bold; color: #38bdf8; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1 style="margin-bottom: 5px; color: #f8fafc;">⚡ GOAT PRO <span style="font-size:16px; color:#38bdf8; vertical-align:middle;">QUANT HARDWARE V2.0</span></h1>
            <p style="margin: 0 0 20px 0; color: #64748b;">Premium Algorithmic Decision Support Terminal</p>
            
            <div class="card" style="border-left: 5px solid {'#10b981' if color=='green' else '#ef4444' if color=='red' else '#f59e0b'}; margin-bottom: 20px;">
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px;">
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
                    <div class="metric"><span>Nifty Spot:</span> <strong>₹{data['price']}</strong></div>
                    <div class="metric"><span>Session VWAP:</span> <strong>₹{data['vwap']}</strong></div>
                    <div class="metric"><span>RSI (14):</span> <strong style="color: {'#ef4444' if data['rsi']>65 else '#10b981' if data['rsi']<35 else '#e2e8f0'}">{data['rsi']}</strong></div>
                    <div class="metric"><span>EMA 9 / 21:</span> <strong>{data['ema9']} / {data['ema21']}</strong></div>
                </div>

                <div class="card">
                    <h3 style="margin-top:0; color:#38bdf8; border-bottom: 1px solid #1f2a48; padding-bottom:10px;">🔔 Strike Analytics Matrix</h3>
                    <table class="strike-table">
                        <thead>
                            <tr>
                                <th>CALL FOCUS</th>
                                <th>STRIKE</th>
                                <th>PUT FOCUS</th>
                            </tr>
                        </thead>
                        <tbody>
                            {"".join([f'<tr class="{"bg-atm" if s==atm else ""}"><td><span class="zone-tag ce-zone">CE TRACK</span></td><td>{s} {"(ATM)" if s==atm else ""}</td><td><span class="zone-tag pe-zone">PE TRACK</span></td></tr>' for s in strikes])}
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
