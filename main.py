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
  @keyframes pulse-slow { 0%,100%{opacity:1} 50%{opacity:.6} }
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
    <div class="flex items-center gap-3">
      <span class="mono text-[10px] text-slate-400" id="last-update">--</span>
      <button id="refresh-btn" onclick="refreshData()"
        class="mono bg-blue-600 hover:bg-blue-700 active:scale-95 text-white px-5 py-2 rounded-xl text-sm font-bold tracking-wider transition-all shadow">
        🔄 REFRESH
      </button>
    </div>
  </div>
</header>

<main class="max-w-6xl mx-auto px-5 py-5 space-y-5">

  <!-- Strategy Banner -->
  <div id="banner-box" class="rounded-2xl p-4 flex items-start gap-3 shadow-sm
    {% if m.bias == 'LONG' %}bg-emerald-50 border border-emerald-300
    {% elif m.bias == 'SHORT' %}bg-red-50 border border-red-300
    {% elif m.bias == 'WAIT_RSI' %}bg-amber-50 border border-amber-300
    {% else %}bg-blue-50 border border-blue-200{% endif %}">
    <span class="text-xl mt-0.5">📢</span>
    <div>
      <p class="mono text-[10px] font-bold tracking-widest uppercase mb-1
        {% if m.bias == 'LONG' %}text-emerald-700
        {% elif m.bias == 'SHORT' %}text-red-700
        {% elif m.bias == 'WAIT_RSI' %}text-amber-700
        {% else %}text-blue-700{% endif %}">
        System Strategy Engine
      </p>
      <p id="intraday-prompt" class="mono text-sm font-bold text-slate-800 leading-relaxed">{{ m.intraday_prompt }}</p>
    </div>
  </div>

  <!-- Section 1: Price Blocks -->
  <div>
    <p class="mono text-sm font-black text-blue-900 uppercase tracking-wider border-l-4 border-blue-600 pl-3 mb-3">📊 Nifty Segment Core</p>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 items-stretch">

      <!-- Nifty Spot -->
      <div class="bg-white border border-slate-200 rounded-2xl p-5 flex flex-col justify-between shadow-sm min-h-[165px]">
        <p class="mono text-[10px] font-bold text-slate-400 tracking-widest uppercase">Nifty Spot</p>
        <p id="spot-price" class="mono text-4xl font-black text-blue-600 tracking-tight mt-2">₹{{ m.spot }}</p>
        <div class="flex justify-between mono text-xs text-slate-400 mt-4 pt-3 border-t border-slate-100">
          <span>H: <span id="day-high" class="text-slate-700 font-bold">₹{{ m.day_high }}</span></span>
          <span>L: <span id="day-low" class="text-slate-700 font-bold">₹{{ m.day_low }}</span></span>
        </div>
      </div>

      <!-- VWAP -->
      <div class="bg-white border border-slate-200 rounded-2xl p-5 flex flex-col justify-between shadow-sm min-h-[165px]">
        <p class="mono text-[10px] font-bold text-slate-400 tracking-widest uppercase">Session VWAP</p>
        <p id="vwap-val" class="mono text-4xl font-black text-blue-600 tracking-tight mt-2">₹{{ m.vwap }}</p>
        <div class="flex items-center gap-2 mt-4 pt-3 border-t border-slate-100">
          <span id="vwap-badge" class="mono text-[10px] font-bold px-2 py-0.5 rounded
            {% if m.spot >= m.vwap %}bg-emerald-100 text-emerald-700{% else %}bg-red-100 text-red-700{% endif %}">
            {% if m.spot >= m.vwap %}PRICE ABOVE{% else %}PRICE BELOW{% endif %}
          </span>
          <span class="mono text-[10px] text-slate-400">1-min avg</span>
        </div>
      </div>

      <!-- Jadui Spot -->
      <div class="flex flex-col min-h-[165px]">
        <div id="jadui-container"
          class="flex flex-col justify-between rounded-2xl p-5 shadow-sm h-full border-2 min-h-[165px]
          {% if m.spot < m.jadui_spot %}jadui-red bg-red-500 border-red-600{% else %}bg-emerald-50 border-emerald-400{% endif %}">
          <p class="mono text-[10px] font-bold tracking-widest uppercase
            {% if m.spot < m.jadui_spot %}text-red-100{% else %}text-slate-400{% endif %}">
            ✨ Jadui Spot Pivot
          </p>
          <p id="jadui-val" class="mono text-4xl font-black tracking-tight mt-2
            {% if m.spot < m.jadui_spot %}text-white{% else %}text-emerald-600{% endif %}">
            ₹{{ m.jadui_spot }}
          </p>
          <p id="jadui-status" class="mono text-[10px] mt-4 pt-3 border-t leading-relaxed
            {% if m.spot < m.jadui_spot %}border-red-400/40 text-red-100{% else %}border-emerald-200 text-slate-500{% endif %}">
            {% if m.spot < m.jadui_spot %}🔴 Below pivot — bearish zone{% else %}🟢 Above pivot — bullish zone{% endif %}
          </p>
        </div>
      </div>

    </div>
  </div>

  <!-- Section 2: Indicators + Strategy -->
  <div>
    <p class="mono text-sm font-black text-indigo-900 uppercase tracking-wider border-l-4 border-indigo-600 pl-3 mb-3">🧠 Indicators & Strategy Engine</p>
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 items-stretch">

      <!-- Indicators card -->
      <div class="bg-white border border-slate-200 rounded-2xl p-5 flex flex-col justify-between shadow-sm min-h-[250px]">

        <!-- PCR + Trend tag -->
        <div class="flex justify-between items-start border-b border-slate-100 pb-4">
          <div>
            <p class="mono text-[10px] font-bold text-slate-400 tracking-widest uppercase mb-1">Est. PCR</p>
            <p id="pcr-val" class="mono text-4xl font-black tracking-tight
              {% if m.pcr >= 0.72 %}text-emerald-600{% else %}text-red-500{% endif %}">
              {{ m.pcr }}
            </p>
          </div>
          <span id="trend-tag" class="mono text-xs font-black uppercase tracking-wider px-3 py-1.5 rounded-lg border mt-1
            {% if m.trend == 'BULLISH' %}bg-emerald-50 border-emerald-300 text-emerald-700
            {% elif m.trend == 'BEARISH' %}bg-red-50 border-red-300 text-red-700
            {% elif m.trend == 'WAIT RSI' %}bg-amber-50 border-amber-300 text-amber-700
            {% elif m.trend == 'VWAP ZONE' %}bg-yellow-50 border-yellow-400 text-yellow-700
            {% else %}bg-slate-100 border-slate-200 text-slate-600{% endif %}">
            {{ m.trend }}
          </span>
        </div>

        <!-- RSI -->
        <div class="border-b border-slate-100 py-3">
          <p class="mono text-[10px] font-bold text-slate-400 tracking-widest uppercase mb-1">RSI (14)</p>
          <div class="flex items-center gap-3">
            <p id="rsi-val" class="mono text-2xl font-black
              {% if m.rsi >= 68 %}text-red-500{% elif m.rsi <= 35 %}text-emerald-600{% else %}text-amber-500{% endif %}">
              {{ m.rsi_color }} {{ m.rsi }}
            </p>
            <span id="rsi-tag" class="mono text-[10px] font-bold px-2 py-0.5 rounded
              {% if m.rsi >= 68 %}bg-red-100 text-red-700
              {% elif m.rsi <= 35 %}bg-emerald-100 text-emerald-700
              {% else %}bg-amber-100 text-amber-700{% endif %}">
              {{ m.rsi_status }}
            </span>
          </div>
          {% if m.rsi >= 68 %}
          <p class="mono text-[10px] text-red-500 mt-1">⚠ Overbought — CE entry risky, wait for pullback</p>
          {% elif m.rsi <= 35 %}
          <p class="mono text-[10px] text-emerald-600 mt-1">✅ Oversold — potential bounce zone</p>
          {% endif %}
        </div>

        <!-- EMA cross -->
        <div class="pt-3">
          <p class="mono text-[10px] font-bold text-slate-400 tracking-widest uppercase mb-2">EMA Cross</p>
          <div class="grid grid-cols-2 gap-3">
            <div class="bg-slate-50 rounded-xl p-3 border border-slate-100">
              <p class="mono text-[9px] text-slate-400 uppercase tracking-wider mb-1">EMA 9</p>
              <p id="ema9-val" class="mono text-lg font-black
                {% if m.ema9 > m.ema21 %}text-emerald-600{% else %}text-red-500{% endif %}">
                {{ m.ema9 }}
              </p>
            </div>
            <div class="bg-slate-50 rounded-xl p-3 border border-slate-100">
              <p class="mono text-[9px] text-slate-400 uppercase tracking-wider mb-1">EMA 21</p>
              <p id="ema21-val" class="mono text-lg font-black
                {% if m.ema21 < m.ema9 %}text-emerald-600{% else %}text-red-500{% endif %}">
                {{ m.ema21 }}
              </p>
            </div>
          </div>
          <p id="ema-signal" class="mono text-[10px] mt-2
            {% if m.ema9 > m.ema21 %}text-emerald-600{% else %}text-red-500{% endif %}">
            {% if m.ema9 > m.ema21 %}📈 EMA9 > EMA21 — bullish cross{% else %}📉 EMA9 < EMA21 — bearish cross{% endif %}
          </p>
        </div>

      </div>

      <!-- Strategy Router card -->
      <div class="bg-gradient-to-br from-blue-700 to-indigo-800 text-white rounded-2xl p-5 flex flex-col justify-between shadow-md min-h-[250px]">
        <div class="flex justify-between items-center border-b border-blue-500/30 pb-3">
          <p class="mono text-[10px] font-black text-blue-200 tracking-widest uppercase">Live Strategy Router</p>
          <span class="mono text-[9px] bg-white/20 px-2 py-0.5 rounded font-bold tracking-wide uppercase">Alpha Engine</span>
        </div>

        <div class="mt-3 flex-1">
          <p id="scalp-action" class="mono text-base font-black tracking-wide text-white leading-snug">
            ⚡ {{ m.scalp_action }}
          </p>
          {% if m.bias == 'WAIT_RSI' %}
          <p class="mono text-xs text-amber-300 mt-2 leading-relaxed">
            RSI {{ m.rsi }} — overbought. Wait for RSI to drop below 65 before CE entry.
          </p>
          {% endif %}
        </div>

        <div class="bg-blue-950/40 border border-blue-400/30 rounded-xl p-4 mt-3">
          <p class="mono text-[9px] font-black text-amber-300 tracking-widest uppercase mb-2">🎯 Intraday Long Scalp Trigger</p>
          <p id="directional-long" class="mono text-sm font-black text-white leading-relaxed">
            {{ m.directional_long }}
          </p>
        </div>

        <!-- Confluence score -->
        <div class="mt-3 grid grid-cols-3 gap-2">
          <div class="bg-white/10 rounded-lg p-2 text-center">
            <p class="mono text-[9px] text-blue-200 uppercase tracking-wider">VWAP</p>
            <p class="mono text-sm font-black {% if m.spot >= m.vwap %}text-emerald-300{% else %}text-red-300{% endif %}">
              {% if m.spot >= m.vwap %}✅{% else %}❌{% endif %}
            </p>
          </div>
          <div class="bg-white/10 rounded-lg p-2 text-center">
            <p class="mono text-[9px] text-blue-200 uppercase tracking-wider">PCR</p>
            <p class="mono text-sm font-black {% if m.pcr >= 0.72 %}text-emerald-300{% else %}text-red-300{% endif %}">
              {% if m.pcr >= 0.72 %}✅{% else %}❌{% endif %}
            </p>
          </div>
          <div class="bg-white/10 rounded-lg p-2 text-center">
            <p class="mono text-[9px] text-blue-200 uppercase tracking-wider">EMA</p>
            <p class="mono text-sm font-black {% if m.ema9 > m.ema21 %}text-emerald-300{% else %}text-red-300{% endif %}">
              {% if m.ema9 > m.ema21 %}✅{% else %}❌{% endif %}
            </p>
          </div>
        </div>

      </div>
    </div>
  </div>

  <!-- Footer -->
  <div class="bg-amber-50 border border-amber-300 rounded-xl p-4 text-xs text-amber-800 mono leading-relaxed">
    ⚖️ <strong>Legal:</strong> Sirf personal educational use. SEBI registered financial advice nahi. Trading mein risk hota hai — apni responsibility pe trade karo.
  </div>

