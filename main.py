import os
import requests
from flask import Flask, render_template, jsonify

app = Flask(__name__)

# 📊 1. SYSTEM CONFIGURATION & LIVE API INTEGRATION LAYER
def fetch_live_market_data():
    """
    Fetches real-time option chain data and index spot levels.
    """
    try:
        # Simulated structure mimicking live exchange payloads from Groww
        # In production, replace this dict with your actual live API request response
        live_payload = {
            "spot_price": 23245.15,
            "total_put_oi": 2090491,
            "total_call_oi": 3236796,
            "day_high": 23266.85,
            "day_low": 23071.5,
            "vwap": 23226.20,
            "rsi": 61.2
        }
        return live_payload
    except Exception as e:
        print(f"Data Sync Error: {e}")
        return None

# 🧠 2. ALGORITHMIC ENGINE (RECONCILIATION & STRATEGY FILTER)
def process_goat_pro_intelligence(data):
    if not data:
        return {}

    spot = data["spot_price"]
    vwap = data["vwap"]
    rsi = data["rsi"]
    
    # Precise PCR calculation to remove hardcoded 1.24 illusion
    pcr = round(data["total_put_oi"] / data["total_call_oi"], 2) if data["total_call_oi"] > 0 else 1.0
    
    # Dynamic Volatility Level: Transforming "Jadui Spot" from static layout text to an active pivot
    range_median = (data["day_high"] + data["day_low"]) / 2
    jadui_spot_trigger = round((range_median + vwap) / 2, 2)

    # RSI State Categorization
    if rsi >= 70:
        rsi_status = "OVERBOUGHT / CAUTIOUS"
        rsi_color = "🔴"
    elif rsi <= 30:
        rsi_status = "OVERSOLD / ACCUMULATION"
        rsi_color = "🟢"
    else:
        rsi_status = "STABLE MOMENTUM"
        rsi_color = "🟡"

    # Strict Rule Engine: Reconciling Trend Alignment with Institutional Flows
    if spot < vwap or pcr < 0.75:
        trend = "📉 BEARISH DISTRIBUTION"
        scalp_action = f"🚀 SCALP ACTIVE: Buy Nifty ATM PE below {round(spot - 5, 1)} | SL: 20 pts | Target: +35 pts"
        intraday_prompt = "⚠️ INTRADAY SHORT: Price action tracking below VWAP. Lock out Call entries."
    elif spot > vwap and pcr >= 0.85:
        trend = "🚀 BULLISH BREAKOUT"
        scalp_action = f"🚀 SCALP ACTIVE: Buy Nifty ATM CE above {round(jadui_spot_trigger, 1)} | SL: 20 pts | Target: +35 pts"
        intraday_prompt = "🔥 INTRADAY LONG: Momentum is strong towards Day High. Ride with trailing SL!"
    else:
        trend = "〰️ SIDEWAYS CONSOLIDATION"
        scalp_action = "🚫 NO TRADING ZONE: Premium Decay Active"
        intraday_prompt = "😴 MID-SESSION SQUEEZE: Institutional volumes are range-bound."

    return {
        "spot": spot,
        "pcr": pcr,
        "pcr_color": "🟢" if pcr >= 1.0 else "🔴",
        "vwap": vwap,
        "jadui_spot": jadui_spot_trigger,
        "rsi": rsi,
        "rsi_status": rsi_status,
        "rsi_color": rsi_color,
        "trend": trend,
        "scalp_action": scalp_action,
        "intraday_prompt": intraday_prompt,
        "day_high": data["day_high"],
        "day_low": data["day_low"]
    }

# 🌐 3. ROUTING & CONTROLLER LAYER FOR RENDER
@app.route('/')
def index():
    raw_data = fetch_live_market_data()
    processed_metrics = process_goat_pro_intelligence(raw_data)
    return render_template('index.html', m=processed_metrics)

@app.route('/api/refresh', methods=['GET'])
def api_refresh():
    raw_data = fetch_live_market_data()
    processed_metrics = process_goat_pro_intelligence(raw_data)
    return jsonify(processed_metrics)

if __name__ == '__main__':
    # Configured to bind correctly to Render's dynamic port environment variable
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
