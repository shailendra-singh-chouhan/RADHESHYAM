import os, random, json, time
import requests
import yfinance as yf
import pandas as pd
from flask import Flask, render_template_string, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ── NSE OPTIONS CHAIN ────────────────────────────────────────────────────────
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
    "Connection": "keep-alive",
}
NSE_SESSION = requests.Session()
NSE_SESSION.headers.update(NSE_HEADERS)
_nse_cookie_ts = 0

def _refresh_nse_cookies():
    global _nse_cookie_ts
    if time.time() - _nse_cookie_ts < 300:
        return
    try:
        NSE_SESSION.get("https://www.nseindia.com", timeout=8)
        NSE_SESSION.get("https://www.nseindia.com/option-chain", timeout=8)
        _nse_cookie_ts = time.time()
    except Exception as e:
        print(f"[NSE] Cookie refresh failed: {e}")

def fetch_options_chain(symbol="NIFTY"):
    _refresh_nse_cookies()
    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    try:
        r = NSE_SESSION.get(url, timeout=10)
        if r.status_code != 200:
            raise ValueError(f"HTTP {r.status_code}")
        data = r.json()
        records = data["records"]["data"]
        exp_date = data["records"]["expiryDates"][0]
        spot = float(data["records"]["underlyingValue"])
        strikes = {}
        for rec in records:
            if rec.get("expiryDate") != exp_date:
                continue
            s = rec["strikePrice"]
            if s not in strikes:
                strikes[s] = {"strike": s, "ce_oi": 0, "pe_oi": 0,
                               "ce_iv": 0, "pe_iv": 0, "ce_ltp": 0, "pe_ltp": 0}
            if "CE" in rec:
                strikes[s]["ce_oi"]  = rec["CE"].get("openInterest", 0)
                strikes[s]["ce_iv"]  = round(rec["CE"].get("impliedVolatility", 0), 1)
                strikes[s]["ce_ltp"] = rec["CE"].get("lastPrice", 0)
            if "PE" in rec:
                strikes[s]["pe_oi"]  = rec["PE"].get("openInterest", 0)
                strikes[s]["pe_iv"]  = round(rec["PE"].get("impliedVolatility", 0), 1)
                strikes[s]["pe_ltp"] = rec["PE"].get("lastPrice", 0)
        all_strikes = sorted(strikes.keys())
        atm = min(all_strikes, key=lambda x: abs(x - spot))
        atm_idx = all_strikes.index(atm)
        selected = all_strikes[max(0, atm_idx-5): atm_idx+6]
        chain = [strikes[s] for s in selected if s in strikes]
        total_ce = sum(v["ce_oi"] for v in strikes.values())
        total_pe = sum(v["pe_oi"] for v in strikes.values())
        real_pcr = round(total_pe / total_ce, 2) if total_ce > 0 else 1.0
        max_pain = _calc_max_pain(strikes)
        return {"chain": chain, "spot": spot, "atm": atm,
                "real_pcr": real_pcr, "max_pain": max_pain,
                "expiry": exp_date, "source": "live"}
    except Exception as e:
        print(f"[NSE] Options fetch failed: {e}")
        return _fake_options(symbol)

def _calc_max_pain(strikes):
    min_pain, pain_strike = float('inf'), 0
    for test_s in strikes:
        pain = sum(max(0, test_s - s) * v["ce_oi"] + max(0, s - test_s) * v["pe_oi"]
                   for s, v in strikes.items())
        if pain < min_pain:
            min_pain, pain_strike = pain, test_s
    return pain_strike

def _fake_options(symbol):
    base = 24350 if symbol == "NIFTY" else 52000
    spot = base + random.uniform(-50, 50)
    atm  = round(spot / 50) * 50
    chain = []
    for i in range(-5, 6):
        s = atm + i * 50
        dist = abs(i)
        chain.append({
            "strike": s,
            "ce_oi":  round(random.uniform(3, 20) * (1/(dist+1)) * 100) * 100,
            "pe_oi":  round(random.uniform(3, 20) * (1/(dist+1)) * 100) * 100,
            "ce_iv":  round(12 + dist*1.5 + random.uniform(-1, 1), 1),
            "pe_iv":  round(12 + dist*1.5 + random.uniform(-1, 1), 1),
            "ce_ltp": round(max(0.5, (atm+250-s)*0.8 + random.uniform(-5, 5)), 1),
            "pe_ltp": round(max(0.5, (s-(atm-250))*0.8 + random.uniform(-5, 5)), 1),
        })
    total_ce = sum(r["ce_oi"] for r in chain)
    total_pe = sum(r["pe_oi"] for r in chain)
    return {"chain": chain, "spot": round(spot, 2), "atm": atm,
            "real_pcr": round(total_pe/total_ce, 2) if total_ce else 1.0,
            "max_pain": atm - 50, "expiry": "SIM", "source": "fallback"}


