import yfinance as yf
import pandas as pd
import numpy as np
from flask import Flask, render_template_string, request
from datetime import datetime

app = Flask(__name__)

def get_ticker_data(symbol="^NSEI"):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1d", interval="1m")
        if df.empty: return None
        
        last_price = df['Close'].iloc[-1]
        typical_price = (df['High'] + df['Low'] + df['Close']) / 3
        total_volume = df['Volume'].sum()
        
        if total_volume > 0:
            vwap = (df['Volume'] * typical_price).sum() / total_volume
        else:
            vwap = typical_price.mean()
        
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
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        
        if not loss.empty and loss.iloc[-1] != 0:
            rs = gain.iloc[-1] / loss.iloc[-1]
            rsi = 100 - (100 / (1 + rs))
        else:
            rsi = 50.0
            
        return {
            "symbol": symbol,
            "price": round(float(last_price), 2),
            "vwap": round(float(vwap), 2),
            "ema9": round(float(ema9), 2),
            "ema21": round(float(ema21), 2),
            "rsi": round(float(rsi), 2) if not np.isnan(rsi) else 50.0,
            "r1": round(r1, 2), "r2": round(r2, 2),
            "s1": round(s1, 2), "s2": round(s2, 2)
        }
    except Exception as e:
        print(f"Ticker Data Error for {symbol}: {e}")
        return None

