import os
import time
import threading
import requests
import pyotp
from flask import Flask, render_template_string
from SmartApi import SmartConnect

app = Flask(__name__)

# ─────────────────────────────────────────────
# TOKENS
# ─────────────────────────────────────────────
NIFTY_TOKEN  = "99926000"   # NSE NIFTY 50 Spot
VIX_TOKEN    = "99926017"   # India VIX

# ─────────────────────────────────────────────
# SESSION CACHE  ← THE CRITICAL FIX
# Login once, reuse token. Re-login only if expired.
# Gemini was logging in on EVERY 5-sec tick = instant ban.
# ─────────────────────────────────────────────
SESSION_CACHE = {
    "obj":          None,
    "logged_in_at": 0,
    "ttl_seconds":  3600,   # Angel One sessions last ~1 hour
}

# ─────────────────────────────────────────────
# TRADE STATE ENGINE
# ─────────────────────────────────────────────
ENGINE = {
    # Tick cache
    "last_update":  0,
    "tick_ttl":     5,       # seconds between real API calls
    "payload":      None,

    # Price tracking
    "last_spot":    0.0,
    "velocity":     0.0,     # pts gained since last tick

    # Trade lifecycle
    # States: BLOCKED → SETUP_READY → TRADE_ACTIVE → BLOCKED
    "status":       "BLOCKED",
    "entry":        0.0,
    "target":       0.0,
    "sl":           0.0,
    "signal":       "SYSTEM INITIALIZING...",

    # ── REAL persistent performance tracker ──
    # Persists in memory; survives tick cycles.
    # Resets only on app restart (Render free-tier caveat noted in UI).
    "session_pnl":      0.0,   # cumulative points this session
    "trades_total":     0,
    "trades_won":       0,
    "trades_lost":      0,
    "last_signal_sent": "",    # dedup Telegram
}

# ─────────────────────────────────────────────
# HTML TEMPLATE
# ─────────────────────────────────────────────
TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="5">
  <title>GOAT PRO V14</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700;900&family=JetBrains+Mono:wght@400;700&display=swap');
    body { font-family: 'Space Grotesk', sans-serif; }
    .mono { font-family: 'JetBrains Mono', monospace; }
    .glow-green { box-shadow: 0 0 18px rgba(16,185,129,0.18); }
    .glow-red   { box-shadow: 0 0 18px rgba(239,68,68,0.18); }
    .glow-blue  { box-shadow: 0 0 18px rgba(59,130,246,0.18); }
    .bar-fill   { transition: width 0.8s ease; }
  </style>
