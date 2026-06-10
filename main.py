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
    if not data: return "❌ DATA ERROR", "gray", "NO SHOT", "gray", "", []
    p, v, rsi = data['price'], data['vwap'], data['rsi']
    e9, e21 = data['ema9'], data['ema21']
    
    # 1. EMA Bracket Trend calculation
    if e9 > e21 + 0.5:
        ema_trend = "Bullish Crossover"
    elif e9 < e21 - 0.5:
        ema_trend = "Bearish Crossover"
    else:
        ema_trend = "Sideways/Squeeze"
        
    # 2. Checklist Verification
    checklist = [
        {"name": f"Price vs VWAP ({'Above' if p > v else 'Below'})", "status": "✅" if p > v else "❌"},
        {"name": f"EMA Crossover ({'9 > 21' if e9 > e21 else '9 < 21'})", "status": "✅" if e9 > e21 else "❌"},
        {"name": f"RSI Momentum Zone ({rsi})", "status": "✅" if 55 <= rsi <= 65 else "❌"}
    ]
    
    # Strategy evaluation
    if p > (v + 2) and e9 > e21 and rsi < 65:
        trend, color = "🟢 BULLISH TREND", "green"
        if rsi > 55:
            sniper, s_color = "🚀 SNIPER LONG ACTIVE (CE)", "green"
        else:
            sniper, s_color = "⏳ WAITING MOMENTUM", "orange"
            checklist[2]["status"] = "❌"  
            
    elif p < (v - 2) and e9 < e21 and rsi > 35:
        trend, color = "🔴 BEARISH TREND", "red"
        checklist[0]["status"] = "✅" if p < v else "❌"
        checklist[1]["status"] = "✅" if e9 < e21 else "❌"
        checklist[2]["status"] = "✅" if 35 <= rsi <= 45 else "❌"
        
        if rsi < 45:
            sniper, s_color = "💥 SNIPER SHORT ACTIVE (PE)", "red"
        else:
            sniper, s_color = "⏳ WAITING MOMENTUM", "orange"
            
    else:
        trend, color = "🟡 CONSOLIDATION ZONE", "orange"
        sniper, s_color = "😴 NO SHOT (STRICT AVOID)", "gray"
        checklist[0]["status"] = "✅" if (p > v and e9 > e21) or (p < v and e9 < e21) else "❌"
        checklist[1]["status"] = "✅" if (e9 > e21 if p > v else e9 < e21) else "❌"
        checklist[2]["status"] = "✅" if (55 <= rsi <= 65 if p > v else 35 <= rsi <= 45) else "❌"
        
    return trend, color, sniper, s_color, ema_trend, checklist

