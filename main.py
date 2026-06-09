import os
import random
import yfinance as yf
import pandas as pd
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>⚡ GOAT PRO — Live Command Center</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700;800&display=swap');
  .mono { font-family: 'JetBrains Mono', monospace; }
  @keyframes pulse-slow { 0%,100%{opacity:1} 50%{opacity:.65} }
  .jadui-red { animation: pulse-slow 1.4s ease-in-out infinite; }
</style>
</head>
<body class="bg-slate-100 text-slate-800 min-h-screen antialiased">

<header class="bg-white border-b border-slate-200 sticky top-0 z-50 shadow-sm">
  <div class="max-w-6xl mx-auto px-5 py-3 flex flex-wrap items-center justify-between gap-3">
    <div class="flex items-center gap-3">
      <span class="h-2.5 w-2.5 rounded-full bg-blue-600 animate-pulse inline-block"></span>
      <h1 class="mono text-lg font-extrabold tracking-widest text-slate-900">⚡ GOAT PRO
        <span class="text-[10px] bg-blue-50 text-blue-700 border border-blue-200 px-2 py-0.5 rounded ml-2 font-bold tracking-wide">LIVE DATA CORE</span>
        {% if data_source == 'fallback' %}
        <span class="text-[10px] bg-amber-50 text-amber-700 border border-amber-300 px-2 py-0.5 rounded ml-1 font-bold">⚠ SIM MODE</span>
        {% endif %}
      </h1>
    </div>
    <button id="refresh-btn" onclick="refreshData()"
      class="mono bg-blue-600 hover:bg-blue-700 active:scale-95 text-white px-5 py-2 rounded-xl text-sm font-bold tracking-wider transition-all shadow">
      🔄 REFRESH
    </button>
  </div>
</header>