# ── MARKET DATA ──────────────────────────────────────────────────────────────
def fetch_crude_mcx():
    """Fetch MCX Crude Oil price — multiple fallback methods."""
    # Method 1: yfinance MCX ticker
    for ticker_sym in ["CRUDEOIL=F", "MCX:CRUDEOIL", "CL=F"]:
        try:
            t = yf.Ticker(ticker_sym)
            h = t.history(period="1d", interval="1m")
            if not h.empty:
                price = round(float(h['Close'].iloc[-1]), 2)
                # CL=F is USD/barrel — convert to INR/barrel approx
                if ticker_sym == "CL=F" and price < 200:
                    # USD to INR + MCX lot adjustment (~0.159 kl per barrel)
                    # MCX Crude = USD price * USDINR / 159 * 1000
                    try:
                        inr_ticker = yf.Ticker("USDINR=X")
                        inr_hist = inr_ticker.history(period="1d", interval="1m")
                        usdinr = float(inr_hist['Close'].iloc[-1]) if not inr_hist.empty else 83.5
                    except:
                        usdinr = 83.5
                    price = round(price * usdinr / 159 * 100, 2)
                return price
        except Exception as e:
            print(f"[CRUDE] {ticker_sym} failed: {e}")
            continue

    # Method 2: NSE commodity proxy (hardcoded fallback with small drift)
    return round(6850 + random.uniform(-30, 30), 2)

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

        # RSI 14
        delta    = closes.diff()
        gain     = delta.clip(lower=0)
        loss     = -delta.clip(upper=0)
        avg_gain = float(gain.rolling(14, min_periods=1).mean().iloc[-1])
        avg_loss = float(loss.rolling(14, min_periods=1).mean().iloc[-1])
        rsi      = round(100-(100/(1+avg_gain/avg_loss)), 1) if avg_loss > 0 else 50.0

        # EMA 9 & 21
        ema9  = round(float(closes.ewm(span=9,  adjust=False).mean().iloc[-1]), 2)
        ema21 = round(float(closes.ewm(span=21, adjust=False).mean().iloc[-1]), 2)

        # OHLC candles (last 60 bars) for chart
        candles = []
        for ts, row in hist.tail(60).iterrows():
            candles.append({
                "time":  int(ts.timestamp()),
                "open":  round(float(row['Open']),  2),
                "high":  round(float(row['High']),  2),
                "low":   round(float(row['Low']),   2),
                "close": round(float(row['Close']), 2),
            })

        # PCR proxy
        tr_ratio = (spot-low)/(high-low) if (high-low) > 0 else 0.5
        pcr      = round(0.68 + tr_ratio * 0.12, 2)

        # Crude
        crude = fetch_crude_mcx()

        return dict(spot_price=spot, pcr=pcr, day_high=high, day_low=low,
                    vwap=vwap, rsi=rsi, ema9=ema9, ema21=ema21,
                    crude=crude, candles=candles, source="live")

    except Exception as e:
        print(f"[DATA] yfinance failed: {e} — sim fallback")
        d    = random.uniform(-2.5, 2.5)
        base = 24350.0
        now  = int(time.time())
        candles = [{"time": now-(60-i)*60,
                    "open":  round(base+random.uniform(-10, 10), 2),
                    "high":  round(base+random.uniform(5, 20), 2),
                    "low":   round(base-random.uniform(5, 20), 2),
                    "close": round(base+random.uniform(-10, 10), 2)} for i in range(60)]
        return dict(spot_price=round(base+d, 2), pcr=round(0.72+d*0.001, 2),
                    day_high=round(base+120, 2), day_low=round(base-80, 2),
                    vwap=round(base+d*0.3, 2), rsi=round(56.5+d*0.4, 1),
                    ema9=round(base+d*0.5, 2), ema21=round(base-10+d*0.2, 2),
                    crude=round(6850+d*5, 2), candles=candles, source="fallback")