def get_strategy_and_sniper(data):
    if not data: return "❌ DATA ERROR", "gray", "NO SHOT", "gray", "", []
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
    user_asset = request.form.get('asset', '^NSEI').strip().upper()
    if not user_asset:
        user_asset = '^NSEI'
        
    data = get_ticker_data(user_asset)
    
    # Anti-crash protection
    if not data:
        fallback_asset = '^NSEI'
        data = get_ticker_data(fallback_asset)
        error_msg = f"⚠️ Could not load '{user_asset}'. Reset to Nifty Spot."
        user_asset = fallback_asset
    else:
        error_msg = ""
        
    state, color, sniper, sniper_color, ema_trend, checklist = get_strategy_and_sniper(data)
    
    # Adaptive Option/Price matrix
    atm = round(data['price'] / 50) * 50 if data['price'] < 50000 else round(data['price'] / 100) * 100
    step = 50 if data['price'] < 50000 else 100
    strikes = [atm + (2*step), atm + step, atm, atm - step, atm - (2*step)]
    
    checklist_html = "".join([
        f'<div style="display:flex; justify-content:space-between; font-size:12px; margin:4px 0; background:#1e293b; padding:5px 8px; border-radius:4px;">'
        f'<span style="color:#cbd5e1;">{item["name"]}</span>'
        f'<span>{item["status"]}</span>'
        f'</div>' for item in checklist
    ])
    
    matrix_rows = ""
    for s in strikes:
        is_atm = "bg-atm" if s == atm else ""
        if s < atm: ce_status, ce_style = "🔥 ITM (High Delta)", "color: #10b981;"
        elif s == atm: ce_status, ce_style = "⚡ ATM (Max Action)", "color: #38bdf8;"
        else: ce_status, ce_style = "⏳ OTM (High Decay)", "color: #94a3b8; font-size: 10px;"
        
        if s > atm: pe_status, pe_style = "🔥 ITM (High Delta)", "color: #ef4444;"
        elif s == atm: pe_status, pe_style = "⚡ ATM (Max Action)", "color: #38bdf8;"
        else: pe_status, pe_style = "⏳ OTM (High Decay)", "color: #94a3b8; font-size: 10px;"
            
        matrix_rows += f'<tr class="{is_atm}"><td style="{ce_style} text-align: left;">{ce_status}</td><td><b>{s}</b></td><td style="{pe_style} text-align: right;">{pe_status}</td></tr>'
        
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>GOAT PRO | QUANT TERMINAL</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <style>
            body {{ background: #0b0f19; color: #e2e8f0; font-family: 'Segoe UI', sans-serif; padding: 10px; margin: 0; }}
            .container {{ max-width: 950px; margin: auto; }}
            .grid {{ display: grid; grid-template-columns: 1fr; gap: 12px; margin-top: 12px; }}
            @media(min-width: 768px) {{ .grid {{ grid-template-columns: 1fr 1fr; }} }}
            .card {{ background: #141b2d; padding: 12px; border-radius: 8px; border: 1px solid #1f2a48; }}
            .btn {{ padding: 8px 12px; border-radius: 4px; font-weight: bold; display: inline-block; text-transform: uppercase; font-size: 12px; width: 100%; box-sizing: border-box; text-align: center; border: none; }}
            .green {{ background: #10b981; color: white; }} .red {{ background: #ef4444; color: white; }}
            .orange {{ background: #f59e0b; color: white; }} .gray {{ background: #6b7280; color: white; }}
            .metric {{ display: flex; justify-content: space-between; font-size: 13px; margin: 8px 0; border-bottom: 1px solid #1f2a48; padding-bottom: 4px; }}
            .strike-table {{ width: 100%; border-collapse: collapse; margin-top: 5px; text-align: center; font-size: 12px; }}
            .strike-table th, .strike-table td {{ padding: 6px; border: 1px solid #1f2a48; }}
            .strike-table th {{ background: #1f2a48; color: #94a3b8; }}
            .bg-atm {{ background: #1e293b; font-weight: bold; color: #38bdf8; }}
            .level-box {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 5px; }}
            .level-item {{ background: #1e293b; padding: 6px; border-radius: 4px; text-align: center; font-size: 12px; }}
            input {{ background: #1e293b; border: 1px solid #1f2a48; color: white; padding: 8px; border-radius: 4px; font-size: 13px; width: 100%; box-sizing: border-box; }}
            .search-btn {{ background: #38bdf8; color: #0b0f19; font-weight: bold; padding: 8px 15px; border-radius: 4px; border: none; cursor: pointer; width: 100%; font-size: 13px; }}
            @media(min-width: 480px) {{ input {{ width: 250px; }} .search-btn {{ width: auto; }} }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1 style="margin: 5px 0; font-size: 18px; color: #f8fafc;">⚡ GOAT PRO <span style="font-size:12px; color:#38bdf8; vertical-align:middle;">UNIVERSAL QUANT TERMINAL</span></h1>
            
            <div class="card" style="margin-bottom: 12px; background: #101726; border: 1px solid #334155;">
                <form method="POST" style="display: flex; flex-wrap: wrap; gap: 10px; align-items: center;">
                    <span style="font-size: 13px; color: #94a3b8; font-weight: bold;">🔎 ANALYZE ANY STOCK / SEGMENT:</span>
                    <input type="text" name="asset" value="{user_asset}" placeholder="e.g. ^NSEI, SBIN.NS, MCX=F">
                    <button type="submit" class="search-btn">⚡ Run Deep Scan</button>
                    {f'<div style="color:#ef4444; font-size:12px; width:100%; margin-top:4px;">{error_msg}</div>' if error_msg else ''}
                    <div style="font-size: 11px; color: #64748b; width: 100%; margin-top: 2px;">
                        💡 Quick Guide: Nifty: <code>^NSEI</code> | Sensex: <code>^BSESN</code> | Crude Oil: <code>MCX=F</code> | Reliance: <code>RELIANCE.NS</code>
                    </div>
                </form>
            </div>
            
            <div class="card" style="border-left: 4px solid {'#10b981' if color=='green' else '#ef4444' if color=='red' else '#f59e0b'}; margin-bottom: 12px;">
                <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 10px;">
                    <div style="flex: 1; min-width: 250px;">
                        <span style="color: #94a3b8; font-size: 11px; text-transform: uppercase;">ACTIVE TICKER: <span style="color:#38bdf8;">{data['symbol']}</span></span>
                        <div style="font-size: 18px; font-weight: bold; margin-top: 2px; color: {'#10b981' if color=='green' else '#ef4444' if color=='red' else '#f59e0b'};">{state}</div>
                        <div style="margin-top: 8px;">
                            <span style="color: #64748b; font-size: 10px; font-weight: bold;">🎯 SNIPER CONFLUENCE CHECKLIST</span>
                            <div style="margin-top: 4px;">{checklist_html}</div>
                        </div>
                    </div>
                    <div style="width: 100%;">
                        <span style="color: #94a3b8; font-size: 11px; display: block; margin-bottom: 4px; text-transform: uppercase;">🎯 Sniper Shot Trigger</span>
                        <div class="btn {sniper_color}">{sniper}</div>
                    </div>
                </div>
            </div>

            <div class="grid">
                <div class="card">
                    <h3 style="margin-top:0; font-size: 14px; color:#38bdf8; border-bottom: 1px solid #1f2a48; padding-bottom:6px;">📊 Asset Analytics</h3>
                    <div class="metric"><span>Current Price:</span> <strong style="color: #10b981;">LIVE ₹{data['price']}</strong></div>
                    <div class="metric"><span>Session VWAP / Mean:</span> <strong>₹{data['vwap']}</strong></div>
                    <div class="metric"><span>RSI (14):</span> <strong style="color: {'#ef4444' if data['rsi']>65 else '#10b981' if data['rsi']<35 else '#e2e8f0'}">{data['rsi']}</strong></div>
                    <div class="metric"><span>EMA 9 / 21:</span> <strong style="color: #38bdf8;">{data['ema9']} / {data['ema21']} <br><span style="font-size:11px; color:#94a3b8;">({ema_trend})</span></strong></div>
                </div>

                <div class="card">
                    <h3 style="margin-top:0; font-size: 14px; color:#38bdf8; border-bottom: 1px solid #1f2a48; padding-bottom:6px;">🎯 Live Pivot Levels</h3>
                    <div class="level-box">
                        <div class="level-item" style="border-top: 2px solid #ef4444;"><span style="color:#94a3b8;font-size:10px;">RESISTANCE 2</span><br><strong>₹{data['r2']}</strong></div>
                        <div class="level-item" style="border-top: 2px solid #f59e0b;"><span style="color:#94a3b8;font-size:10px;">RESISTANCE 1</span><br><strong>₹{data['r1']}</strong></div>
                        <div class="level-item" style="border-top: 2px solid #10b981;"><span style="color:#94a3b8;font-size:10px;">SUPPORT 1</span><br><strong>₹{data['s1']}</strong></div>
                        <div class="level-item" style="border-top: 2px solid #38bdf8;"><span style="color:#94a3b8;font-size:10px;">SUPPORT 2</span><br><strong>₹{data['s2']}</strong></div>
                    </div>
                </div>

                <div class="card" style="grid-column: 1 / -1;">
                    <h3 style="margin-top:0; font-size: 14px; color:#38bdf8; border-bottom: 1px solid #1f2a48; padding-bottom:6px;">🔔 Dynamic Options/Price Matrix</h3>
                    <table class="strike-table">
                        <thead>
                            <tr>
                                <th style="width: 40%;">CALL SIDE VALUE</th>
                                <th style="width: 20%;">STRIKE/ZONE</th>
                                <th style="width: 40%;">PUT SIDE VALUE</th>
                            </tr>
                        </thead>
                        <tbody>
                            {matrix_rows}
                        </tbody>
                    </table>
                </div>
            </div>
            
            <p style="text-align: center; margin-top: 15px; font-size: 10px; color: #475569;">
                Pulse: {datetime.now().strftime('%H:%M:%S')} IST | Asset Type: Dynamic Exchange Feed
            </p>
        </div>
    </body>
    </html>
    """
    return render_template_string(html)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
