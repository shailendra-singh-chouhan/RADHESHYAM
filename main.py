import os
import time
import datetime
import threading
import sqlite3
import json
import requests
import pyotp
from flask import Flask, render_template_string, request, jsonify
from SmartApi import SmartConnect

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except:
    YFINANCE_AVAILABLE = False

app = Flask(__name__)

# ── TOKENS ──────────────────────────────────────────────
NIFTY_TOKEN = "99926000"
VIX_TOKEN   = "99926017"

# ── MARKET HOURS GUARD ──────────────────────────────────
def market_status():
    now = datetime.datetime.now()
    wd  = now.weekday()  # 0=Mon, 6=Sun
    t   = now.time()

    if wd >= 5:
        return "CLOSED", "Weekend — Market Closed"
    if t < datetime.time(9, 0):
        return "CLOSED", "Market opens at 9:00 AM"
    if t < datetime.time(9, 15):
        return "PRE_OPEN", "Pre-Open Session (9:00-9:15) — No Trades"
    if t > datetime.time(15, 30):
        return "CLOSED", "Market Closed after 3:30 PM"
    return "OPEN", "Market Open"

# ── ATM STRIKE CALCULATOR ───────────────────────────────
def get_atm_strike(spot, interval=50):
    return int(round(spot / interval) * interval)

# ── CANDLE BUILDER ──────────────────────────────────────
CANDLE_5MIN = []
_candle_current = {"open": 0, "high": 0, "low": 0, "close": 0, "time": None}

def update_candle(price):
    global _candle_current, CANDLE_5MIN
    now = datetime.datetime.now()
    minute = now.minute
    second = now.second

    if _candle_current["time"] is None:
        _candle_current = {"open": price, "high": price, "low": price, "close": price, "time": now}
        return

    prev_slot = int(_candle_current["time"].minute / 5)
    curr_slot = int(minute / 5)

    if curr_slot != prev_slot:
        # Candle close — save karo
        CANDLE_5MIN.append(dict(_candle_current))
        if len(CANDLE_5MIN) > 100:
            CANDLE_5MIN.pop(0)
        # New candle start
        _candle_current = {"open": price, "high": price, "low": price, "close": price, "time": now}
    else:
        # Current candle update
        _candle_current["high"]  = max(_candle_current["high"], price)
        _candle_current["low"]   = min(_candle_current["low"], price)
        _candle_current["close"] = price
        _candle_current["time"]  = now

def is_new_candle_closed():
    """5 min candle close hua kya? Signal sirf tab check hoga."""
    now = datetime.datetime.now()
    return len(CANDLE_5MIN) > 0 and \
           int(CANDLE_5MIN[-1]["time"].minute / 5) != int(now.minute / 5)

# ── SQLITE ──────────────────────────────────────────────
DB_PATH = "/tmp/goat_paper.db"

def db_init():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS paper_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        direction TEXT, entry_price REAL, exit_price REAL,
        target REAL, sl REAL, qty INTEGER DEFAULT 1,
        setup TEXT, source TEXT DEFAULT 'MANUAL',
        note TEXT, post_note TEXT, exit_reason TEXT,
        emotion TEXT, entry_time TEXT, exit_time TEXT,
        pnl REAL, status TEXT DEFAULT 'OPEN',
        decision_quality TEXT DEFAULT '—',
        emotion_score INTEGER DEFAULT 0,
        atm_strike INTEGER DEFAULT 0,
        option_type TEXT DEFAULT 'CE'
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS introspection (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, rule_followed INTEGER, sl_skip TEXT,
        revenge TEXT, discipline INTEGER, tomorrow_rule TEXT,
        created_at TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS decision_quality (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, dq_score INTEGER, breakdown TEXT, created_at TEXT
    )""")
    con.commit()
    con.close()

db_init()

def db_open_trade():
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY id DESC LIMIT 1").fetchone()
    con.close()
    if not row: return None
    cols = ['id','direction','entry_price','exit_price','target','sl','qty','setup','source',
            'note','post_note','exit_reason','emotion','entry_time','exit_time','pnl','status',
            'decision_quality','emotion_score','atm_strike','option_type']
    return dict(zip(cols, row))

def db_closed_trades(limit=50):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT * FROM paper_trades WHERE status='CLOSED' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    cols = ['id','direction','entry_price','exit_price','target','sl','qty','setup','source',
            'note','post_note','exit_reason','emotion','entry_time','exit_time','pnl','status',
            'decision_quality','emotion_score','atm_strike','option_type']
    return [dict(zip(cols, r)) for r in rows]

def db_insert_trade(t):
    con = sqlite3.connect(DB_PATH)
    con.execute("""INSERT INTO paper_trades
        (direction,entry_price,target,sl,qty,setup,source,note,entry_time,status,
         decision_quality,emotion_score,atm_strike,option_type)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (t['direction'], t['entry_price'], t['target'], t['sl'],
         t.get('qty',1), t['setup'], t.get('source','MANUAL'), t.get('note',''),
         t['entry_time'], 'OPEN', t.get('decision_quality','—'),
         t.get('emotion_score',0), t.get('atm_strike',0), t.get('option_type','CE')))
    con.commit()
    con.close()

def db_close_trade(trade_id, exit_price, exit_reason, post_note, emotion, pnl,
                   decision_quality='—', emotion_score=0):
    con = sqlite3.connect(DB_PATH)
    con.execute("""UPDATE paper_trades SET
        exit_price=?, exit_reason=?, post_note=?, emotion=?,
        exit_time=?, pnl=?, status='CLOSED',
        decision_quality=?, emotion_score=?
        WHERE id=?""",
        (exit_price, exit_reason, post_note, emotion,
         time.strftime("%H:%M:%S"), pnl, decision_quality, emotion_score, trade_id))
    con.commit()
    con.close()