# ── STRATEGY ENGINE ──────────────────────────────────────────────────────────
def process_intelligence(data, opt_pcr=None):
    spot  = data["spot_price"]
    vwap  = data["vwap"]
    rsi   = data["rsi"]
    pcr   = opt_pcr if opt_pcr else data["pcr"]
    ema9  = data["ema9"]
    ema21 = data["ema21"]
    high  = data["day_high"]
    low   = data["day_low"]

    jadui_spot   = round(((high+low)/2 + vwap) / 2, 2)
    long_trigger = round(max(jadui_spot, vwap) + 6.5, 1)
    dir_long     = f"BUY CE ABOVE {long_trigger} | SL {long_trigger-20:.1f} | TGT {long_trigger+35:.1f}"

    rsi_status = "SATURATED" if rsi >= 68 else "OVERSOLD" if rsi <= 35 else "STABLE"
    rsi_color  = "🔴" if rsi >= 68 else "🟢" if rsi <= 35 else "🟡"

    # 5-state VWAP buffer logic
    buf           = 5.0
    clearly_above = spot > vwap + buf
    clearly_below = spot < vwap - buf
    at_vwap       = not clearly_above and not clearly_below
    pcr_ok        = pcr >= 0.72
    ema_bull      = ema9 > ema21
    rsi_ok        = rsi < 68

    if clearly_below:
        bias = "SHORT"; trend = "BEARISH"
        scalp_action    = f"Buy ATM PE below {round(spot-4,1)} | SL 20 pts | TGT +35 pts"
        intraday_prompt = "🔴 INTRADAY SHORT — Price clearly below VWAP. Avoid CE entries."

    elif at_vwap and pcr_ok and ema_bull and rsi_ok:
        bias = "LONG"; trend = "VWAP ZONE"
        scalp_action    = f"Buy ATM CE above {round(jadui_spot,1)} | SL 20 pts | TGT +35 pts"
        intraday_prompt = f"🟡 VWAP ZONE — Within {buf} pts of VWAP. PCR ✅ EMA ✅ RSI ✅ — Cautious long OK."

    elif at_vwap:
        bias = "NEUTRAL"; trend = "VWAP ZONE"
        scalp_action    = "WAIT — At VWAP decision zone. Let price pick direction."
        intraday_prompt = f"⚡ VWAP DECISION ZONE — Within {buf} pts. Wait for clear breakout."

    elif clearly_above and pcr_ok and ema_bull and rsi_ok:
        bias = "LONG"; trend = "BULLISH"
        scalp_action    = f"Buy ATM CE above {round(jadui_spot,1)} | SL 20 pts | TGT +35 pts"
        intraday_prompt = "🟢 INTRADAY LONG — VWAP ✅ PCR ✅ EMA ✅ RSI ✅ — Strong confluence."

    elif clearly_above and pcr_ok and not rsi_ok:
        bias = "WAIT_RSI"; trend = "WAIT RSI"
        scalp_action    = f"Setup ready — wait for RSI < 65 before CE entry"
        intraday_prompt = f"🟡 SETUP READY — Bullish but RSI {rsi} overbought. Wait for pullback."

    elif clearly_above and not pcr_ok:
        bias = "NEUTRAL"; trend = "SIDEWAYS"
        scalp_action    = "NO TRADE — PCR weak. Wait for put writers to step in."
        intraday_prompt = "😴 RANGE-BOUND — Price above VWAP but PCR not confirming."

    else:
        bias = "NEUTRAL"; trend = "SIDEWAYS"
        scalp_action    = "NO TRADE ZONE — Premium decay active"
        intraday_prompt = "😴 SIDEWAYS — No clear directional confluence."

    return dict(spot=spot, pcr=pcr, vwap=vwap, jadui_spot=jadui_spot,
                rsi=rsi, rsi_status=rsi_status, rsi_color=rsi_color,
                ema9=ema9, ema21=ema21, trend=trend, bias=bias,
                scalp_action=scalp_action, intraday_prompt=intraday_prompt,
                directional_long=dir_long, day_high=high, day_low=low,
                crude=data.get("crude", 0))