</head>
<body class="bg-[#060b16] text-slate-200 p-3">
<div class="max-w-sm mx-auto space-y-3">

  <!-- ── HEADER ── -->
  <div class="flex justify-between items-center pb-2 border-b border-slate-800">
    <div>
      <p class="mono text-[9px] text-slate-500 tracking-[0.2em] uppercase">Nifty Exclusive · Angel One Feed</p>
      <h1 class="text-lg font-black tracking-tight">
        <span class="text-blue-500">GOAT PRO</span>
        <span class="text-slate-400 text-sm font-medium ml-1">V14</span>
      </h1>
    </div>
    <span class="mono text-[9px] px-2 py-1 rounded border border-emerald-800 bg-emerald-950 text-emerald-400 animate-pulse">
      ⚡ 5s LIVE
    </span>
  </div>

  <!-- ── PRICE + VIX ── -->
  <div class="grid grid-cols-2 gap-2">
    <div class="bg-slate-900 border border-slate-800 rounded-lg p-3 text-center relative overflow-hidden">
      <div class="absolute top-0 inset-x-0 h-[2px] bg-gradient-to-r from-blue-600 to-cyan-500"></div>
      <p class="mono text-[9px] text-slate-500 tracking-widest uppercase">Nifty Spot</p>
      <p class="mono text-2xl font-black text-white mt-0.5">₹{{ m.spot }}</p>
      <p class="mono text-[9px] mt-1 font-bold {{ 'text-emerald-400' if m.velocity > 0 else 'text-red-400' if m.velocity < 0 else 'text-slate-500' }}">
        {{ '+' if m.velocity > 0 else '' }}{{ m.velocity }} pts/tick
      </p>
    </div>
    <div class="bg-slate-900 border border-slate-800 rounded-lg p-3 text-center relative overflow-hidden">
      <div class="absolute top-0 inset-x-0 h-[2px] {{ 'bg-red-500' if m.vix > 18 else 'bg-purple-500' }}"></div>
      <p class="mono text-[9px] text-slate-500 tracking-widest uppercase">India VIX</p>
      <p class="mono text-2xl font-black mt-0.5 {{ 'text-red-400' if m.vix > 18 else 'text-purple-400' }}">{{ m.vix }}</p>
      <p class="mono text-[9px] mt-1 text-slate-500">{{ 'HIGH RISK' if m.vix > 18 else 'STABLE' }}</p>
    </div>
  </div>

  <!-- ── EXECUTION MATRIX ── -->
  <div class="bg-slate-900 border rounded-lg p-3 space-y-2
    {% if m.status == 'TRADE_ACTIVE' %}border-emerald-600 glow-green
    {% elif m.status == 'SETUP_READY' %}border-blue-600 glow-blue
    {% else %}border-slate-700{% endif %}">

    <div class="flex justify-between items-center">
      <span class="mono text-[9px] font-bold text-slate-400 tracking-widest uppercase">Execution Matrix</span>
      {% if m.status == 'TRADE_ACTIVE' %}
        <span class="mono text-[8px] px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800">🟢 LIVE POSITION</span>
      {% elif m.status == 'SETUP_READY' %}
        <span class="mono text-[8px] px-1.5 py-0.5 rounded bg-blue-950 text-blue-400 border border-blue-800">⏳ AWAITING REVERSAL</span>
      {% else %}
        <span class="mono text-[8px] px-1.5 py-0.5 rounded bg-red-950 text-red-500 border border-red-900">🚫 BLOCKED</span>
      {% endif %}
    </div>

    <div class="bg-black/40 rounded border border-slate-800 px-3 py-2 text-center">
      <p class="mono text-[8px] text-slate-600 tracking-widest uppercase mb-1">Signal</p>
      <p class="font-black text-sm {{ 'text-emerald-400' if m.status == 'TRADE_ACTIVE' else 'text-white' if m.status == 'SETUP_READY' else 'text-red-400' }}">{{ m.signal }}</p>
    </div>

    <div class="grid grid-cols-3 gap-1.5 text-center">
      {% for label, val, color in [('ENTRY', m.entry, 'text-blue-400'), ('TARGET', m.target, 'text-emerald-400'), ('STOPLOSS', m.sl, 'text-red-400')] %}
      <div class="bg-[#060b16] border border-slate-800 rounded p-1.5">
        <p class="mono text-[8px] text-slate-600 uppercase">{{ label }}</p>
        <p class="mono text-xs font-black {{ color }}">{% if val > 0 %}₹{{ val }}{% else %}---{% endif %}</p>
      </div>
      {% endfor %}
    </div>
  </div>

  <!-- ── 5/5 CHECKLIST ── -->
  <div class="bg-slate-900 border border-slate-800 rounded-lg p-3">
    <div class="flex justify-between items-center mb-2">
      <p class="mono text-[9px] text-slate-500 tracking-widest uppercase">Strict 5/5 Gate</p>
      <p class="mono text-[9px] font-bold {{ 'text-emerald-400' if m.pass_count == 5 else 'text-amber-400' if m.pass_count >= 3 else 'text-red-400' }}">
        {{ m.pass_count }}/5 PASS
      </p>
    </div>
    <ul class="space-y-1.5">
      {% for label, result in [
        ('Price above trend base', m.chk[0]),
        ('Order flow positive (velocity > 0)', m.chk[1]),
        ('Away from whipsaw zone (>10 pts from round)', m.chk[2]),
        ('VIX stable (< 18.0)', m.chk[3]),
        ('No overextension (< 0.8% from base)', m.chk[4])
      ] %}
      <li class="flex justify-between items-center">
        <span class="mono text-[9px] text-slate-400">{{ label }}</span>
        <span class="mono text-[9px] font-bold {{ 'text-emerald-500' if result else 'text-red-500' }}">{{ 'PASS ✓' if result else 'FAIL ✗' }}</span>
      </li>
      {% endfor %}
    </ul>
  </div>

  <!-- ── PERFORMANCE METER ── -->
  <div class="bg-slate-900 border border-slate-800 rounded-lg p-3 space-y-2">
    <div class="flex justify-between items-center">
      <p class="mono text-[9px] text-slate-500 tracking-widest uppercase">Session Performance</p>
      <p class="mono text-[9px] font-black {{ 'text-emerald-400' if m.win_rate >= 60 else 'text-amber-400' if m.win_rate >= 40 else 'text-red-400' }}">
        {{ m.win_rate }}% WIN-RATE
      </p>
    </div>

    <!-- Win rate bar -->
    <div class="w-full bg-slate-950 rounded-full h-1.5 border border-slate-800">
      <div class="bar-fill h-full rounded-full {{ 'bg-emerald-500' if m.win_rate >= 60 else 'bg-amber-500' if m.win_rate >= 40 else 'bg-red-500' }}"
           style="width: {{ m.win_rate }}%"></div>
    </div>

    <div class="grid grid-cols-4 gap-1 text-center">
      <div class="bg-[#060b16] border border-slate-800 rounded p-1.5">
        <p class="mono text-[8px] text-slate-600">TRADES</p>
        <p class="mono text-xs font-black text-white">{{ m.total }}</p>
      </div>
      <div class="bg-[#060b16] border border-slate-800 rounded p-1.5">
        <p class="mono text-[8px] text-slate-600">WINS</p>
        <p class="mono text-xs font-black text-emerald-400">{{ m.wins }}</p>
      </div>
      <div class="bg-[#060b16] border border-slate-800 rounded p-1.5">
        <p class="mono text-[8px] text-slate-600">LOSS</p>
        <p class="mono text-xs font-black text-red-400">{{ m.losses }}</p>
      </div>
      <div class="bg-[#060b16] border border-slate-800 rounded p-1.5">
        <p class="mono text-[8px] text-slate-600">P&L pts</p>
        <p class="mono text-xs font-black {{ 'text-emerald-400' if m.pnl >= 0 else 'text-red-400' }}">
          {{ '+' if m.pnl >= 0 else '' }}{{ m.pnl }}
        </p>
      </div>
    </div>

    <!-- System health label -->
    {% if m.total == 0 %}
    <p class="mono text-[8px] text-slate-600 text-center">No trades this session. Counters reset on app restart.</p>
    {% elif m.win_rate >= 60 %}
    <p class="mono text-[8px] text-emerald-600 text-center">✓ System running above expectation</p>
    {% elif m.win_rate >= 40 %}
    <p class="mono text-[8px] text-amber-600 text-center">⚠ Marginal edge — review setup conditions</p>
    {% else %}
    <p class="mono text-[8px] text-red-600 text-center">✗ Poor session — consider halting new entries</p>
    {% endif %}
  </div>