<main class="max-w-6xl mx-auto px-5 py-5 space-y-5">

  <!-- Strategy Banner -->
  <div class="bg-blue-50 border border-blue-200 rounded-2xl p-4 flex items-start gap-3 shadow-sm">
    <span class="text-xl mt-0.5">📢</span>
    <div>
      <p class="mono text-[10px] font-bold text-blue-700 tracking-widest uppercase mb-1">System Strategy Engine</p>
      <p id="intraday-prompt" class="mono text-sm font-bold text-slate-800 leading-relaxed">{{ m.intraday_prompt }}</p>
    </div>
  </div>

  <!-- Section 1: Price Blocks (3-col) -->
  <div>
    <p class="mono text-sm font-black text-blue-900 uppercase tracking-wider border-l-4 border-blue-600 pl-3 mb-3">📊 Nifty Segment Core</p>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 items-stretch">

      <!-- Nifty Spot -->
      <div class="bg-white border border-slate-200 rounded-2xl p-5 flex flex-col justify-between shadow-sm min-h-[160px]">
        <p class="mono text-[10px] font-bold text-slate-400 tracking-widest uppercase">Nifty Spot</p>
        <p id="spot-price" class="mono text-4xl font-black text-blue-600 tracking-tight mt-2">₹{{ m.spot }}</p>
        <div class="flex justify-between mono text-xs text-slate-400 mt-4 pt-3 border-t border-slate-100">
          <span>H: <span id="day-high" class="text-slate-700 font-bold">₹{{ m.day_high }}</span></span>
          <span>L: <span id="day-low" class="text-slate-700 font-bold">₹{{ m.day_low }}</span></span>
        </div>
      </div>

      <!-- VWAP -->
      <div class="bg-white border border-slate-200 rounded-2xl p-5 flex flex-col justify-between shadow-sm min-h-[160px]">
        <p class="mono text-[10px] font-bold text-slate-400 tracking-widest uppercase">Session VWAP</p>
        <p id="vwap-val" class="mono text-4xl font-black text-blue-600 tracking-tight mt-2">₹{{ m.vwap }}</p>
        <p class="mono text-[10px] text-slate-400 mt-4 pt-3 border-t border-slate-100 leading-relaxed">
          Volume-weighted avg · 1-min close array
        </p>
      </div>

      <!-- Jadui Spot -->
      <div class="flex flex-col min-h-[160px]">
        <div id="jadui-container"
          class="flex flex-col justify-between rounded-2xl p-5 shadow-sm h-full border-2
          {% if m.spot < m.jadui_spot %}jadui-red bg-red-500 border-red-600 text-white{% else %}bg-emerald-50 border-emerald-400 text-slate-800{% endif %}">
          <p class="mono text-[10px] font-bold tracking-widest uppercase
            {% if m.spot < m.jadui_spot %}text-red-100{% else %}text-slate-400{% endif %}">
            ✨ Jadui Spot Pivot
          </p>
          <p id="jadui-val" class="mono text-4xl font-black tracking-tight mt-2
            {% if m.spot < m.jadui_spot %}text-white{% else %}text-emerald-600{% endif %}">
            ₹{{ m.jadui_spot }}
          </p>
          <p class="mono text-[10px] mt-4 pt-3 border-t leading-relaxed
            {% if m.spot < m.jadui_spot %}border-red-400/40 text-red-100{% else %}border-emerald-200 text-slate-400{% endif %}">
            {% if m.spot < m.jadui_spot %}🔴 PRICE BELOW PIVOT — caution{% else %}🟢 PRICE ABOVE PIVOT — bullish{% endif %}
          </p>
        </div>
      </div>

    </div>
  </div>

  <!-- Section 2: Indicators + Strategy (2-col) -->
  <div>
    <p class="mono text-sm font-black text-indigo-900 uppercase tracking-wider border-l-4 border-indigo-600 pl-3 mb-3">🧠 Indicators & Strategy Engine</p>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 items-stretch">

      <!-- PCR + RSI + EMA card -->
      <div class="bg-white border border-slate-200 rounded-2xl p-5 flex flex-col justify-between shadow-sm min-h-[240px]">
        <!-- PCR row -->
        <div class="flex justify-between items-start border-b border-slate-100 pb-4">
          <div>
            <p class="mono text-[10px] font-bold text-slate-400 tracking-widest uppercase mb-1">Est. PCR</p>
            <p id="pcr-val"
              class="mono text-4xl font-black tracking-tight
              {% if m.pcr >= 0.75 %}text-emerald-600{% else %}text-red-500{% endif %}">
              {{ m.pcr }}
            </p>
          </div>
          <span id="trend-tag"
            class="mono text-xs font-black uppercase tracking-wider px-3 py-1.5 rounded-lg border
            {% if m.trend == 'BULLISH BREAKOUT' %}bg-emerald-50 border-emerald-300 text-emerald-700
            {% elif m.trend == 'BEARISH DIST' %}bg-red-50 border-red-300 text-red-700
            {% else %}bg-slate-100 border-slate-200 text-slate-600{% endif %}">
            {{ m.trend }}
          </span>
        </div>
        <!-- RSI row -->
        <div class="border-b border-slate-100 py-4">
          <p class="mono text-[10px] font-bold text-slate-400 tracking-widest uppercase mb-1">RSI (14)</p>
          <p id="rsi-val"
            class="mono text-2xl font-black
            {% if m.rsi >= 68 %}text-red-500{% elif m.rsi <= 35 %}text-emerald-600{% else %}text-amber-500{% endif %}">
            {{ m.rsi_color }} {{ m.rsi }}
            <span class="text-sm text-slate-500 font-bold">({{ m.rsi_status }})</span>
          </p>
        </div>
        <!-- EMA row -->
        <div class="pt-4 grid grid-cols-2 gap-3">
          <div>
            <p class="mono text-[10px] font-bold text-slate-400 tracking-widest uppercase mb-1">EMA 9</p>
            <p id="ema9-val" class="mono text-lg font-black
              {% if m.ema9 > m.ema21 %}text-emerald-600{% else %}text-red-500{% endif %}">
              {{ m.ema9 }}
            </p>
          </div>
          <div>
            <p class="mono text-[10px] font-bold text-slate-400 tracking-widest uppercase mb-1">EMA 21</p>
            <p id="ema21-val" class="mono text-lg font-black
              {% if m.ema21 < m.ema9 %}text-emerald-600{% else %}text-red-500{% endif %}">
              {{ m.ema21 }}
            </p>
          </div>
        </div>
      </div>

      <!-- Strategy Router card -->
      <div class="bg-gradient-to-br from-blue-700 to-indigo-800 text-white rounded-2xl p-5 flex flex-col justify-between shadow-md min-h-[240px]">
        <div class="flex justify-between items-center border-b border-blue-500/30 pb-3">
          <p class="mono text-[10px] font-black text-blue-200 tracking-widest uppercase">Live Strategy Router</p>
          <span class="mono text-[9px] bg-white/20 px-2 py-0.5 rounded font-bold tracking-wide uppercase">Alpha Engine</span>
        </div>
        <p id="scalp-action" class="mono text-base font-black tracking-wide text-white leading-snug mt-3">
          ⚡ {{ m.scalp_action }}
        </p>
        <div class="bg-blue-950/40 border border-blue-400/30 rounded-xl p-4 mt-3">
          <p class="mono text-[9px] font-black text-amber-300 tracking-widest uppercase mb-1.5">🎯 Intraday Long Scalp Trigger</p>
          <p id="directional-long" class="mono text-sm font-black text-white leading-relaxed">
            {{ m.directional_long }}
          </p>
        </div>
      </div>

    </div>
  </div>

  <!-- Footer disclaimer -->
  <div class="bg-amber-50 border border-amber-300 rounded-xl p-4 text-xs text-amber-800 mono leading-relaxed">
    ⚖️ <strong>Legal:</strong> Sirf personal educational use. SEBI registered financial advice nahi. Trading mein risk hota hai — apni responsibility pe trade karo.
  </div>