# ── HTML TEMPLATE ────────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>⚡ GOAT PRO — Live Command Center</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700;800&display=swap');
  .mono { font-family: 'JetBrains Mono', monospace; }
  @keyframes pulse-red { 0%,100%{opacity:1} 50%{opacity:.6} }
  .jadui-red { animation: pulse-red 1.4s ease-in-out infinite; }
  #chart-wrap { width:100%; height:300px; border-radius:12px; overflow:hidden; }
  .oi-bar-ce { background:linear-gradient(90deg,#ef4444,#fca5a5); height:7px; border-radius:4px; transition:width .5s; }
  .oi-bar-pe { background:linear-gradient(90deg,#10b981,#6ee7b7); height:7px; border-radius:4px; transition:width .5s; }
  .tbl-atm { background:#eff6ff; }
</style>
</head>
<body class="bg-slate-100 text-slate-800 min-h-screen antialiased">

<!-- HEADER -->
<header class="bg-white border-b border-slate-200 sticky top-0 z-50 shadow-sm">
  <div class="max-w-6xl mx-auto px-5 py-3 flex flex-wrap items-center justify-between gap-3">
    <div class="flex items-center gap-3">
      <span class="h-2.5 w-2.5 rounded-full bg-blue-600 animate-pulse inline-block"></span>
      <h1 class="mono text-lg font-extrabold tracking-widest text-slate-900">
        ⚡ GOAT PRO
        <span class="text-[10px] bg-blue-50 text-blue-700 border border-blue-200 px-2 py-0.5 rounded ml-2 font-bold">LIVE DATA CORE</span>
        {% if src == 'fallback' %}
        <span class="text-[10px] bg-amber-50 text-amber-700 border border-amber-300 px-2 py-0.5 rounded ml-1 font-bold">⚠ SIM</span>
        {% endif %}
      </h1>
    </div>
    <div class="flex items-center gap-3">
      <span class="mono text-[10px] text-slate-400" id="ts">--</span>
      <button id="rbtn" onclick="refreshAll()"
        class="mono bg-blue-600 hover:bg-blue-700 active:scale-95 text-white px-5 py-2 rounded-xl text-sm font-bold tracking-wider transition-all shadow">
        🔄 REFRESH
      </button>
    </div>
  </div>
</header>

<main class="max-w-6xl mx-auto px-5 py-5 space-y-5">

  <!-- Banner -->
  <div id="banner" class="rounded-2xl p-4 flex items-start gap-3 shadow-sm
    {% if m.bias=='LONG' %}bg-emerald-50 border border-emerald-300
    {% elif m.bias=='SHORT' %}bg-red-50 border border-red-300
    {% elif m.bias=='WAIT_RSI' %}bg-amber-50 border border-amber-300
    {% else %}bg-blue-50 border border-blue-200{% endif %}">
    <span class="text-xl mt-0.5">📢</span>
    <div>
      <p class="mono text-[10px] font-bold tracking-widest uppercase mb-1
        {% if m.bias=='LONG' %}text-emerald-700{% elif m.bias=='SHORT' %}text-red-700
        {% elif m.bias=='WAIT_RSI' %}text-amber-700{% else %}text-blue-700{% endif %}">
        Strategy Engine</p>
      <p id="prompt" class="mono text-sm font-bold text-slate-800 leading-relaxed">{{ m.intraday_prompt }}</p>
    </div>
  </div>

  <!-- Section 1: Price Blocks (3 col) -->
  <div>
    <p class="mono text-sm font-black text-blue-900 uppercase tracking-wider border-l-4 border-blue-600 pl-3 mb-3">📊 Nifty Segment Core</p>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 items-stretch">

      <!-- Spot -->
      <div class="bg-white border border-slate-200 rounded-2xl p-5 flex flex-col justify-between shadow-sm min-h-[165px]">
        <p class="mono text-[10px] font-bold text-slate-400 tracking-widest uppercase">Nifty Spot</p>
        <p id="spot" class="mono text-4xl font-black text-blue-600 tracking-tight mt-2">₹{{ m.spot }}</p>
        <div class="flex justify-between mono text-xs text-slate-400 mt-4 pt-3 border-t border-slate-100">
          <span>H: <span id="hi" class="text-slate-700 font-bold">₹{{ m.day_high }}</span></span>
          <span>L: <span id="lo" class="text-slate-700 font-bold">₹{{ m.day_low }}</span></span>
        </div>
      </div>

      <!-- VWAP -->
      <div class="bg-white border border-slate-200 rounded-2xl p-5 flex flex-col justify-between shadow-sm min-h-[165px]">
        <p class="mono text-[10px] font-bold text-slate-400 tracking-widest uppercase">Session VWAP</p>
        <p id="vwap" class="mono text-4xl font-black text-blue-600 tracking-tight mt-2">₹{{ m.vwap }}</p>
        <div class="flex items-center gap-2 mt-4 pt-3 border-t border-slate-100">
          <span id="vbadge" class="mono text-[10px] font-bold px-2 py-0.5 rounded
            {% if m.spot>=m.vwap %}bg-emerald-100 text-emerald-700{% else %}bg-red-100 text-red-700{% endif %}">
            {% if m.spot>=m.vwap %}ABOVE{% else %}BELOW{% endif %}
          </span>
          <span class="mono text-[10px] text-slate-400">1-min avg</span>
        </div>
      </div>

      <!-- Jadui Spot -->
      <div class="flex flex-col min-h-[165px]">
        <div id="jbox"
          class="flex flex-col justify-between rounded-2xl p-5 shadow-sm h-full border-2 min-h-[165px]
          {% if m.spot<m.jadui_spot %}jadui-red bg-red-500 border-red-600{% else %}bg-emerald-50 border-emerald-400{% endif %}">
          <p class="mono text-[10px] font-bold tracking-widest uppercase
            {% if m.spot<m.jadui_spot %}text-red-100{% else %}text-slate-400{% endif %}">✨ Jadui Spot Pivot</p>
          <p id="jadui" class="mono text-4xl font-black tracking-tight mt-2
            {% if m.spot<m.jadui_spot %}text-white{% else %}text-emerald-600{% endif %}">₹{{ m.jadui_spot }}</p>
          <p id="jstatus" class="mono text-[10px] mt-4 pt-3 border-t leading-relaxed
            {% if m.spot<m.jadui_spot %}border-red-400/40 text-red-100{% else %}border-emerald-200 text-slate-500{% endif %}">
            {% if m.spot<m.jadui_spot %}🔴 Below pivot — bearish zone{% else %}🟢 Above pivot — bullish zone{% endif %}
          </p>
        </div>
      </div>

    </div>
  </div>

  <!-- Section 2: Crude + Indicators + Strategy (3 col) -->
  <div>
    <p class="mono text-sm font-black text-indigo-900 uppercase tracking-wider border-l-4 border-indigo-600 pl-3 mb-3">🧠 Indicators & Strategy Engine</p>
    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 items-stretch">

      <!-- Crude + PCR + RSI + EMA -->
      <div class="md:col-span-2 bg-white border border-slate-200 rounded-2xl p-5 shadow-sm min-h-[250px]">
        <div class="grid grid-cols-2 gap-4">

          <!-- PCR + Trend -->
          <div class="border-b border-slate-100 pb-4 col-span-2 flex justify-between items-start">
            <div>
              <p class="mono text-[10px] font-bold text-slate-400 tracking-widest uppercase mb-1">PCR (Real)</p>
              <p id="pcr" class="mono text-4xl font-black tracking-tight
                {% if m.pcr>=0.72 %}text-emerald-600{% else %}text-red-500{% endif %}">{{ m.pcr }}</p>
            </div>
            <div class="text-right">
              <span id="trend" class="mono text-xs font-black uppercase tracking-wider px-3 py-1.5 rounded-lg border
                {% if m.trend=='BULLISH' %}bg-emerald-50 border-emerald-300 text-emerald-700
                {% elif m.trend=='BEARISH' %}bg-red-50 border-red-300 text-red-700
                {% elif m.trend=='WAIT RSI' %}bg-amber-50 border-amber-300 text-amber-700
                {% elif m.trend=='VWAP ZONE' %}bg-yellow-50 border-yellow-400 text-yellow-700
                {% else %}bg-slate-100 border-slate-200 text-slate-600{% endif %}">{{ m.trend }}</span>
            </div>
          </div>

          <!-- RSI -->
          <div class="border-r border-slate-100 pr-4">
            <p class="mono text-[10px] font-bold text-slate-400 tracking-widest uppercase mb-1">RSI (14)</p>
            <div class="flex items-center gap-2">
              <p id="rsi" class="mono text-2xl font-black
                {% if m.rsi>=68 %}text-red-500{% elif m.rsi<=35 %}text-emerald-600{% else %}text-amber-500{% endif %}">
                {{ m.rsi_color }} {{ m.rsi }}</p>
              <span id="rtag" class="mono text-[10px] font-bold px-2 py-0.5 rounded
                {% if m.rsi>=68 %}bg-red-100 text-red-700{% elif m.rsi<=35 %}bg-emerald-100 text-emerald-700
                {% else %}bg-amber-100 text-amber-700{% endif %}">{{ m.rsi_status }}</span>
            </div>
            {% if m.rsi>=68 %}<p class="mono text-[10px] text-red-500 mt-1">⚠ Wait for pullback</p>{% endif %}
          </div>

          <!-- Crude Oil -->
          <div>
            <p class="mono text-[10px] font-bold text-slate-400 tracking-widest uppercase mb-1">MCX Crude Oil</p>
            <p id="crude" class="mono text-2xl font-black text-orange-600 tracking-tight">₹{{ m.crude }}</p>
            <p class="mono text-[10px] text-slate-400 mt-1">per barrel · INR</p>
          </div>

          <!-- EMA -->
          <div class="col-span-2 pt-3 border-t border-slate-100">
            <p class="mono text-[10px] font-bold text-slate-400 tracking-widest uppercase mb-2">EMA Cross</p>
            <div class="grid grid-cols-3 gap-3">
              <div class="bg-slate-50 rounded-xl p-3 border border-slate-100">
                <p class="mono text-[9px] text-slate-400 uppercase tracking-wider mb-1">EMA 9</p>
                <p id="e9" class="mono text-lg font-black {% if m.ema9>m.ema21 %}text-emerald-600{% else %}text-red-500{% endif %}">{{ m.ema9 }}</p>
              </div>
              <div class="bg-slate-50 rounded-xl p-3 border border-slate-100">
                <p class="mono text-[9px] text-slate-400 uppercase tracking-wider mb-1">EMA 21</p>
                <p id="e21" class="mono text-lg font-black {% if m.ema21<m.ema9 %}text-emerald-600{% else %}text-red-500{% endif %}">{{ m.ema21 }}</p>
              </div>
              <div class="bg-slate-50 rounded-xl p-3 border border-slate-100 flex items-center justify-center">
                <p id="esig" class="mono text-xs font-black text-center {% if m.ema9>m.ema21 %}text-emerald-600{% else %}text-red-500{% endif %}">
                  {% if m.ema9>m.ema21 %}📈 Bull<br>Cross{% else %}📉 Bear<br>Cross{% endif %}
                </p>
              </div>
            </div>
          </div>

        </div>
      </div>

      <!-- Strategy Router -->
      <div class="bg-gradient-to-br from-blue-700 to-indigo-800 text-white rounded-2xl p-5 flex flex-col justify-between shadow-md min-h-[250px]">
        <div class="flex justify-between items-center border-b border-blue-500/30 pb-3">
          <p class="mono text-[10px] font-black text-blue-200 tracking-widest uppercase">Strategy Router</p>
          <span class="mono text-[9px] bg-white/20 px-2 py-0.5 rounded font-bold uppercase">Alpha</span>
        </div>
        <p id="action" class="mono text-base font-black text-white leading-snug mt-3">⚡ {{ m.scalp_action }}</p>
        <div class="bg-blue-950/40 border border-blue-400/30 rounded-xl p-4 mt-3">
          <p class="mono text-[9px] font-black text-amber-300 tracking-widest uppercase mb-2">🎯 Long Scalp Trigger</p>
          <p id="dtrig" class="mono text-sm font-black text-white leading-relaxed">{{ m.directional_long }}</p>
        </div>
        <!-- Confluence ticks -->
        <div class="mt-3 grid grid-cols-3 gap-2">
          <div class="bg-white/10 rounded-lg p-2 text-center">
            <p class="mono text-[9px] text-blue-200 uppercase">VWAP</p>
            <p id="cv" class="mono text-sm font-black {% if m.spot>=m.vwap %}text-emerald-300{% else %}text-red-300{% endif %}">
              {% if m.spot>=m.vwap %}✅{% else %}❌{% endif %}</p>
          </div>
          <div class="bg-white/10 rounded-lg p-2 text-center">
            <p class="mono text-[9px] text-blue-200 uppercase">PCR</p>
            <p id="cp" class="mono text-sm font-black {% if m.pcr>=0.72 %}text-emerald-300{% else %}text-red-300{% endif %}">
              {% if m.pcr>=0.72 %}✅{% else %}❌{% endif %}</p>
          </div>
          <div class="bg-white/10 rounded-lg p-2 text-center">
            <p class="mono text-[9px] text-blue-200 uppercase">EMA</p>
            <p id="ce" class="mono text-sm font-black {% if m.ema9>m.ema21 %}text-emerald-300{% else %}text-red-300{% endif %}">
              {% if m.ema9>m.ema21 %}✅{% else %}❌{% endif %}</p>
          </div>
        </div>
      </div>

    </div>
  </div>

  <!-- Section 3: Candle Chart -->
  <div>
    <p class="mono text-sm font-black text-slate-700 uppercase tracking-wider border-l-4 border-slate-400 pl-3 mb-3">📈 Live Candle Chart — 1 Min</p>
    <div class="bg-white border border-slate-200 rounded-2xl p-4 shadow-sm">
      <div id="chart-wrap"></div>
      <div class="flex gap-4 mt-3 mono text-[10px] text-slate-500 flex-wrap">
        <span>🔵 VWAP</span><span>🟠 EMA9</span><span>🟣 EMA21</span>
        <span id="cbars" class="ml-auto text-slate-400"></span>
      </div>
    </div>
  </div>

  <!-- Section 4: Options Chain -->
  <div>
    <p class="mono text-sm font-black text-slate-700 uppercase tracking-wider border-l-4 border-purple-500 pl-3 mb-3">
      ⛓ Options Chain — Nifty
      <span id="oexp" class="text-[10px] font-normal text-slate-400 ml-2">loading...</span>
    </p>
    <!-- Summary strip -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm text-center">
        <p class="mono text-[9px] text-slate-400 uppercase tracking-wider mb-1">Real PCR</p>
        <p id="opcr" class="mono text-2xl font-black text-slate-800">--</p>
      </div>
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm text-center">
        <p class="mono text-[9px] text-slate-400 uppercase tracking-wider mb-1">Max Pain</p>
        <p id="omp" class="mono text-2xl font-black text-amber-600">--</p>
      </div>
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm text-center">
        <p class="mono text-[9px] text-slate-400 uppercase tracking-wider mb-1">ATM Strike</p>
        <p id="oatm" class="mono text-2xl font-black text-blue-600">--</p>
      </div>
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm text-center">
        <p class="mono text-[9px] text-slate-400 uppercase tracking-wider mb-1">PCR Signal</p>
        <p id="osig" class="mono text-lg font-black text-slate-600">--</p>
      </div>
    </div>
    <!-- Chain table -->
    <div class="bg-white border border-slate-200 rounded-2xl shadow-sm overflow-x-auto">
      <table class="w-full text-center mono text-xs min-w-[520px]">
        <thead>
          <tr class="border-b border-slate-100 bg-slate-50">
            <th class="py-2 px-3 text-red-600 font-bold">CE LTP</th>
            <th class="py-2 px-3 text-red-500 font-bold">CE IV%</th>
            <th class="py-2 px-3 text-red-500 font-bold">CE OI</th>
            <th class="py-2 px-2 font-black text-slate-700 bg-blue-50 text-sm">STRIKE</th>
            <th class="py-2 px-3 text-emerald-500 font-bold">PE OI</th>
            <th class="py-2 px-3 text-emerald-500 font-bold">PE IV%</th>
            <th class="py-2 px-3 text-emerald-600 font-bold">PE LTP</th>
          </tr>
        </thead>
        <tbody id="otbl">
          <tr><td colspan="7" class="py-8 text-slate-400 mono text-xs">Loading options chain...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Footer -->
  <div class="bg-amber-50 border border-amber-300 rounded-xl p-4 text-xs text-amber-800 mono leading-relaxed">
    ⚖️ <strong>Legal:</strong> Sirf personal educational use. SEBI registered financial advice nahi. Trading mein risk hota hai — apni responsibility pe trade karo.
  </div>

</main>

<!-- CHART + OPTIONS SCRIPTS -->
<script>
let chart, cSeries, vwapL, ema9L, ema21L;

function initChart() {
  const el = document.getElementById('chart-wrap');
  chart = LightweightCharts.createChart(el, {
    width: el.clientWidth, height: 300,
    layout: { background: { color: '#ffffff' }, textColor: '#64748b' },
    grid: { vertLines: { color: '#f1f5f9' }, horzLines: { color: '#f1f5f9' } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: '#e2e8f0' },
    timeScale: { borderColor: '#e2e8f0', timeVisible: true, secondsVisible: false },
  });
  cSeries = chart.addCandlestickSeries({
    upColor:'#10b981', downColor:'#ef4444',
    borderUpColor:'#10b981', borderDownColor:'#ef4444',
    wickUpColor:'#10b981', wickDownColor:'#ef4444',
  });
  vwapL  = chart.addLineSeries({ color:'#3b82f6', lineWidth:2, title:'VWAP' });
  ema9L  = chart.addLineSeries({ color:'#f97316', lineWidth:1, lineStyle:2, title:'EMA9' });
  ema21L = chart.addLineSeries({ color:'#8b5cf6', lineWidth:1, lineStyle:2, title:'EMA21' });
  window.addEventListener('resize', () => chart.resize(el.clientWidth, 300));
}

function loadChart(candles, vwap, ema9, ema21) {
  if (!cSeries) initChart();
  if (!candles || !candles.length) return;
  cSeries.setData(candles);
  vwapL.setData(candles.map(c => ({ time: c.time, value: parseFloat(vwap) })));
  ema9L.setData(candles.map(c => ({ time: c.time, value: parseFloat(ema9) })));
  ema21L.setData(candles.map(c => ({ time: c.time, value: parseFloat(ema21) })));
  chart.timeScale().fitContent();
  document.getElementById('cbars').innerText = candles.length + ' bars';
}

function fmtOI(n) {
  if (n >= 100000) return (n/100000).toFixed(1)+'L';
  if (n >= 1000)   return (n/1000).toFixed(1)+'K';
  return n;
}

function renderOptions(opt) {
  document.getElementById('oexp').innerText  = opt.expiry + (opt.source==='fallback' ? ' [SIM]' : '');
  document.getElementById('opcr').innerText  = opt.real_pcr;
  document.getElementById('omp').innerText   = '₹' + opt.max_pain;
  document.getElementById('oatm').innerText  = '₹' + opt.atm;
  const p = parseFloat(opt.real_pcr);
  const s = document.getElementById('osig');
  if (p >= 1.2)      { s.innerText='🟢 Very Bullish'; s.className='mono text-lg font-black text-emerald-600'; }
  else if (p >= 0.9) { s.innerText='🟢 Bullish';      s.className='mono text-lg font-black text-emerald-500'; }
  else if (p >= 0.7) { s.innerText='🟡 Neutral';      s.className='mono text-lg font-black text-amber-500'; }
  else               { s.innerText='🔴 Bearish';      s.className='mono text-lg font-black text-red-500'; }
  const maxOI = Math.max(...opt.chain.map(r => Math.max(r.ce_oi, r.pe_oi)), 1);
  let html = '';
  opt.chain.forEach(row => {
    const isATM = row.strike === opt.atm;
    const cw = Math.round((row.ce_oi/maxOI)*100);
    const pw = Math.round((row.pe_oi/maxOI)*100);
    html += `<tr class="border-b border-slate-50 hover:bg-slate-50 ${isATM?'tbl-atm':''}">
      <td class="py-2 px-3 text-red-600 font-bold">${row.ce_ltp}</td>
      <td class="py-2 px-3 text-red-400">${row.ce_iv}%</td>
      <td class="py-2 px-3"><div class="flex items-center gap-1 justify-end">
        <span class="text-red-500 font-bold">${fmtOI(row.ce_oi)}</span>
        <div class="w-14"><div class="oi-bar-ce" style="width:${cw}%"></div></div>
      </div></td>
      <td class="py-2 px-2 font-black bg-blue-50 text-sm ${isATM?'text-blue-600':''}">${row.strike}${isATM?' ◆':''}</td>
      <td class="py-2 px-3"><div class="flex items-center gap-1">
        <div class="w-14"><div class="oi-bar-pe" style="width:${pw}%"></div></div>
        <span class="text-emerald-600 font-bold">${fmtOI(row.pe_oi)}</span>
      </div></td>
      <td class="py-2 px-3 text-emerald-400">${row.pe_iv}%</td>
      <td class="py-2 px-3 text-emerald-600 font-bold">${row.pe_ltp}</td>
    </tr>`;
  });
  document.getElementById('otbl').innerHTML = html;
}

function clr(cond, t, f) { return cond ? t : f; }
function now() {
  const t = new Date();
  return [t.getHours(),t.getMinutes(),t.getSeconds()].map(x=>String(x).padStart(2,'0')).join(':');
}

async function refreshAll() {
  const btn = document.getElementById('rbtn');
  btn.innerText = '⏳ SYNCING...'; btn.disabled = true;
  try {
    const [mr, or] = await Promise.all([fetch('/api/refresh'), fetch('/api/options')]);
    const d = await mr.json();
    const o = await or.json();

    // Text
    document.getElementById('spot').innerText  = '₹' + d.spot;
    document.getElementById('hi').innerText    = '₹' + d.day_high;
    document.getElementById('lo').innerText    = '₹' + d.day_low;
    document.getElementById('vwap').innerText  = '₹' + d.vwap;
    document.getElementById('jadui').innerText = '₹' + d.jadui_spot;
    document.getElementById('pcr').innerText   = d.pcr;
    document.getElementById('rsi').innerText   = d.rsi_color + ' ' + d.rsi;
    document.getElementById('rtag').innerText  = d.rsi_status;
    document.getElementById('trend').innerText = d.trend;
    document.getElementById('action').innerText= '⚡ ' + d.scalp_action;
    document.getElementById('prompt').innerText= d.intraday_prompt;
    document.getElementById('dtrig').innerText = d.directional_long;
    document.getElementById('e9').innerText    = d.ema9;
    document.getElementById('e21').innerText   = d.ema21;
    document.getElementById('crude').innerText = '₹' + d.crude;
    document.getElementById('ts').innerText    = 'Updated ' + now();

    // PCR color
    document.getElementById('pcr').className = 'mono text-4xl font-black tracking-tight ' + clr(d.pcr>=0.72,'text-emerald-600','text-red-500');
    // RSI
    const rc = d.rsi>=68?'text-red-500':d.rsi<=35?'text-emerald-600':'text-amber-500';
    document.getElementById('rsi').className = 'mono text-2xl font-black ' + rc;
    const rtc = d.rsi>=68?'bg-red-100 text-red-700':d.rsi<=35?'bg-emerald-100 text-emerald-700':'bg-amber-100 text-amber-700';
    document.getElementById('rtag').className = 'mono text-[10px] font-bold px-2 py-0.5 rounded ' + rtc;
    // EMA
    const ec = clr(d.ema9>d.ema21,'text-emerald-600','text-red-500');
    document.getElementById('e9').className = document.getElementById('e21').className = 'mono text-lg font-black ' + ec;
    const esig = document.getElementById('esig');
    esig.className = 'mono text-xs font-black text-center ' + ec;
    esig.innerHTML = d.ema9>d.ema21 ? '📈 Bull<br>Cross' : '📉 Bear<br>Cross';
    // VWAP badge
    const vb = document.getElementById('vbadge');
    vb.className = 'mono text-[10px] font-bold px-2 py-0.5 rounded ' + clr(d.spot>=d.vwap,'bg-emerald-100 text-emerald-700','bg-red-100 text-red-700');
    vb.innerText = d.spot>=d.vwap ? 'ABOVE' : 'BELOW';
    // Confluence
    document.getElementById('cv').innerText = d.spot>=d.vwap ? '✅' : '❌';
    document.getElementById('cv').className = 'mono text-sm font-black ' + clr(d.spot>=d.vwap,'text-emerald-300','text-red-300');
    document.getElementById('cp').innerText = d.pcr>=0.72 ? '✅' : '❌';
    document.getElementById('cp').className = 'mono text-sm font-black ' + clr(d.pcr>=0.72,'text-emerald-300','text-red-300');
    document.getElementById('ce').innerText = d.ema9>d.ema21 ? '✅' : '❌';
    document.getElementById('ce').className = 'mono text-sm font-black ' + clr(d.ema9>d.ema21,'text-emerald-300','text-red-300');
    // Trend tag
    const tt = document.getElementById('trend');
    const tm = {BULLISH:'bg-emerald-50 border-emerald-300 text-emerald-700',BEARISH:'bg-red-50 border-red-300 text-red-700','WAIT RSI':'bg-amber-50 border-amber-300 text-amber-700','VWAP ZONE':'bg-yellow-50 border-yellow-400 text-yellow-700'};
    tt.className = 'mono text-xs font-black uppercase tracking-wider px-3 py-1.5 rounded-lg border ' + (tm[d.trend]||'bg-slate-100 border-slate-200 text-slate-600');
    // Jadui box
    const jb=document.getElementById('jbox'), jv=document.getElementById('jadui'), js=document.getElementById('jstatus');
    if (d.spot < d.jadui_spot) {
      jb.className='flex flex-col justify-between rounded-2xl p-5 shadow-sm h-full border-2 min-h-[165px] jadui-red bg-red-500 border-red-600';
      jv.className='mono text-4xl font-black tracking-tight mt-2 text-white';
      js.className='mono text-[10px] mt-4 pt-3 border-t border-red-400/40 text-red-100';
      js.innerText='🔴 Below pivot — bearish zone';
    } else {
      jb.className='flex flex-col justify-between rounded-2xl p-5 shadow-sm h-full border-2 min-h-[165px] bg-emerald-50 border-emerald-400';
      jv.className='mono text-4xl font-black tracking-tight mt-2 text-emerald-600';
      js.className='mono text-[10px] mt-4 pt-3 border-t border-emerald-200 text-slate-500';
      js.innerText='🟢 Above pivot — bullish zone';
    }
    // Banner
    const bb=document.getElementById('banner');
    const bm={LONG:'bg-emerald-50 border border-emerald-300',SHORT:'bg-red-50 border border-red-300',WAIT_RSI:'bg-amber-50 border border-amber-300'};
    bb.className='rounded-2xl p-4 flex items-start gap-3 shadow-sm ' + (bm[d.bias]||'bg-blue-50 border border-blue-200');
    // Chart
    if (d.candles && d.candles.length) loadChart(d.candles, d.vwap, d.ema9, d.ema21);
    // Options
    renderOptions(o);
  } catch(e) { console.error('Refresh failed:', e); }
  btn.innerText = '🔄 REFRESH'; btn.disabled = false;
}

// Init
initChart();
const IC = {{ cj | safe }};
if (IC.length) loadChart(IC, {{ m.vwap }}, {{ m.ema9 }}, {{ m.ema21 }});
document.getElementById('ts').innerText = 'Updated ' + now();
setInterval(refreshAll, 15000);
fetch('/api/options').then(r=>r.json()).then(renderOptions).catch(console.error);
</script>
</body>
</html>"""


# ── ROUTES ───────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    raw = fetch_live_market_data()
    opt = fetch_options_chain()
    m   = process_intelligence(raw, opt_pcr=opt["real_pcr"])
    cj  = json.dumps(raw.get("candles", []))
    return render_template_string(HTML, m=m, src=raw["source"], cj=cj)

@app.route('/api/refresh')
def api_refresh():
    raw    = fetch_live_market_data()
    result = process_intelligence(raw)
    result["candles"] = raw.get("candles", [])
    result["crude"]   = raw.get("crude", 0)
    return jsonify(result)

@app.route('/api/options')
def api_options():
    return jsonify(fetch_options_chain())

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