def db_clear_trades():
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM paper_trades")
    con.commit()
    con.close()

def db_add_intro(data):
    con = sqlite3.connect(DB_PATH)
    con.execute("""INSERT INTO introspection
        (date,rule_followed,sl_skip,revenge,discipline,tomorrow_rule,created_at)
        VALUES (?,?,?,?,?,?,?)""",
        (data['date'], data['rule_followed'], data['sl_skip'],
         data['revenge'], data['discipline'], data['tomorrow_rule'],
         time.strftime("%Y-%m-%d %H:%M:%S")))
    con.commit()
    con.close()

def db_get_intros(limit=10):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT * FROM introspection ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    cols = ['id','date','rule_followed','sl_skip','revenge','discipline','tomorrow_rule','created_at']
    return [dict(zip(cols, r)) for r in rows]

def db_add_dq(data):
    con = sqlite3.connect(DB_PATH)
    con.execute("""INSERT INTO decision_quality (date,dq_score,breakdown,created_at)
        VALUES (?,?,?,?)""",
        (data['date'], data['dq_score'], data['breakdown'],
         time.strftime("%Y-%m-%d %H:%M:%S")))
    con.commit()
    con.close()

def db_get_dqs(limit=7):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute("SELECT * FROM decision_quality ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    cols = ['id','date','dq_score','breakdown','created_at']
    return [dict(zip(cols, r)) for r in rows]

def calc_dq_score(rule_followed, sl_skip, revenge, discipline):
    base = (int(rule_followed) + int(discipline)) * 10
    score = base
    penalties = []
    if sl_skip == 'Yes':  score -= 25; penalties.append("SL Skip: -25")
    if revenge  == 'Yes': score -= 25; penalties.append("Revenge Trade: -25")
    score = max(0, min(100, score))
    parts = [f"Base({rule_followed}+{discipline})x10={base}"] + penalties + [f"Final={score}"]
    return score, " | ".join(parts)

def calc_stats(trades):
    if not trades:
        return dict(total=0,wins=0,losses=0,win_rate=0,total_pnl=0,
                    avg_win=0,avg_loss=0,best=0,worst=0,expectancy=0,
                    by_setup={},by_emotion={})
    wins   = [t for t in trades if (t['pnl'] or 0) > 0]
    losses = [t for t in trades if (t['pnl'] or 0) <= 0]
    total  = len(trades)
    wr     = round(len(wins)/total*100) if total else 0
    tot_pnl  = round(sum(t['pnl'] or 0 for t in trades), 1)
    avg_win  = round(sum(t['pnl'] for t in wins)/len(wins), 1)   if wins   else 0
    avg_loss = round(sum(t['pnl'] for t in losses)/len(losses),1) if losses else 0
    best   = round(max(t['pnl'] or 0 for t in trades), 1)
    worst  = round(min(t['pnl'] or 0 for t in trades), 1)
    exp    = round((wr/100)*avg_win - ((100-wr)/100)*abs(avg_loss), 2)
    by_setup = {}
    for t in trades:
        s = t.get('setup','?')
        if s not in by_setup: by_setup[s] = {'count':0,'wins':0,'pnl':0.0}
        by_setup[s]['count'] += 1
        by_setup[s]['pnl']    = round(by_setup[s]['pnl'] + (t['pnl'] or 0), 1)
        if (t['pnl'] or 0) > 0: by_setup[s]['wins'] += 1
    for s in by_setup:
        c = by_setup[s]['count']
        by_setup[s]['win_rate'] = round(by_setup[s]['wins']/c*100) if c else 0
    by_emo = {}
    for t in trades:
        e = t.get('emotion') or '—'
        if e not in by_emo: by_emo[e] = {'count':0,'pnl':0.0}
        by_emo[e]['count'] += 1
        by_emo[e]['pnl']    = round(by_emo[e]['pnl'] + (t['pnl'] or 0), 1)
    return dict(total=total,wins=len(wins),losses=len(losses),win_rate=wr,
                total_pnl=tot_pnl,avg_win=avg_win,avg_loss=avg_loss,
                best=best,worst=worst,expectancy=exp,
                by_setup=by_setup,by_emotion=by_emo)

# ── SESSION ─────────────────────────────────────────────
SESSION_CACHE = {"obj": None, "logged_in_at": 0, "ttl_seconds": 3600}

def get_session():
    now = time.time()
    if SESSION_CACHE["obj"] and (now - SESSION_CACHE["logged_in_at"] < SESSION_CACHE["ttl_seconds"]):
        return SESSION_CACHE["obj"], None
    for k in ["ANGEL_API_KEY","ANGEL_CLIENT_ID","ANGEL_MPIN","ANGEL_TOTP_SECRET"]:
        if not os.environ.get(k): return None, "ENV MISSING"
    try:
        totp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now()
        obj  = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
        s    = obj.generateSession(os.environ["ANGEL_CLIENT_ID"], os.environ["ANGEL_MPIN"], totp)
        if not s.get("status"): return None, "LOGIN FAILED"
        SESSION_CACHE.update({"obj": obj, "logged_in_at": now})
        return obj, None
    except Exception as e:
        return None, str(e)

# ── YFINANCE FALLBACK ───────────────────────────────────
def get_nifty_yfinance():
    if not YFINANCE_AVAILABLE: return None
    try:
        ticker = yf.Ticker("^NSEI")
        price  = ticker.fast_info['last_price']
        return float(price) if price else None
    except:
        return None

def get_vix_yfinance():
    if not YFINANCE_AVAILABLE: return None
    try:
        ticker = yf.Ticker("^INDIAVIX")
        price  = ticker.fast_info['last_price']
        return float(price) if price else None
    except:
        return None

# ── TELEGRAM ────────────────────────────────────────────
def _tg(msg):
    tok  = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat: return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": msg, "parse_mode": "HTML"}, timeout=4)
    except: pass

def tg(msg):
    threading.Thread(target=_tg, args=(msg,), daemon=True).start()

# ── GOAT BRAIN — Trade Explanation ──────────────────────
def goat_brain(direction, spot, vix, vel, chk, atm_strike, option_type):
    reasons = []
    if chk[0]: reasons.append("✅ NIFTY round level ke upar hai")
    if chk[1]: reasons.append("✅ Momentum positive hai (velocity)")
    if chk[2]: reasons.append("✅ Round level se kafi door hai")
    if chk[3]: reasons.append("✅ VIX low hai — fear nahi hai market mein")
    if chk[4]: reasons.append("✅ Strike range ke andar hai")
    if direction == "LONG":
        action = f"LONG liya — {atm_strike} {option_type} buy karo"
    else:
        action = f"SHORT liya — {atm_strike} {option_type} buy karo"
    return f"{action}\n" + "\n".join(reasons) + f"\nVIX: {vix} | Speed: {vel}"

# ── ENGINE ──────────────────────────────────────────────
ENGINE = {
    "last_update": 0, "tick_ttl": 5, "payload": None,
    "last_spot": 0.0, "velocity": 0.0,
    "status": "BLOCKED",
    "direction": "LONG",
    "entry": 0.0, "target": 0.0, "sl": 0.0,
    "signal": "SYSTEM INITIALIZING...",
    "brain": "",
    "atm_strike": 0,
    "option_type": "CE",
    "session_pnl": 0.0,
    "trades_total": 0, "trades_won": 0, "trades_lost": 0,
    "last_signal_sent": "",
    "data_source": "—",
    "market_status": "CHECKING...",
    "market_msg": "",
    "last_candle_signal": 0,
}

def run_pipeline():
    global ENGINE
    now = time.time()
    if ENGINE["payload"] and (now - ENGINE["last_update"] < ENGINE["tick_ttl"]):
        return ENGINE["payload"]

    # ── Market Hours Check ──
    mstatus, mmsg = market_status()
    ENGINE["market_status"] = mstatus
    ENGINE["market_msg"]    = mmsg

    if mstatus != "OPEN":
        ENGINE["status"] = "BLOCKED"
        ENGINE["signal"] = mmsg
        payload = dict(
            spot=ENGINE.get("last_spot", 0),
            vix=15.0, velocity=0,
            status="BLOCKED", signal=mmsg,
            entry=0, target=0, sl=0,
            chk=[False]*5, pass_count=0,
            total=ENGINE["trades_total"],
            wins=ENGINE["trades_won"],
            losses=ENGINE["trades_lost"],
            pnl=round(ENGINE["session_pnl"], 1),
            win_rate=0,
            market_status=mstatus,
            market_msg=mmsg,
            atm_strike=0,
            option_type="CE",
            brain="",
            data_source="—",
            candles=CANDLE_5MIN[-20:] if CANDLE_5MIN else []
        )
        ENGINE.update({"last_update": now, "payload": payload})
        return payload

    # ── Fetch Data — Angel One Primary ──
    spot = None
    vix  = None
    data_source = "Angel One"

    obj, err = get_session()
    if obj:
        try:
            nr = obj.ltpData("NSE", "NIFTY", NIFTY_TOKEN)
            vr = obj.ltpData("NSE", "INDIAVIX", VIX_TOKEN)
            if nr.get("status") and "data" in nr:
                spot = float(nr["data"]["ltp"])
            if vr.get("status") and "data" in vr:
                vix = float(vr["data"]["ltp"])
        except:
            SESSION_CACHE["logged_in_at"] = 0

    # ── Fallback — yfinance ──
    if spot is None:
        spot = get_nifty_yfinance()
        data_source = "yfinance"
    if vix is None:
        vix = get_vix_yfinance() or 15.0

    if spot is None:
        payload = ENGINE["payload"] or {"error": "All data sources failed"}
        return payload

    ENGINE["data_source"] = data_source

    # ── Candle Update ──
    update_candle(spot)

    # ── Velocity ──
    if ENGINE["last_spot"] > 0:
        ENGINE["velocity"] = round(spot - ENGINE["last_spot"], 2)
    ENGINE["last_spot"] = spot

    vel = ENGINE["velocity"]

    # ── ATM Strike ──
    atm = get_atm_strike(spot)
    ENGINE["atm_strike"] = atm

    # ── VIX Based SL/Target ──
    vix_mult = vix / 15.0
    sl_pts   = round(40 * vix_mult, 1)
    tgt_pts  = round(90 * vix_mult, 1)

    # ── 5 Param Checklist ──
    base100 = (spot // 100) * 100
    base50  = (spot // 50)  * 50
    dist_round = abs(spot - base50)

    chk = [
        spot > base100,                        # Above round level
        vel > 0,                               # Positive momentum
        dist_round > 10,                       # Not too close to round
        vix < 18.0,                            # VIX safe
        (spot - base100) < (0.008 * spot)      # Within strike range
    ]
    all_pass   = all(chk)
    pass_count = sum(chk)

    # ── Direction — LONG ya SHORT ──
    # LONG: spot upar ja raha, vel positive
    # SHORT: spot neeche, vel negative
    if vel > 0.5 and chk[0]:
        direction   = "LONG"
        option_type = "CE"
    elif vel < -0.5 and not chk[0]:
        direction   = "SHORT"
        option_type = "PE"
    else:
        direction   = ENGINE.get("direction", "LONG")
        option_type = ENGINE.get("option_type", "CE")

    ENGINE["direction"]   = direction
    ENGINE["option_type"] = option_type

    open_t = db_open_trade()

    # ── Candle Close Pe Signal Check ──
    candle_signal_ok = is_new_candle_closed() or (now - ENGINE["last_candle_signal"] > 300)

    # ── Trade Management ──
    if ENGINE["status"] == "TRADE_ACTIVE":
        if open_t and open_t['source'] == 'AUTO':
            if direction == "LONG":
                hit_target = spot >= ENGINE["target"]
                hit_sl     = spot <= ENGINE["sl"]
            else:
                hit_target = spot <= ENGINE["target"]
                hit_sl     = spot >= ENGINE["sl"]

            if hit_target:
                pnl = round(abs(ENGINE["target"] - open_t['entry_price']), 1)
                db_close_trade(open_t['id'], spot, 'Target Hit',
                               'Auto-closed by GOAT PRO', 'Calm', pnl)
                ENGINE["trades_won"]   += 1
                ENGINE["trades_total"] += 1
                ENGINE["session_pnl"]  += pnl
                ENGINE["status"]  = "BLOCKED"
                ENGINE["signal"]  = f"🎯 TARGET HIT +{pnl} pts"
                tg(f"🎯 TARGET HIT\n+{pnl} pts at {spot}\nATM: {atm} {option_type}")

            elif hit_sl:
                pnl = round(abs(open_t['entry_price'] - ENGINE["sl"]), 1)
                db_close_trade(open_t['id'], spot, 'Stoploss Hit',
                               'Auto-closed by GOAT PRO', 'Calm', -pnl)
                ENGINE["trades_lost"]  += 1
                ENGINE["trades_total"] += 1
                ENGINE["session_pnl"]  -= pnl
                ENGINE["status"]  = "BLOCKED"
                ENGINE["signal"]  = f"🛑 SL HIT -{pnl} pts"
                tg(f"🛑 SL HIT\n-{pnl} pts at {spot}\nATM: {atm} {option_type}")

    elif ENGINE["status"] in ("BLOCKED", "SETUP_READY"):
        if not all_pass:
            ENGINE.update({
                "status": "BLOCKED",
                "signal": f"⏳ NO TRADE — {pass_count}/5 conditions met",
                "entry": 0.0, "target": 0.0, "sl": 0.0
            })
        else:
            if candle_signal_ok:
                # Proper pullback entry logic
                if direction == "LONG":
                    entry  = round(spot - 5, 2)  # Small pullback wait
                    target = round(entry + tgt_pts, 2)
                    sl     = round(entry - sl_pts, 2)
                else:
                    entry  = round(spot + 5, 2)
                    target = round(entry - tgt_pts, 2)
                    sl     = round(entry + sl_pts, 2)

                ENGINE.update({
                    "status": "SETUP_READY",
                    "entry": entry,
                    "target": target,
                    "sl": sl,
                    "signal": f"📊 SETUP READY — {direction} | ATM {atm} {option_type}"
                })
                ENGINE["last_candle_signal"] = now

            # Auto Entry — candle close confirmed signal
            trigger_long  = direction == "LONG"  and spot <= (ENGINE["entry"] + 8) and vel > 0.3
            trigger_short = direction == "SHORT" and spot >= (ENGINE["entry"] - 8) and vel < -0.3

            if (trigger_long or trigger_short) and not open_t and ENGINE["status"] == "SETUP_READY":
                sig_key = f"{direction}_{ENGINE['entry']}"
                if ENGINE["last_signal_sent"] != sig_key:
                    ENGINE["last_signal_sent"] = sig_key
                    ENGINE["status"] = "TRADE_ACTIVE"

                    brain_text = goat_brain(direction, spot, vix, vel, chk, atm, option_type)
                    ENGINE["brain"] = brain_text
                    ENGINE["signal"] = f"🚀 {direction} AUTO-ENTERED at {spot} | {atm} {option_type}"

                    db_insert_trade({
                        "direction":   direction,
                        "entry_price": spot,
                        "target":      ENGINE["target"],
                        "sl":          ENGINE["sl"],
                        "qty":         1,
                        "setup":       "GOAT Signal",
                        "source":      "AUTO",
                        "note":        brain_text,
                        "entry_time":  time.strftime("%H:%M:%S"),
                        "atm_strike":  atm,
                        "option_type": option_type
                    })
                    tg(f"🚀 AUTO {direction}\n{spot} | {atm} {option_type}\nTGT:{ENGINE['target']} SL:{ENGINE['sl']}\n{brain_text}")

    # ── Win Rate ──
    total    = ENGINE["trades_total"]
    win_rate = round(ENGINE["trades_won"] / total * 100) if total else 0

    payload = dict(
        spot=round(spot, 2),
        vix=round(vix, 2),
        velocity=vel,
        status=ENGINE["status"],
        signal=ENGINE["signal"],
        brain=ENGINE["brain"],
        entry=ENGINE["entry"],
        target=ENGINE["target"],
        sl=ENGINE["sl"],
        chk=chk,
        pass_count=pass_count,
        total=total,
        wins=ENGINE["trades_won"],
        losses=ENGINE["trades_lost"],
        pnl=round(ENGINE["session_pnl"], 1),
        win_rate=win_rate,
        market_status=mstatus,
        market_msg=mmsg,
        atm_strike=atm,
        option_type=option_type,
        direction=direction,
        data_source=data_source,
        candles=CANDLE_5MIN[-20:] if CANDLE_5MIN else [],
        current_candle=_candle_current
    )
    ENGINE.update({"last_update": now, "payload": payload})
    return payload

# ── HTML TEMPLATE ────────────────────────────────────────
TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🐐 GOAT PRO</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
<style>
  body { background: #060b16; color: #e2e8f0; font-family: 'Segoe UI', monospace; }
  .glow-green { box-shadow: 0 0 12px #22c55e55; }
  .glow-red   { box-shadow: 0 0 12px #ef444455; }
  .glow-yellow{ box-shadow: 0 0 12px #eab30855; }
  .tab-btn { cursor:pointer; padding:8px 16px; border-radius:8px; font-size:13px; transition:all 0.2s; }
  .tab-btn.active { background:#1e40af; color:white; }
  .tab-btn:not(.active) { background:#1e293b; color:#94a3b8; }
  .tab-content { display:none; }
  .tab-content.active { display:block; }
  .chk-item { display:flex; align-items:center; gap:8px; padding:6px 10px; border-radius:6px; margin:3px 0; font-size:13px; }
  .chk-pass { background:#14532d44; color:#86efac; }
  .chk-fail { background:#7f1d1d44; color:#fca5a5; }
  .stat-card { background:#0f172a; border:1px solid #1e293b; border-radius:12px; padding:16px; }
  .signal-box { border-radius:12px; padding:16px; margin:12px 0; font-size:15px; font-weight:600; }
  .brain-box  { background:#0f172a; border:1px solid #334155; border-radius:10px; padding:14px; font-size:12px; color:#94a3b8; white-space:pre-line; }
</style>
</head>
<body class="min-h-screen p-3">

<!-- HEADER -->
<div class="flex items-center justify-between mb-4">
  <div>
    <h1 class="text-2xl font-bold text-yellow-400">🐐 GOAT PRO</h1>
    <p class="text-xs text-slate-500">Virtual Trading System — Not Financial Advice</p>
  </div>
  <div class="text-right">
    <div class="text-xs text-slate-400" id="clock"></div>
    <div class="text-xs mt-1
      {% if m.market_status == 'OPEN' %}text-green-400
      {% elif m.market_status == 'PRE_OPEN' %}text-yellow-400
      {% else %}text-red-400{% endif %}">
      {{ m.market_msg }}
    </div>
    <div class="text-xs text-slate-600 mt-1">Data: {{ m.data_source }}</div>
  </div>
</div>

<!-- SPOT + VIX + ATM -->
<div class="grid grid-cols-3 gap-3 mb-4">
  <div class="stat-card text-center">
    <div class="text-xs text-slate-400 mb-1">NIFTY SPOT</div>
    <div class="text-2xl font-bold text-white" id="spot">{{ m.spot }}</div>
    <div class="text-xs mt-1 {% if m.velocity > 0 %}text-green-400{% else %}text-red-400{% endif %}">
      {{ '+' if m.velocity > 0 else '' }}{{ m.velocity }} pts/tick
    </div>
  </div>
  <div class="stat-card text-center">
    <div class="text-xs text-slate-400 mb-1">INDIA VIX</div>
    <div class="text-2xl font-bold {% if m.vix < 15 %}text-green-400{% elif m.vix < 20 %}text-yellow-400{% else %}text-red-400{% endif %}">
      {{ m.vix }}
    </div>
    <div class="text-xs mt-1 text-slate-500">
      {% if m.vix < 15 %}Low Fear{% elif m.vix < 20 %}Normal{% else %}High Fear{% endif %}
    </div>
  </div>
  <div class="stat-card text-center">
    <div class="text-xs text-slate-400 mb-1">ATM STRIKE</div>
    <div class="text-xl font-bold text-blue-400">{{ m.atm_strike }}</div>
    <div class="text-xs mt-1">
      <span class="text-green-400">CE</span> /
      <span class="text-red-400">PE</span>
    </div>
  </div>
</div>

<!-- SIGNAL BOX -->
<div class="signal-box
  {% if m.status == 'TRADE_ACTIVE' %}bg-green-900 border border-green-500 text-green-300 glow-green
  {% elif m.status == 'SETUP_READY' %}bg-yellow-900 border border-yellow-500 text-yellow-300 glow-yellow
  {% else %}bg-slate-800 border border-slate-600 text-slate-300{% endif %}">
  <div class="flex items-center gap-2">
    <span>{% if m.status == 'TRADE_ACTIVE' %}🚀{% elif m.status == 'SETUP_READY' %}📊{% else %}⏳{% endif %}</span>
    <span id="signal">{{ m.signal }}</span>
  </div>
  {% if m.status == 'TRADE_ACTIVE' %}
  <div class="grid grid-cols-3 gap-2 mt-3 text-sm">
    <div>Entry: <span class="text-white font-bold">{{ m.entry }}</span></div>
    <div>Target: <span class="text-green-400 font-bold">{{ m.target }}</span></div>
    <div>SL: <span class="text-red-400 font-bold">{{ m.sl }}</span></div>
  </div>
  <div class="mt-2 text-xs text-slate-400">
    Direction: <span class="{% if m.direction == 'LONG' %}text-green-400{% else %}text-red-400{% endif %} font-bold">
      {{ m.direction }} — {{ m.atm_strike }} {{ m.option_type }}
    </span>
  </div>
  {% endif %}
</div>

<!-- GOAT BRAIN -->
{% if m.brain %}
<div class="brain-box mb-4">
  <div class="text-yellow-400 font-bold text-xs mb-2">🧠 GOAT BRAIN — Yeh trade kyun li?</div>
  {{ m.brain }}
</div>
{% endif %}

<!-- CHECKLIST -->
<div class="stat-card mb-4">
  <div class="flex items-center justify-between mb-2">
    <span class="text-sm font-semibold text-slate-300">5-Point Checklist</span>
    <span class="text-sm font-bold {% if m.pass_count == 5 %}text-green-400{% elif m.pass_count >= 3 %}text-yellow-400{% else %}text-red-400{% endif %}">
      {{ m.pass_count }}/5
    </span>
  </div>
  {% set labels = ['NIFTY above round level', 'Velocity positive', 'Distance from round level OK', 'VIX safe (below 18)', 'Within strike range'] %}
  {% for i in range(5) %}
  <div class="chk-item {% if m.chk[i] %}chk-pass{% else %}chk-fail{% endif %}">
    <span>{% if m.chk[i] %}✅{% else %}❌{% endif %}</span>
    <span>{{ labels[i] }}</span>
  </div>
  {% endfor %}
</div>

<!-- SESSION STATS -->
<div class="grid grid-cols-4 gap-2 mb-4">
  <div class="stat-card text-center">
    <div class="text-xs text-slate-400">Trades</div>
    <div class="text-xl font-bold text-white">{{ m.total }}</div>
  </div>
  <div class="stat-card text-center">
    <div class="text-xs text-slate-400">Win Rate</div>
    <div class="text-xl font-bold {% if m.win_rate >= 60 %}text-green-400{% else %}text-yellow-400{% endif %}">{{ m.win_rate }}%</div>
  </div>
  <div class="stat-card text-center">
    <div class="text-xs text-slate-400">W/L</div>
    <div class="text-xl font-bold"><span class="text-green-400">{{ m.wins }}</span>/<span class="text-red-400">{{ m.losses }}</span></div>
  </div>
  <div class="stat-card text-center">
    <div class="text-xs text-slate-400">P&L</div>
    <div class="text-xl font-bold {% if m.pnl >= 0 %}text-green-400{% else %}text-red-400{% endif %}">
      {{ '+' if m.pnl >= 0 else '' }}{{ m.pnl }}
    </div>
  </div>
</div>

<!-- CANDLESTICK CHART -->
<div class="stat-card mb-4">
  <div class="text-sm font-semibold text-slate-300 mb-2">📊 5-Min Candle Chart</div>
  <div id="chart" style="height:200px;"></div>
</div>

<!-- TABS -->
<div class="flex gap-2 mb-4 overflow-x-auto">
  <button class="tab-btn active" onclick="switchTab('journal')">📋 Journal</button>
  <button class="tab-btn" onclick="switchTab('exit')">🚪 Exit</button>
  <button class="tab-btn" onclick="switchTab('stats')">📈 Stats</button>
  <button class="tab-btn" onclick="switchTab('intro')">🧘 Intro</button>
</div>

<!-- TAB: JOURNAL -->
<div id="tab-journal" class="tab-content active">
  {% if open_trade %}
  <div class="stat-card mb-3 border border-green-800">
    <div class="text-green-400 font-bold text-sm mb-2">🟢 OPEN TRADE</div>
    <div class="grid grid-cols-2 gap-2 text-sm">
      <div>Direction: <span class="font-bold {% if open_trade.direction == 'LONG' %}text-green-400{% else %}text-red-400{% endif %}">{{ open_trade.direction }}</span></div>
      <div>Entry: <span class="font-bold text-white">{{ open_trade.entry_price }}</span></div>
      <div>Target: <span class="text-green-400 font-bold">{{ open_trade.target }}</span></div>
      <div>SL: <span class="text-red-400 font-bold">{{ open_trade.sl }}</span></div>
      <div>Strike: <span class="text-blue-400">{{ open_trade.atm_strike }} {{ open_trade.option_type }}</span></div>
      <div>Time: <span class="text-slate-300">{{ open_trade.entry_time }}</span></div>
    </div>
    {% if open_trade.note %}
    <div class="mt-2 text-xs text-slate-400 bg-slate-900 rounded p-2">{{ open_trade.note }}</div>
    {% endif %}
  </div>
  {% endif %}

  <div class="space-y-2">
    {% for t in closed_trades[:10] %}
    <div class="stat-card border {% if t.pnl and t.pnl > 0 %}border-green-900{% else %}border-red-900{% endif %}">
      <div class="flex justify-between items-center">
        <span class="text-xs font-bold {% if t.direction == 'LONG' %}text-green-400{% else %}text-red-400{% endif %}">
          {{ t.direction }} — {{ t.atm_strike }} {{ t.option_type }}
        </span>
        <span class="text-sm font-bold {% if t.pnl and t.pnl > 0 %}text-green-400{% else %}text-red-400{% endif %}">
          {{ '+' if t.pnl and t.pnl > 0 else '' }}{{ t.pnl }} pts
        </span>
      </div>
      <div class="text-xs text-slate-500 mt-1">
        {{ t.entry_time }} → {{ t.exit_time }} | {{ t.exit_reason }}
      </div>
    </div>
    {% else %}
    <div class="text-center text-slate-500 text-sm py-8">Koi trade nahi hua abhi</div>
    {% endfor %}
  </div>
</div>

<!-- TAB: EXIT -->
<div id="tab-exit" class="tab-content">
  {% if open_trade %}
  <div class="stat-card">
    <div class="text-yellow-400 font-bold mb-3">Manual Exit — Trade #{{ open_trade.id }}</div>
    <div class="space-y-3">
      <div>
        <label class="text-xs text-slate-400">Exit Price</label>
        <input type="number" id="exit_price" value="{{ m.spot }}"
               class="w-full bg-slate-800 border border-slate-600 rounded p-2 text-white mt-1">
      </div>
      <div>
        <label class="text-xs text-slate-400">Exit Reason</label>
        <select id="exit_reason" class="w-full bg-slate-800 border border-slate-600 rounded p-2 text-white mt-1">
          <option>Target Hit</option>
          <option>SL Hit</option>
          <option>Manual Exit</option>
          <option>Time Exit</option>
          <option>Trailing SL</option>
        </select>
      </div>
      <div>
        <label class="text-xs text-slate-400">Emotion</label>
        <select id="exit_emotion" class="w-full bg-slate-800 border border-slate-600 rounded p-2 text-white mt-1">
          <option>Calm</option>
          <option>Greedy</option>
          <option>Fearful</option>
          <option>Revenge</option>
          <option>Overconfident</option>
          <option>Patient</option>
        </select>
      </div>
      <div>
        <label class="text-xs text-slate-400">Post Note</label>
        <textarea id="exit_note" rows="2"
                  class="w-full bg-slate-800 border border-slate-600 rounded p-2 text-white mt-1"
                  placeholder="Trade ke baad kya siikhaa?"></textarea>
      </div>
      <button onclick="doExit({{ open_trade.id }}, '{{ open_trade.direction }}', {{ open_trade.entry_price }})"
              class="w-full bg-red-700 hover:bg-red-600 text-white rounded p-3 font-bold">
        Exit Trade
      </button>
    </div>
  </div>
  {% else %}
  <div class="text-center text-slate-500 text-sm py-8">Koi open trade nahi hai</div>
  {% endif %}
</div>

<!-- TAB: STATS -->
<div id="tab-stats" class="tab-content">
  <div class="grid grid-cols-2 gap-3 mb-3">
    <div class="stat-card text-center">
      <div class="text-xs text-slate-400">Total Trades</div>
      <div class="text-2xl font-bold text-white">{{ stats.total }}</div>
    </div>
    <div class="stat-card text-center">
      <div class="text-xs text-slate-400">Win Rate</div>
      <div class="text-2xl font-bold {% if stats.win_rate >= 60 %}text-green-400{% else %}text-yellow-400{% endif %}">
        {{ stats.win_rate }}%
      </div>
    </div>
    <div class="stat-card text-center">
      <div class="text-xs text-slate-400">Avg Win</div>
      <div class="text-xl font-bold text-green-400">+{{ stats.avg_win }}</div>
    </div>
    <div class="stat-card text-center">
      <div class="text-xs text-slate-400">Avg Loss</div>
      <div class="text-xl font-bold text-red-400">{{ stats.avg_loss }}</div>
    </div>
    <div class="stat-card text-center">
      <div class="text-xs text-slate-400">Best Trade</div>
      <div class="text-xl font-bold text-green-400">+{{ stats.best }}</div>
    </div>
    <div class="stat-card text-center">
      <div class="text-xs text-slate-400">Expectancy</div>
      <div class="text-xl font-bold {% if stats.expectancy >= 0 %}text-green-400{% else %}text-red-400{% endif %}">
        {{ stats.expectancy }}
      </div>
    </div>
  </div>
  <div class="stat-card">
    <div class="text-sm font-semibold text-slate-300 mb-2">Total P&L</div>
    <div class="text-3xl font-bold {% if stats.total_pnl >= 0 %}text-green-400{% else %}text-red-400{% endif %}">
      {{ '+' if stats.total_pnl >= 0 else '' }}{{ stats.total_pnl }} pts
    </div>
  </div>
</div>

<!-- TAB: INTROSPECTION -->
<div id="tab-intro" class="tab-content">
  <div class="stat-card mb-3">
    <div class="text-yellow-400 font-bold mb-3">🧘 Aaj Ka Introspection</div>
    <div class="space-y-3">
      <div>
        <label class="text-xs text-slate-400">Rules follow kiye? (1-5)</label>
        <input type="range" id="rule_followed" min="1" max="5" value="3"
               class="w-full mt-1" oninput="document.getElementById('rf_val').textContent=this.value">
        <span id="rf_val" class="text-yellow-400 text-sm">3</span>
      </div>
      <div>
        <label class="text-xs text-slate-400">SL skip kiya?</label>
        <select id="sl_skip" class="w-full bg-slate-800 border border-slate-600 rounded p-2 text-white mt-1">
          <option>No</option><option>Yes</option>
        </select>
      </div>
      <div>
        <label class="text-xs text-slate-400">Revenge trade?</label>
        <select id="revenge" class="w-full bg-slate-800 border border-slate-600 rounded p-2 text-white mt-1">
          <option>No</option><option>Yes</option>
        </select>
      </div>
      <div>
        <label class="text-xs text-slate-400">Discipline score (1-5)</label>
        <input type="range" id="discipline" min="1" max="5" value="3"
               class="w-full mt-1" oninput="document.getElementById('dis_val').textContent=this.value">
        <span id="dis_val" class="text-yellow-400 text-sm">3</span>
      </div>
      <div>
        <label class="text-xs text-slate-400">Kal ke liye rule</label>
        <textarea id="tomorrow_rule" rows="2"
                  class="w-full bg-slate-800 border border-slate-600 rounded p-2 text-white mt-1"
                  placeholder="Kal kya better karunga..."></textarea>
      </div>
      <button onclick="saveIntro()"
              class="w-full bg-blue-700 hover:bg-blue-600 text-white rounded p-3 font-bold">
        Save Introspection
      </button>
      <div id="intro_result" class="text-center text-sm text-green-400 hidden"></div>
    </div>
  </div>

  {% if latest_dq %}
  <div class="stat-card border border-blue-900">
    <div class="text-blue-400 font-bold mb-1">Last DQ Score</div>
    <div class="text-3xl font-bold {% if latest_dq.dq_score >= 70 %}text-green-400{% elif latest_dq.dq_score >= 40 %}text-yellow-400{% else %}text-red-400{% endif %}">
      {{ latest_dq.dq_score }}/100
    </div>
    <div class="text-xs text-slate-500 mt-1">{{ latest_dq.breakdown }}</div>
  </div>
  {% endif %}
</div>

<!-- FOOTER -->
<div class="text-center text-xs text-slate-700 mt-6 pb-4">
  🐐 GOAT PRO — Virtual Paper Trading System<br>
  Not financial advice. Trade at your own risk.
</div>

<script>
// Clock
function updateClock() {
  const now = new Date();
  document.getElementById('clock').textContent =
    now.toLocaleTimeString('en-IN', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
}
setInterval(updateClock, 1000);
updateClock();

// Tabs
function switchTab(name) {
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.target.classList.add('active');
}

// Auto refresh every 5 sec
setInterval(() => location.reload(), 5000);

// Candle Chart
const chartEl = document.getElementById('chart');
if (chartEl) {
  const chart = LightweightCharts.createChart(chartEl, {
    width: chartEl.clientWidth,
    height: 200,
    layout: { background: { color: '#0f172a' }, textColor: '#94a3b8' },
    grid: { vertLines: { color: '#1e293b' }, horzLines: { color: '#1e293b' } },
    timeScale: { borderColor: '#334155' },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal }
  });
  const candleSeries = chart.addCandlestickSeries({
    upColor: '#22c55e', downColor: '#ef4444',
    borderUpColor: '#22c55e', borderDownColor: '#ef4444',
    wickUpColor: '#22c55e', wickDownColor: '#ef4444'
  });

  const candles = {{ m.candles | tojson }};
  if (candles && candles.length > 0) {
    const data = candles.map((c, i) => ({
      time: i + 1,
      open: c.open, high: c.high,
      low: c.low, close: c.close
    }));
    candleSeries.setData(data);
  }
}

// Exit Trade
function doExit(tradeId, direction, entryPrice) {
  const exitPrice = parseFloat(document.getElementById('exit_price').value);
  const pnl = direction === 'LONG' ? exitPrice - entryPrice : entryPrice - exitPrice;
  fetch('/paper/exit', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      trade_id: tradeId,
      exit_price: exitPrice,
      direction: direction,
      entry_price: entryPrice,
      exit_reason: document.getElementById('exit_reason').value,
      post_note: document.getElementById('exit_note').value,
      emotion: document.getElementById('exit_emotion').value
    })
  }).then(() => location.reload());
}

// Introspection
function saveIntro() {
  fetch('/paper/intro', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      rule_followed: document.getElementById('rule_followed').value,
      sl_skip: document.getElementById('sl_skip').value,
      revenge: document.getElementById('revenge').value,
      discipline: document.getElementById('discipline').value,
      tomorrow_rule: document.getElementById('tomorrow_rule').value
    })
  }).then(r => r.json()).then(d => {
    const el = document.getElementById('intro_result');
    el.textContent = 'DQ Score: ' + d.dq_score + '/100 — ' + d.breakdown;
    el.classList.remove('hidden');
  });
}
</script>
</body>
</html>"""

# ── ROUTES ──────────────────────────────────────────────
@app.route("/")
def index():
    data = run_pipeline()
    if "error" in data:
        return (f"<div style='background:#060b16;color:#f87171;font-family:monospace;"
                f"padding:40px;min-height:100vh;display:flex;flex-direction:column;"
                f"justify-content:center;align-items:center;text-align:center'>"
                f"<h2>ENGINE HALTED</h2>"
                f"<p style='color:#94a3b8;margin-top:12px'>{data['error']}</p>"
                f"<p style='color:#334155;font-size:11px;margin-top:20px'>"
                f"Check Render Environment Variables</p></div>")
    closed = db_closed_trades()
    return render_template_string(
        TEMPLATE,
        m=data,
        open_trade=db_open_trade(),
        closed_trades=closed,
        stats=calc_stats(closed),
        intros=db_get_intros(5),
        latest_dq=(db_get_dqs(1) or [None])[0],
        dq_history=db_get_dqs(7)
    )

@app.route("/ping")
def ping():
    """UptimeRobot is point karo — server + pipeline dono alive rahenge."""
    data = run_pipeline()
    return jsonify({
        "status": "alive",
        "spot": data.get("spot", 0),
        "market": data.get("market_status", "—"),
        "signal": data.get("signal", "—"),
        "time": time.strftime("%H:%M:%S")
    })

@app.route("/api/data")
def api_data():
    return jsonify(run_pipeline())

@app.route("/paper/exit", methods=["POST"])
def paper_exit():
    d     = request.get_json()
    tid   = d.get("trade_id")
    ep    = float(d.get("exit_price", 0))
    dir_  = d.get("direction", "LONG")
    enp   = float(d.get("entry_price", 0))
    pnl   = round((ep - enp) if dir_ == "LONG" else (enp - ep), 2)
    db_close_trade(tid, ep, d.get("exit_reason", ""), d.get("post_note", ""),
                   d.get("emotion", ""), pnl)
    if pnl > 0: ENGINE["trades_won"]  += 1
    else:       ENGINE["trades_lost"] += 1
    ENGINE["trades_total"] += 1
    ENGINE["session_pnl"]   = round(ENGINE["session_pnl"] + pnl, 1)
    return jsonify({"status": "ok", "pnl": pnl})

@app.route("/paper/intro", methods=["POST"])
def paper_intro():
    d     = request.get_json()
    today = time.strftime("%d-%m-%Y")
    db_add_intro({**d, "date": today})
    dq_score, breakdown = calc_dq_score(
        d.get("rule_followed", 3), d.get("sl_skip", "No"),
        d.get("revenge", "No"),    d.get("discipline", 3)
    )
    db_add_dq({"date": today, "dq_score": dq_score, "breakdown": breakdown})
    return jsonify({"status": "ok", "dq_score": dq_score, "breakdown": breakdown})

@app.route("/paper/clear", methods=["POST"])
def paper_clear():
    db_clear_trades()
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