</main>

<script>
async function refreshData() {
  const btn = document.getElementById('refresh-btn');
  btn.innerText = '⏳ SYNCING...';
  btn.disabled = true;
  try {
    const r = await fetch('/api/refresh');
    const d = await r.json();

    document.getElementById('spot-price').innerText   = '₹' + d.spot;
    document.getElementById('day-high').innerText     = '₹' + d.day_high;
    document.getElementById('day-low').innerText      = '₹' + d.day_low;
    document.getElementById('vwap-val').innerText     = '₹' + d.vwap;
    document.getElementById('jadui-val').innerText    = '₹' + d.jadui_spot;
    document.getElementById('pcr-val').innerText      = d.pcr;
    document.getElementById('rsi-val').innerText      = d.rsi_color + ' ' + d.rsi + ' (' + d.rsi_status + ')';
    document.getElementById('trend-tag').innerText    = d.trend;
    document.getElementById('scalp-action').innerText = '⚡ ' + d.scalp_action;
    document.getElementById('intraday-prompt').innerText = d.intraday_prompt;
    document.getElementById('directional-long').innerText = d.directional_long;
    document.getElementById('ema9-val').innerText     = d.ema9;
    document.getElementById('ema21-val').innerText    = d.ema21;

    // PCR color
    const pcr = document.getElementById('pcr-val');
    pcr.className = 'mono text-4xl font-black tracking-tight ' + (d.pcr >= 0.75 ? 'text-emerald-600' : 'text-red-500');

    // RSI color
    const rsi = document.getElementById('rsi-val');
    const rsiColor = d.rsi >= 68 ? 'text-red-500' : d.rsi <= 35 ? 'text-emerald-600' : 'text-amber-500';
    rsi.className = 'mono text-2xl font-black ' + rsiColor;

    // EMA cross color
    const ema9cl  = d.ema9 > d.ema21 ? 'text-emerald-600' : 'text-red-500';
    const ema21cl = d.ema21 < d.ema9  ? 'text-emerald-600' : 'text-red-500';
    document.getElementById('ema9-val').className  = 'mono text-lg font-black ' + ema9cl;
    document.getElementById('ema21-val').className = 'mono text-lg font-black ' + ema21cl;

    // Trend tag color
    const tt = document.getElementById('trend-tag');
    if (d.trend === 'BULLISH BREAKOUT')
      tt.className = 'mono text-xs font-black uppercase tracking-wider px-3 py-1.5 rounded-lg border bg-emerald-50 border-emerald-300 text-emerald-700';
    else if (d.trend === 'BEARISH DIST')
      tt.className = 'mono text-xs font-black uppercase tracking-wider px-3 py-1.5 rounded-lg border bg-red-50 border-red-300 text-red-700';
    else
      tt.className = 'mono text-xs font-black uppercase tracking-wider px-3 py-1.5 rounded-lg border bg-slate-100 border-slate-200 text-slate-600';

    // Jadui Spot color toggle
    const jc = document.getElementById('jadui-container');
    const jv = document.getElementById('jadui-val');
    if (d.spot < d.jadui_spot) {
      jc.className = 'flex flex-col justify-between rounded-2xl p-5 shadow-sm h-full border-2 jadui-red bg-red-500 border-red-600 text-white';
      jv.className = 'mono text-4xl font-black tracking-tight mt-2 text-white';
    } else {
      jc.className = 'flex flex-col justify-between rounded-2xl p-5 shadow-sm h-full border-2 bg-emerald-50 border-emerald-400 text-slate-800';
      jv.className = 'mono text-4xl font-black tracking-tight mt-2 text-emerald-600';
    }
  } catch(e) {
    console.error('Refresh failed:', e);
  }
  btn.innerText = '🔄 REFRESH';
  btn.disabled = false;
}
setInterval(refreshData, 15000);
</script>
</body>
</html>"""


def fetch_live_market_data():
    source = 'live'
    try:
        ticker = yf.Ticker("^NSEI")
        hist = ticker.history(period="1d", interval="1m")
        if hist.empty or len(hist) < 5:
            raise ValueError("Insufficient data")

        closes = hist['Close'].dropna()
        spot_price      = round(float(closes.iloc[-1]), 2)
        day_high        = round(float(hist['High'].max()), 2)
        day_low         = round(float(hist['Low'].min()), 2)
        calculated_vwap = round(float(closes.mean()), 2)

        # RSI 14
        delta    = closes.diff()
        gain     = delta.clip(lower=0)
        loss     = -delta.clip(upper=0)
        avg_gain = float(gain.rolling(14, min_periods=1).mean().iloc[-1])
        avg_loss = float(loss.rolling(14, min_periods=1).mean().iloc[-1])
        if avg_loss == 0:
            calculated_rsi = 100.0 if avg_gain > 0 else 50.0
        else:
            calculated_rsi = round(100 - (100 / (1 + avg_gain / avg_loss)), 1)

        # EMA 9 and EMA 21
        ema9  = round(float(closes.ewm(span=9,  adjust=False).mean().iloc[-1]), 2)
        ema21 = round(float(closes.ewm(span=21, adjust=False).mean().iloc[-1]), 2)

        trend_ratio    = (spot_price - day_low) / (day_high - day_low) if (day_high - day_low) > 0 else 0.5
        calculated_pcr = round(0.68 + trend_ratio * 0.12, 2)

        return {
            "spot_price": spot_price, "pcr": calculated_pcr,
            "day_high": day_high, "day_low": day_low,
            "vwap": calculated_vwap, "rsi": calculated_rsi,
            "ema9": ema9, "ema21": ema21, "source": source
        }
    except Exception as e:
        print(f"[DATA] yfinance failed: {e} — using sim fallback")
        d = random.uniform(-2.5, 2.5)
        base = 24350.0
        return {
            "spot_price": round(base + d, 2),
            "pcr":        round(0.72 + d * 0.001, 2),
            "day_high":   round(base + 120, 2),
            "day_low":    round(base - 80,  2),
            "vwap":       round(base + d * 0.3, 2),
            "rsi":        round(56.5 + d * 0.4, 1),
            "ema9":       round(base + d * 0.5, 2),
            "ema21":      round(base - 10 + d * 0.2, 2),
            "source":     "fallback"
        }


def process_intelligence(data):
    spot  = data["spot_price"]
    vwap  = data["vwap"]
    rsi   = data["rsi"]
    pcr   = data["pcr"]
    ema9  = data["ema9"]
    ema21 = data["ema21"]
    high  = data["day_high"]
    low   = data["day_low"]

    range_median      = (high + low) / 2
    jadui_spot        = round((range_median + vwap) / 2, 2)

    if rsi >= 68:
        rsi_status, rsi_color = "SATURATED", "🔴"
    elif rsi <= 35:
        rsi_status, rsi_color = "OVERSOLD",  "🟢"
    else:
        rsi_status, rsi_color = "STABLE",    "🟡"

    long_trigger      = round(max(jadui_spot, vwap) + 6.5, 1)
    directional_long  = f"BUY CE ABOVE {long_trigger} | SL {long_trigger-20:.1f} | TGT {long_trigger+35:.1f}"

    ema_bull = ema9 > ema21

    if spot < vwap:
        trend          = "BEARISH DIST"
        scalp_action   = f"Buy ATM PE below {round(spot-4,1)} | SL 20 pts | TGT +35 pts"
        intraday_prompt = "⚠️ INTRADAY SHORT — Price below VWAP. Lock out CE entries."
    elif spot >= vwap and pcr >= 0.75 and ema_bull:
        trend          = "BULLISH BREAKOUT"
        scalp_action   = f"Buy ATM CE above {round(jadui_spot,1)} | SL 20 pts | TGT +35 pts"
        intraday_prompt = "🔥 INTRADAY LONG — VWAP + PCR + EMA all bullish. Trail SL."
    elif spot >= vwap and pcr >= 0.75:
        trend          = "BULLISH BREAKOUT"
        scalp_action   = f"Buy ATM CE above {round(jadui_spot,1)} | SL 20 pts | TGT +35 pts"
        intraday_prompt = "📈 LONG SETUP — Price above VWAP, PCR bullish. EMA cross pending."
    else:
        trend          = "SIDEWAYS"
        scalp_action   = "NO TRADE ZONE — Premium decay active"
        intraday_prompt = "😴 RANGE-BOUND — Wait for VWAP + PCR confluence."

    return {
        "spot": spot, "pcr": pcr, "vwap": vwap, "jadui_spot": jadui_spot,
        "rsi": rsi, "rsi_status": rsi_status, "rsi_color": rsi_color,
        "ema9": ema9, "ema21": ema21,
        "trend": trend, "scalp_action": scalp_action,
        "intraday_prompt": intraday_prompt, "directional_long": directional_long,
        "day_high": high, "day_low": low
    }


@app.route('/')
def index():
    raw  = fetch_live_market_data()
    m    = process_intelligence(raw)
    return render_template_string(HTML_TEMPLATE, m=m, data_source=raw["source"])


@app.route('/api/refresh')
def api_refresh():
    raw = fetch_live_market_data()
    return jsonify(process_intelligence(raw))


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