@app.route('/')
def index():
    data = get_nifty_data()
    if not data:
        return "<h1>⚠️ Waiting for Exchange Data Stream...</h1><p>Refresh in a few seconds.</p><meta http-equiv='refresh' content='5'>"
        
    state, color, sniper, sniper_color, ema_trend, checklist = get_strategy_and_sniper(data)
    
    atm = round(data['price'] / 50) * 50
    strikes = [atm + 100, atm + 50, atm, atm - 50, atm - 100]
    
    # Generate compact checklist HTML
    checklist_html = "".join([
        f'<div style="display:flex; justify-content:space-between; font-size:12px; margin:3px 0; background:#1e293b; padding:4px 8px; border-radius:4px;">'
        f'<span style="color:#cbd5e1;">{item["name"]}</span>'
        f'<span>{item["status"]}</span>'
        f'</div>' for item in checklist
    ])
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>GOAT PRO | QUANT TERMINAL</title>
        <meta http-equiv="refresh" content="15">
        <style>
            body {{ background: #0b0f19; color: #e2e8f0; font-family: 'Segoe UI', sans-serif; padding: 10px 20px; margin: 0; }}
            .container {{ max-width: 950px; margin: auto; }}
            .grid {{ display: grid; grid-template-columns: 1fr; gap: 15px; margin-top: 15px; }}
            @media(min-width: 600px) {{ .grid {{ grid-template-columns: 1fr 1fr; }} }}
            .card {{ background: #141b2d; padding: 15px; border-radius: 8px; border: 1px solid #1f2a48; }}
            .btn {{ padding: 8px 15px; border-radius: 4px; font-weight: bold; display: inline-block; text-transform: uppercase; font-size: 13px; }}
            .green {{ background: #10b981; color: white; }} .red {{ background: #ef4444; color: white; }}
            .orange {{ background: #f59e0b; color: white; }} .gray {{ background: #6b7280; color: white; }}
            .metric {{ display: flex; justify-content: space-between; font-size: 14px; margin: 8px 0; border-bottom: 1px solid #1f2a48; padding-bottom: 6px; }}
            .strike-table {{ width: 100%; border-collapse: collapse; margin-top: 5px; text-align: center; font-size: 13px; }}
            .strike-table th, .strike-table td {{ padding: 6px; border: 1px solid #1f2a48; }}
            .strike-table th {{ background: #1f2a48; color: #94a3b8; font-size: 12px; }}
            .zone-tag {{ font-size: 11px; font-weight: bold; padding: 3px 6px; border-radius: 3px; display: inline-block; }}
            .ce-zone {{ background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); }}
            .pe-zone {{ background: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.3); }}
            .bg-atm {{ background: #1e293b; font-weight: bold; color: #38bdf8; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1 style="margin: 5px 0; font-size: 20px; color: #f8fafc;">⚡ GOAT PRO <span style="font-size:14px; color:#38bdf8; vertical-align:middle;">QUANT HARDWARE V2.0</span></h1>
            <p style="margin: 0 0 10px 0; font-size: 13px; color: #64748b;">Premium Algorithmic Decision Support Terminal</p>
            
            <div class="card" style="border-left: 4px solid {'#10b981' if color=='green' else '#ef4444' if color=='red' else '#f59e0b'}; margin-bottom: 15px;">
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                    <div style="flex: 1; min-width: 250px;">
                        <span style="color: #94a3b8; font-size: 12px; text-transform: uppercase;">System Bias</span>
                        <div style="font-size: 20px; font-weight: bold; margin-top: 3px; color: {'#10b981' if color=='green' else '#ef4444' if color=='red' else '#f59e0b' if color=='orange' else '#94a3b8'};">{state}</div>
                        <div style="margin-top: 10px;">
                            <span style="color: #64748b; font-size: 11px; font-weight: bold; letter-spacing: 0.5px;">🎯 SNIPER CONFLUENCE CHECKLIST</span>
                            <div style="margin-top: 4px;">
                                {checklist_html}
                            </div>
                        </div>
                    </div>
                    <div style="text-align: right;">
                        <span style="color: #94a3b8; font-size: 12px; display: block; margin-bottom: 4px; text-transform: uppercase;">🎯 Sniper Shot Trigger</span>
                        <div class="btn {sniper_color}" style="text-align: center; min-width: 200px;">{sniper}</div>
                    </div>
                </div>
            </div>

            <div class="grid">
                <div class="card">
                    <h3 style="margin-top:0; font-size: 15px; color:#38bdf8; border-bottom: 1px solid #1f2a48; padding-bottom:8px;">📊 Spot Analytics</h3>
                    <div class="metric"><span>Nifty Spot:</span> <strong>₹{data['price']}</strong></div>
                    <div class="metric"><span>Session VWAP:</span> <strong>₹{data['vwap']}</strong></div>
                    <div class="metric"><span>RSI (14):</span> <strong style="color: {'#ef4444' if data['rsi']>65 else '#10b981' if data['rsi']<35 else '#e2e8f0'}">{data['rsi']}</strong></div>
                    <div class="metric"><span>EMA 9 / 21:</span> <strong style="color: #38bdf8;">{data['ema9']} / {data['ema21']} <span style="font-size:12px; color:#94a3b8;">({ema_trend})</span></strong></div>
                </div>

                <div class="card">
                    <h3 style="margin-top:0; font-size: 15px; color:#38bdf8; border-bottom: 1px solid #1f2a48; padding-bottom:8px;">🔔 Strike Analytics Matrix</h3>
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
            
            <p style="text-align: center; margin-top: 15px; font-size: 11px; color: #475569;">
                Last Infrastructure Pulse: {datetime.now().strftime('%H:%M:%S')} IST | Auto-refresh: 15s
            </p>
        </div>
    </body>
    </html>
    """
    return render_template_string(html)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