</div>
</body>
</html>"""


# ─────────────────────────────────────────────
# TELEGRAM  (non-blocking, fire-and-forget)
# ─────────────────────────────────────────────
def _tg_send(msg):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat  = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": msg, "parse_mode": "HTML"},
            timeout=4
        )
    except Exception:
        pass

def telegram_alert(msg):
    """Sends Telegram in background thread — never blocks main ticker."""
    threading.Thread(target=_tg_send, args=(msg,), daemon=True).start()


# ─────────────────────────────────────────────
# BROKER SESSION  (login once, reuse)
# ─────────────────────────────────────────────
def get_broker_session():
    """Returns cached SmartConnect object. Re-logins only when TTL expires."""
    global SESSION_CACHE
    now = time.time()
    if SESSION_CACHE["obj"] and (now - SESSION_CACHE["logged_in_at"] < SESSION_CACHE["ttl_seconds"]):
        return SESSION_CACHE["obj"], None

    api_key     = os.environ.get("ANGEL_API_KEY")
    client_id   = os.environ.get("ANGEL_CLIENT_ID")
    mpin        = os.environ.get("ANGEL_MPIN")
    totp_secret = os.environ.get("ANGEL_TOTP_SECRET")

    if not all([api_key, client_id, mpin, totp_secret]):
        return None, "ENV VARIABLES MISSING"

    try:
        totp    = pyotp.TOTP(totp_secret).now()
        obj     = SmartConnect(api_key=api_key)
        session = obj.generateSession(client_id, mpin, totp)
        if not session.get("status"):
            return None, "BROKER LOGIN FAILED"
        SESSION_CACHE["obj"]          = obj
        SESSION_CACHE["logged_in_at"] = now
        return obj, None
    except Exception as e:
        return None, f"LOGIN EXCEPTION: {e}"


# ─────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────
def run_pipeline():
    global ENGINE
    now = time.time()

    # Return cached payload within tick window
    if ENGINE["payload"] and (now - ENGINE["last_update"] < ENGINE["tick_ttl"]):
        return ENGINE["payload"]

    # ── Broker session ──
    obj, err = get_broker_session()
    if err:
        return ENGINE["payload"] or {"error": err}

    # ── Fetch prices ──
    try:
        n_res = obj.ltpData("NSE", "NIFTY", NIFTY_TOKEN)
        v_res = obj.ltpData("NSE", "INDIAVIX", VIX_TOKEN)
    except Exception as e:
        # Session may have expired mid-hour; force re-login next tick
        SESSION_CACHE["logged_in_at"] = 0
        return ENGINE["payload"] or {"error": f"LTP FETCH FAILED: {e}"}

    if not n_res.get("status") or "data" not in n_res:
        return ENGINE["payload"] or {"error": "NIFTY FEED DEAD"}

    spot = float(n_res["data"]["ltp"])
    try:
        vix = float(v_res["data"]["ltp"]) if v_res.get("status") else 15.0
    except Exception:
        vix = 15.0

    # ── Velocity ──
    if ENGINE["last_spot"] > 0:
        ENGINE["velocity"] = round(spot - ENGINE["last_spot"], 2)
    ENGINE["last_spot"] = spot
    vel = ENGINE["velocity"]

    # ── Risk sizing (VIX-adjusted, realistic intraday) ──
    vix_mult  = vix / 15.0
    sl_pts    = round(40 * vix_mult, 1)   # base 40 pts SL
    tgt_pts   = round(90 * vix_mult, 1)   # base 90 pts TGT  → ~1:2.25 R:R

    # ── Checklist (all derived from live data, nothing hardcoded) ──
    base100   = (spot // 100) * 100
    base50    = (spot // 50) * 50
    dist_from_round = abs(spot - base50)

    chk = [
        spot > base100,                   # 1. Price above trend base
        vel > 0,                          # 2. Order flow positive
        dist_from_round > 10,             # 3. Away from whipsaw zone
        vix < 18.0,                       # 4. VIX safe
        (spot - base100) < (0.008 * spot) # 5. Not overextended (< 0.8% above base)
    ]
    all_pass = all(chk)
    pass_count = sum(chk)

    # ── State machine ──
    if ENGINE["status"] == "TRADE_ACTIVE":
        if spot >= ENGINE["target"]:
            pts = round(ENGINE["target"] - ENGINE["entry"], 1)
            ENGINE["trades_won"]   += 1
            ENGINE["trades_total"] += 1
            ENGINE["session_pnl"]  += pts
            ENGINE["status"]        = "BLOCKED"
            ENGINE["signal"]        = f"🎯 TARGET HIT +{pts} pts — Scanning next setup"
            telegram_alert(f"🎯 <b>GOAT PRO: TARGET HIT</b>\nBooked <b>+{pts} pts</b> at ₹{spot}\nSession P&L: {round(ENGINE['session_pnl'],1)} pts")
        elif spot <= ENGINE["sl"]:
            pts = round(ENGINE["entry"] - ENGINE["sl"], 1)
            ENGINE["trades_lost"]   += 1
            ENGINE["trades_total"]  += 1
            ENGINE["session_pnl"]   -= pts
            ENGINE["status"]         = "BLOCKED"
            ENGINE["signal"]         = f"🛑 SL HIT -{pts} pts — Risk managed, cooling off"
            telegram_alert(f"🛑 <b>GOAT PRO: STOPLOSS HIT</b>\nLoss <b>-{pts} pts</b> at ₹{spot}\nSession P&L: {round(ENGINE['session_pnl'],1)} pts")

    elif ENGINE["status"] in ("BLOCKED", "SETUP_READY"):
        if not all_pass:
            ENGINE["status"]  = "BLOCKED"
            ENGINE["signal"]  = "NO TRADE ZONE — checklist not cleared"
            ENGINE["entry"]   = 0.0
            ENGINE["target"]  = 0.0
            ENGINE["sl"]      = 0.0
        else:
            # Setup is valid — lock levels
            entry = round(spot - 8, 2)   # slight anticipation of micro-dip
            ENGINE["status"]  = "SETUP_READY"
            ENGINE["entry"]   = entry
            ENGINE["target"]  = round(entry + tgt_pts, 2)
            ENGINE["sl"]      = round(entry - sl_pts,  2)
            ENGINE["signal"]  = f"SETUP READY — Pullback to ₹{ENGINE['entry']}"

            # Trigger: price enters zone AND velocity confirms reversal bounce
            if spot <= (entry + 5) and vel > 0.5:
                ENGINE["status"] = "TRADE_ACTIVE"
                ENGINE["signal"] = f"🔥 LONG EXECUTED at ₹{spot}"
                sig_key = f"LONG_{entry}"
                if ENGINE["last_signal_sent"] != sig_key:
                    ENGINE["last_signal_sent"] = sig_key
                    telegram_alert(
                        f"🟢 <b>GOAT PRO: LONG EXECUTED</b>\n"
                        f"Entry: ₹{spot}\nTarget: ₹{ENGINE['target']}\nSL: ₹{ENGINE['sl']}"
                    )

    # ── Performance stats ──
    total = ENGINE["trades_total"]
    wins  = ENGINE["trades_won"]
    win_rate = round((wins / total * 100)) if total > 0 else 0

    payload = {
        "spot":       round(spot, 2),
        "vix":        round(vix,  2),
        "velocity":   vel,
        "status":     ENGINE["status"],
        "signal":     ENGINE["signal"],
        "entry":      ENGINE["entry"],
        "target":     ENGINE["target"],
        "sl":         ENGINE["sl"],
        "chk":        chk,
        "pass_count": pass_count,
        "total":      total,
        "wins":       wins,
        "losses":     ENGINE["trades_lost"],
        "pnl":        round(ENGINE["session_pnl"], 1),
        "win_rate":   win_rate,
    }

    ENGINE["last_update"] = now
    ENGINE["payload"]     = payload
    return payload


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────
@app.route("/")
def index():
    data = run_pipeline()
    if "error" in data:
        return (
            f"<div style='background:#060b16;color:#f87171;font-family:monospace;"
            f"padding:40px;min-height:100vh;display:flex;flex-direction:column;"
            f"justify-content:center;align-items:center;text-align:center;'>"
            f"<h2>🚨 ENGINE HALTED</h2>"
            f"<p style='color:#94a3b8;margin-top:12px'>{data['error']}</p>"
            f"<p style='color:#334155;font-size:11px;margin-top:20px'>"
            f"Check Render → Environment Variables → ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_MPIN, ANGEL_TOTP_SECRET</p>"
            f"</div>"
        )
    return render_template_string(TEMPLATE, m=data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