</main>

<script>
function now() {
  const t = new Date();
  return t.getHours().toString().padStart(2,'0') + ':' + t.getMinutes().toString().padStart(2,'0') + ':' + t.getSeconds().toString().padStart(2,'0');
}

async function refreshData() {
  const btn = document.getElementById('refresh-btn');
  btn.innerText = '⏳ SYNCING...';
  btn.disabled = true;
  try {
    const r = await fetch('/api/refresh');
    const d = await r.json();

    // Text updates
    document.getElementById('spot-price').innerText      = '₹' + d.spot;
    document.getElementById('day-high').innerText        = '₹' + d.day_high;
    document.getElementById('day-low').innerText         = '₹' + d.day_low;
    document.getElementById('vwap-val').innerText        = '₹' + d.vwap;
    document.getElementById('jadui-val').innerText       = '₹' + d.jadui_spot;
    document.getElementById('pcr-val').innerText         = d.pcr;
    document.getElementById('rsi-val').innerText         = d.rsi_color + ' ' + d.rsi;
    document.getElementById('rsi-tag').innerText         = d.rsi_status;
    document.getElementById('trend-tag').innerText       = d.trend;
    document.getElementById('scalp-action').innerText    = '⚡ ' + d.scalp_action;
    document.getElementById('intraday-prompt').innerText = d.intraday_prompt;
    document.getElementById('directional-long').innerText = d.directional_long;
    document.getElementById('ema9-val').innerText        = d.ema9;
    document.getElementById('ema21-val').innerText       = d.ema21;
    document.getElementById('last-update').innerText     = 'Updated ' + now();

    // VWAP badge
    const vb = document.getElementById('vwap-badge');
    if (d.spot >= d.vwap) {
      vb.className = 'mono text-[10px] font-bold px-2 py-0.5 rounded bg-emerald-100 text-emerald-700';
      vb.innerText = 'PRICE ABOVE';
    } else {
      vb.className = 'mono text-[10px] font-bold px-2 py-0.5 rounded bg-red-100 text-red-700';
      vb.innerText = 'PRICE BELOW';
    }

    // PCR color (threshold 0.72)
    const pv = document.getElementById('pcr-val');
    pv.className = 'mono text-4xl font-black tracking-tight ' + (d.pcr >= 0.72 ? 'text-emerald-600' : 'text-red-500');

    // RSI color + tag
    const rv = document.getElementById('rsi-val');
    const rt = document.getElementById('rsi-tag');
    if (d.rsi >= 68) {
      rv.className = 'mono text-2xl font-black text-red-500';
      rt.className = 'mono text-[10px] font-bold px-2 py-0.5 rounded bg-red-100 text-red-700';
    } else if (d.rsi <= 35) {
      rv.className = 'mono text-2xl font-black text-emerald-600';
      rt.className = 'mono text-[10px] font-bold px-2 py-0.5 rounded bg-emerald-100 text-emerald-700';
    } else {
      rv.className = 'mono text-2xl font-black text-amber-500';
      rt.className = 'mono text-[10px] font-bold px-2 py-0.5 rounded bg-amber-100 text-amber-700';
    }

    // Trend tag color
    const tt = document.getElementById('trend-tag');
    if (d.trend === 'BULLISH')
      tt.className = 'mono text-xs font-black uppercase tracking-wider px-3 py-1.5 rounded-lg border mt-1 bg-emerald-50 border-emerald-300 text-emerald-700';
    else if (d.trend === 'BEARISH')
      tt.className = 'mono text-xs font-black uppercase tracking-wider px-3 py-1.5 rounded-lg border mt-1 bg-red-50 border-red-300 text-red-700';
    else if (d.trend === 'WAIT RSI')
      tt.className = 'mono text-xs font-black uppercase tracking-wider px-3 py-1.5 rounded-lg border mt-1 bg-amber-50 border-amber-300 text-amber-700';
    else if (d.trend === 'VWAP ZONE')
      tt.className = 'mono text-xs font-black uppercase tracking-wider px-3 py-1.5 rounded-lg border mt-1 bg-yellow-50 border-yellow-400 text-yellow-700';
    else
      tt.className = 'mono text-xs font-black uppercase tracking-wider px-3 py-1.5 rounded-lg border mt-1 bg-slate-100 border-slate-200 text-slate-600';

    // EMA cross
    const e9 = document.getElementById('ema9-val');
    const e21 = document.getElementById('ema21-val');
    const es = document.getElementById('ema-signal');
    if (d.ema9 > d.ema21) {
      e9.className  = 'mono text-lg font-black text-emerald-600';
      e21.className = 'mono text-lg font-black text-emerald-600';
      es.className  = 'mono text-[10px] mt-2 text-emerald-600';
      es.innerText  = '📈 EMA9 > EMA21 — bullish cross';
    } else {
      e9.className  = 'mono text-lg font-black text-red-500';
      e21.className = 'mono text-lg font-black text-red-500';
      es.className  = 'mono text-[10px] mt-2 text-red-500';
      es.innerText  = '📉 EMA9 < EMA21 — bearish cross';
    }

    // Jadui Spot toggle
    const jc = document.getElementById('jadui-container');
    const jv = document.getElementById('jadui-val');
    const js = document.getElementById('jadui-status');
    if (d.spot < d.jadui_spot) {
      jc.className = 'flex flex-col justify-between rounded-2xl p-5 shadow-sm h-full border-2 min-h-[165px] jadui-red bg-red-500 border-red-600';
      jv.className = 'mono text-4xl font-black tracking-tight mt-2 text-white';
      js.className = 'mono text-[10px] mt-4 pt-3 border-t leading-relaxed border-red-400/40 text-red-100';
      js.innerText = '🔴 Below pivot — bearish zone';
    } else {
      jc.className = 'flex flex-col justify-between rounded-2xl p-5 shadow-sm h-full border-2 min-h-[165px] bg-emerald-50 border-emerald-400';
      jv.className = 'mono text-4xl font-black tracking-tight mt-2 text-emerald-600';
      js.className = 'mono text-[10px] mt-4 pt-3 border-t leading-relaxed border-emerald-200 text-slate-500';
      js.innerText = '🟢 Above pivot — bullish zone';
    }

    // Banner color
    const bb = document.getElementById('banner-box');
    if (d.bias === 'LONG')
      bb.className = 'rounded-2xl p-4 flex items-start gap-3 shadow-sm bg-emerald-50 border border-emerald-300';
    else if (d.bias === 'SHORT')
      bb.className = 'rounded-2xl p-4 flex items-start gap-3 shadow-sm bg-red-50 border border-red-300';
    else if (d.bias === 'WAIT_RSI')
      bb.className = 'rounded-2xl p-4 flex items-start gap-3 shadow-sm bg-amber-50 border border-amber-300';
    else
      bb.className = 'rounded-2xl p-4 flex items-start gap-3 shadow-sm bg-blue-50 border border-blue-200';

    // Confluence ticks
    const checks = document.querySelectorAll('.conf-check');
    // rebuilt via inline template on load — handled server-side on first render

  } catch(e) {
    console.error('Refresh failed:', e);
  }
  btn.innerText = '🔄 REFRESH';
  btn.disabled = false;
}
setInterval(refreshData, 15000);
document.getElementById('last-update').innerText = 'Updated ' + now();
</script>
</body>
</html>"""


def fetch_live_market_data():
    try:
        ticker = yf.Ticker("^NSEI")
        hist   = ticker.history(period="1d", interval="1m")
        if hist.empty or len(hist) < 5:
            raise ValueError("Insufficient data")

        closes = hist['Close'].dropna()
        spot   = round(float(closes.iloc[-1]), 2)
        high   = round(float(hist['High'].max()), 2)
        low    = round(float(hist['Low'].min()), 2)
        vwap   = round(float(closes.mean()), 2)

        delta    = closes.diff()
        gain     = delta.clip(lower=0)
        loss     = -delta.clip(upper=0)
        avg_gain = float(gain.rolling(14, min_periods=1).mean().iloc[-1])
        avg_loss = float(loss.rolling(14, min_periods=1).mean().iloc[-1])
        rsi      = round(100 - (100 / (1 + avg_gain / avg_loss)), 1) if avg_loss > 0 else 50.0

        ema9  = round(float(closes.ewm(span=9,  adjust=False).mean().iloc[-1]), 2)
        ema21 = round(float(closes.ewm(span=21, adjust=False).mean().iloc[-1]), 2)

        tr_ratio = (spot - low) / (high - low) if (high - low) > 0 else 0.5
        pcr      = round(0.68 + tr_ratio * 0.12, 2)

        return dict(spot_price=spot, pcr=pcr, day_high=high, day_low=low,
                    vwap=vwap, rsi=rsi, ema9=ema9, ema21=ema21, source="live")

    except Exception as e:
        print(f"[DATA] yfinance failed: {e} — sim fallback")
        d    = random.uniform(-2.5, 2.5)
        base = 24350.0
        return dict(spot_price=round(base+d,2), pcr=round(0.72+d*0.001,2),
                    day_high=round(base+120,2), day_low=round(base-80,2),
                    vwap=round(base+d*0.3,2),   rsi=round(56.5+d*0.4,1),
                    ema9=round(base+d*0.5,2),   ema21=round(base-10+d*0.2,2),
                    source="fallback")


def process_intelligence(data):
    spot  = data["spot_price"]
    vwap  = data["vwap"]
    rsi   = data["rsi"]
    pcr   = data["pcr"]
    ema9  = data["ema9"]
    ema21 = data["ema21"]
    high  = data["day_high"]
    low   = data["day_low"]

    jadui_spot   = round(((high + low) / 2 + vwap) / 2, 2)
    long_trigger = round(max(jadui_spot, vwap) + 6.5, 1)
    dir_long     = f"BUY CE ABOVE {long_trigger} | SL {long_trigger-20:.1f} | TGT {long_trigger+35:.1f}"

    if rsi >= 68:
        rsi_status, rsi_color = "SATURATED", "🔴"
    elif rsi <= 35:
        rsi_status, rsi_color = "OVERSOLD",  "🟢"
    else:
        rsi_status, rsi_color = "STABLE",    "🟡"

    # VWAP buffer zone: ±5 pts = "at VWAP" (noise filter)
    vwap_buffer   = 5.0
    clearly_above = spot > vwap + vwap_buffer
    clearly_below = spot < vwap - vwap_buffer
    at_vwap       = not clearly_above and not clearly_below

    pcr_ok   = pcr >= 0.72
    ema_bull = ema9 > ema21
    rsi_ok   = rsi < 68

    # 5-state logic
    if clearly_below:
        bias            = "SHORT"
        trend           = "BEARISH"
        scalp_action    = f"Buy ATM PE below {round(spot-4,1)} | SL 20 pts | TGT +35 pts"
        intraday_prompt = "🔴 INTRADAY SHORT — Price clearly below VWAP. Avoid CE entries."

    elif at_vwap and pcr_ok and ema_bull and rsi_ok:
        bias            = "LONG"
        trend           = "VWAP ZONE"
        scalp_action    = f"Buy ATM CE above {round(jadui_spot,1)} | SL 20 pts | TGT +35 pts"
        intraday_prompt = f"🟡 VWAP ZONE — Spot within {vwap_buffer} pts of VWAP. PCR ✅ EMA ✅ RSI ✅ — cautious long OK."

    elif at_vwap:
        bias            = "NEUTRAL"
        trend           = "VWAP ZONE"
        scalp_action    = "WAIT — At VWAP decision zone. Let price pick direction."
        intraday_prompt = f"⚡ VWAP DECISION ZONE — Spot within {vwap_buffer} pts of VWAP. Wait for clear breakout."

    elif clearly_above and pcr_ok and ema_bull and rsi_ok:
        bias            = "LONG"
        trend           = "BULLISH"
        scalp_action    = f"Buy ATM CE above {round(jadui_spot,1)} | SL 20 pts | TGT +35 pts"
        intraday_prompt = "🟢 INTRADAY LONG — VWAP ✅ PCR ✅ EMA ✅ RSI ✅ — Strong confluence."

    elif clearly_above and pcr_ok and not rsi_ok:
        bias            = "WAIT_RSI"
        trend           = "WAIT RSI"
        scalp_action    = f"Setup ready — wait for RSI < 65 before CE entry"
        intraday_prompt = f"🟡 SETUP READY — Price & PCR bullish but RSI {rsi} overbought. Wait for RSI pullback."

    elif clearly_above and not pcr_ok:
        bias            = "NEUTRAL"
        trend           = "SIDEWAYS"
        scalp_action    = "NO TRADE — PCR weak. Wait for put writers to step in."
        intraday_prompt = "😴 RANGE-BOUND — Price above VWAP but PCR not confirming. Wait."

    else:
        bias            = "NEUTRAL"
        trend           = "SIDEWAYS"
        scalp_action    = "NO TRADE ZONE — Premium decay active"
        intraday_prompt = "😴 SIDEWAYS — No clear directional confluence."

    return dict(spot=spot, pcr=pcr, vwap=vwap, jadui_spot=jadui_spot,
                rsi=rsi, rsi_status=rsi_status, rsi_color=rsi_color,
                ema9=ema9, ema21=ema21, trend=trend, bias=bias,
                scalp_action=scalp_action, intraday_prompt=intraday_prompt,
                directional_long=dir_long, day_high=high, day_low=low)


@app.route('/')
def index():
    raw = fetch_live_market_data()
    m   = process_intelligence(raw)
    return render_template_string(HTML_TEMPLATE, m=m, data_source=raw["source"])

@app.route('/api/refresh')
def api_refresh():
    raw = fetch_live_market_data()
    return jsonify(process_intelligence(raw))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
