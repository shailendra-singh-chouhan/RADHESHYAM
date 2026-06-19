# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
  GOAT PRO — Virtual Paper Trading System
  Single-file Flask app for Render deployment.
"""

import os
import time
import datetime
import threading
import sqlite3
try:
    import psycopg2
    import psycopg2.extras
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False
import json
import requests
import pyotp
from flask import Flask, render_template_string, request, jsonify

from SmartApi import SmartConnect

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

app = Flask(__name__)

# ═══════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════

TOKENS = {
    "NIFTY":     {"token": "99926000", "exchange": "NSE"},
    "BANKNIFTY": {"token": "99926009", "exchange": "NSE"},
    "FINNIFTY":  {"token": "99926037", "exchange": "NSE"},
    "SENSEX":    {"token": "99919000", "exchange": "BSE"},
    "VIX":       {"token": "99926017", "exchange": "NSE"},
}

ATM_INTERVALS = {
    "NIFTY":     50,
    "BANKNIFTY": 100,
    "FINNIFTY":  50,
    "SENSEX":    100,
}

LOT_SIZES = {
    "NIFTY":     65,
    "BANKNIFTY": 30,
    "FINNIFTY":  60,
}

# ── TRADE LIMITS ──
MAX_TRADES_PER_DAY   = 8          # max auto trades per day
TRADE_COOLDOWN_SECS  = 180        # 3 min gap between trades
MIN_CHECKLIST_PASS   = 3          # trade if 3/5 pass (relaxed from 5/5)

# ── DB ──
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_PATH = "/opt/render/project/src/trades.db"
USE_POSTGRES = bool(DATABASE_URL and POSTGRES_AVAILABLE)
if not USE_POSTGRES:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def get_db_conn():
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        return conn, "postgres"
    else:
        conn = sqlite3.connect(DB_PATH)
        return conn, "sqlite"

# ── Candle tracking ──
CANDLE_5MIN = []
_candle_current = {"open": 0, "high": 0, "low": 0, "close": 0, "time": None}

# ── ORB (Opening Range Breakout) ──
ORB = {
    "high": 0.0,
    "low":  0.0,
    "set":  False,
    "date": "",
    "candles_captured": 0,
}

# ═══════════════════════════════════════════════
# MARKET STATUS
# ═══════════════════════════════════════════════

def market_status():
    now = datetime.datetime.now()
    weekday = now.weekday()
    t = now.time()
    if weekday >= 5:
        return "CLOSED", "Weekend — Market Closed"
    if t < datetime.time(9, 15):
        return "CLOSED", f"Market opens at 9:15 AM"
    if t > datetime.time(15, 30):
        return "CLOSED", "Market Closed after 3:30 PM"
    return "OPEN", "Market Open"

def get_session_name():
    now = datetime.datetime.now()
    t = now.time()
    if datetime.time(9,0) <= t < datetime.time(9,15):   return "PRE-OPEN"
    if datetime.time(9,15) <= t < datetime.time(11,0):  return "MORNING"
    if datetime.time(11,0) <= t < datetime.time(14,0):  return "MIDDAY"
    if datetime.time(14,0) <= t <= datetime.time(15,30): return "POWER_HOUR"
    return "CLOSED"

def get_atm_strike(spot, interval=50):
    return int(round(spot / interval) * interval)

def get_expiry_label():
    today = datetime.date.today()
    days_to_thu = (3 - today.weekday()) % 7
    if days_to_thu == 0:
        if datetime.datetime.now().time() > datetime.time(15, 30):
            days_to_thu = 7
    expiry = today + datetime.timedelta(days=days_to_thu)
    return expiry.strftime("%d %b")

# ═══════════════════════════════════════════════
# CANDLE BUILDER
# ═══════════════════════════════════════════════

def update_candle(price):
    global _candle_current, CANDLE_5MIN
    now = datetime.datetime.now()
    if _candle_current["time"] is None:
        _candle_current = {"open": price, "high": price, "low": price, "close": price, "time": now.isoformat()}
        return
    prev_slot = int(datetime.datetime.fromisoformat(_candle_current["time"]).minute / 5)
    curr_slot = int(now.minute / 5)
    if curr_slot != prev_slot:
        CANDLE_5MIN.append(dict(_candle_current))
        if len(CANDLE_5MIN) > 100:
            CANDLE_5MIN.pop(0)
        _candle_current = {"open": price, "high": price, "low": price, "close": price, "time": now.isoformat()}
    else:
        _candle_current["high"] = max(_candle_current["high"], price)
        _candle_current["low"]  = min(_candle_current["low"], price)
        _candle_current["close"] = price

# ═══════════════════════════════════════════════
# ORB — Opening Range Breakout (first 15 min)
# ═══════════════════════════════════════════════

def update_orb(spot, now_time):
    """Build ORB from 9:15–9:30 candles"""
    global ORB
    today_str = datetime.date.today().isoformat()

    # Reset ORB on new day
    if ORB["date"] != today_str:
        ORB = {"high": 0.0, "low": 0.0, "set": False, "date": today_str, "candles_captured": 0}

    # Capture range in first 15 minutes
    if datetime.time(9, 15) <= now_time <= datetime.time(9, 30):
        if ORB["high"] == 0.0:
            ORB["high"] = spot
            ORB["low"]  = spot
        else:
            ORB["high"] = max(ORB["high"], spot)
            ORB["low"]  = min(ORB["low"], spot)
        ORB["set"] = True

def get_orb_signal(spot):
    """Returns: 'LONG', 'SHORT', or None"""
    if not ORB["set"] or ORB["high"] == 0.0:
        return None
    buffer = (ORB["high"] - ORB["low"]) * 0.1  # 10% buffer to avoid fakeout
    if spot > ORB["high"] + buffer:
        return "LONG"
    if spot < ORB["low"] - buffer:
        return "SHORT"
    return None

# ═══════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════

def db_init():
    conn, db_type = get_db_conn()
    cur = conn.cursor()
    serial = "SERIAL PRIMARY KEY" if db_type == "postgres" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    cur.execute(f"""CREATE TABLE IF NOT EXISTS paper_trades (
        id {serial},
        direction TEXT, entry_price REAL, exit_price REAL,
        target REAL, sl REAL, qty INTEGER DEFAULT 1,
        setup TEXT, source TEXT DEFAULT 'MANUAL',
        note TEXT, post_note TEXT, exit_reason TEXT,
        emotion TEXT, entry_time TEXT, exit_time TEXT,
        pnl REAL, status TEXT DEFAULT 'OPEN',
        decision_quality TEXT DEFAULT '-',
        emotion_score INTEGER DEFAULT 0,
        atm_strike INTEGER DEFAULT 0,
        option_type TEXT DEFAULT 'CE',
        pnl_rs REAL DEFAULT 0,
        strategy TEXT DEFAULT 'ORB'
    )""")
    cur.execute(f"""CREATE TABLE IF NOT EXISTS introspection (
        id {serial},
        date TEXT, rule_followed INTEGER, sl_skip TEXT,
        revenge TEXT, discipline INTEGER, tomorrow_rule TEXT, created_at TEXT
    )""")
    cur.execute(f"""CREATE TABLE IF NOT EXISTS decision_quality (
        id {serial},
        date TEXT, dq_score INTEGER, breakdown TEXT, created_at TEXT
    )""")
    conn.commit()
    cur.close(); conn.close()

def db_open_trade():
    conn, _ = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row: return None
    cols = ['id','direction','entry_price','exit_price','target','sl','qty','setup',
            'source','note','post_note','exit_reason','emotion','entry_time',
            'exit_time','pnl','status','decision_quality','emotion_score',
            'atm_strike','option_type','pnl_rs','strategy']
    return dict(zip(cols, row))

def db_closed_trades(limit=50):
    conn, _ = get_db_conn()
    cur = conn.cursor()
    ph = "%s" if USE_POSTGRES else "?"
    cur.execute(f"SELECT * FROM paper_trades WHERE status='CLOSED' ORDER BY id DESC LIMIT {ph}", (limit,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    cols = ['id','direction','entry_price','exit_price','target','sl','qty','setup',
            'source','note','post_note','exit_reason','emotion','entry_time',
            'exit_time','pnl','status','decision_quality','emotion_score',
            'atm_strike','option_type','pnl_rs','strategy']
    return [dict(zip(cols, r)) for r in rows]

def db_count_today():
    conn, _ = get_db_conn()
    cur = conn.cursor()
    today = datetime.date.today().isoformat()
    ph = "%s" if USE_POSTGRES else "?"
    cur.execute(f"SELECT COUNT(*) FROM paper_trades WHERE entry_time >= {ph} AND source='AUTO'", (today,))
    count = cur.fetchone()[0]
    cur.close(); conn.close()
    return count

def db_insert_trade(t):
    conn, db_type = get_db_conn()
    cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    sql = f"""INSERT INTO paper_trades
        (direction,entry_price,target,sl,qty,setup,source,note,entry_time,status,
         decision_quality,emotion_score,atm_strike,option_type,pnl_rs,strategy)
        VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})"""
    cur.execute(sql, (
        t['direction'], t['entry_price'], t['target'], t['sl'], t.get('qty', 65),
        t['setup'], t.get('source','MANUAL'), t.get('note',''),
        t['entry_time'], 'OPEN', t.get('decision_quality','-'),
        t.get('emotion_score',0), t.get('atm_strike',0),
        t.get('option_type','CE'), t.get('pnl_rs',0),
        t.get('strategy','ORB')
    ))
    conn.commit()
    cur.close(); conn.close()

def db_close_trade(trade_id, exit_price, exit_reason, post_note, emotion, pnl,
                   decision_quality='-', emotion_score=0, pnl_rs=0):
    conn, db_type = get_db_conn()
    cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    sql = f"""UPDATE paper_trades SET exit_price={ph},exit_reason={ph},post_note={ph},
        emotion={ph},exit_time={ph},pnl={ph},pnl_rs={ph},status='CLOSED',
        decision_quality={ph},emotion_score={ph} WHERE id={ph}"""
    cur.execute(sql, (exit_price, exit_reason, post_note, emotion,
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        pnl, pnl_rs, decision_quality, emotion_score, trade_id))
    conn.commit()
    cur.close(); conn.close()

def db_clear_trades():
    conn, _ = get_db_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM paper_trades")
    conn.commit()
    cur.close(); conn.close()

def db_add_intro(data):
    conn, db_type = get_db_conn()
    cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    sql = f"INSERT INTO introspection (date,rule_followed,sl_skip,revenge,discipline,tomorrow_rule,created_at) VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})"
    cur.execute(sql, (data['date'],data['rule_followed'],data['sl_skip'],data['revenge'],
        data['discipline'],data['tomorrow_rule'],datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    cur.close(); conn.close()

def db_get_intros(limit=10):
    conn, db_type = get_db_conn()
    cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    cur.execute(f"SELECT * FROM introspection ORDER BY id DESC LIMIT {ph}", (limit,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    cols = ['id','date','rule_followed','sl_skip','revenge','discipline','tomorrow_rule','created_at']
    return [dict(zip(cols, r)) for r in rows]

def db_add_dq(data):
    conn, db_type = get_db_conn()
    cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    cur.execute(f"INSERT INTO decision_quality (date,dq_score,breakdown,created_at) VALUES ({ph},{ph},{ph},{ph})",
        (data['date'],data['dq_score'],data['breakdown'],datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    cur.close(); conn.close()

def db_get_dqs(limit=7):
    conn, db_type = get_db_conn()
    cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    cur.execute(f"SELECT * FROM decision_quality ORDER BY id DESC LIMIT {ph}", (limit,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    cols = ['id','date','dq_score','breakdown','created_at']
    return [dict(zip(cols, r)) for r in rows]

def calc_dq_score(rule_followed, sl_skip, revenge, discipline):
    base = (int(rule_followed) + int(discipline)) * 10
    score = base
    penalties = []
    if sl_skip == 'Yes': score -= 25; penalties.append("SL Skip: -25")
    if revenge == 'Yes':  score -= 25; penalties.append("Revenge: -25")
    score = max(0, min(100, score))
    parts = [f"Base={base}"] + penalties + [f"Final={score}"]
    return score, " | ".join(parts)

def calc_stats(trades):
    if not trades:
        return {'total':0,'wins':0,'losses':0,'win_rate':0,'total_pnl':0,
                'avg_win':0,'avg_loss':0,'best':0,'worst':0,'expectancy':0}
    wins   = [t for t in trades if (t['pnl'] or 0) > 0]
    losses = [t for t in trades if (t['pnl'] or 0) <= 0]
    total  = len(trades)
    wr     = round(len(wins)/total*100) if total else 0
    tot_pnl = round(sum(t['pnl'] or 0 for t in trades), 1)
    avg_win  = round(sum(t['pnl'] for t in wins)/len(wins), 1) if wins else 0
    avg_loss = round(sum(t['pnl'] for t in losses)/len(losses), 1) if losses else 0
    best  = round(max(t['pnl'] or 0 for t in trades), 1)
    worst = round(min(t['pnl'] or 0 for t in trades), 1)
    exp   = round((wr/100)*avg_win - ((100-wr)/100)*abs(avg_loss), 2)
    return {'total':total,'wins':len(wins),'losses':len(losses),
            'win_rate':wr,'total_pnl':tot_pnl,'avg_win':avg_win,
            'avg_loss':avg_loss,'best':best,'worst':worst,'expectancy':exp}

db_init()

# ═══════════════════════════════════════════════
# ANGEL ONE SESSION
# ═══════════════════════════════════════════════

SESSION_CACHE = {"obj": None, "logged_in_at": 0, "ttl_seconds": 3600}

def get_session():
    now = time.time()
    if SESSION_CACHE["obj"] and (now - SESSION_CACHE["logged_in_at"] < SESSION_CACHE["ttl_seconds"]):
        return SESSION_CACHE["obj"], None
    for key in ["ANGEL_API_KEY","ANGEL_CLIENT_ID","ANGEL_MPIN","ANGEL_TOTP_SECRET"]:
        if not os.environ.get(key):
            return None, f"ENV MISSING: {key}"
    try:
        totp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now()
        obj  = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
        s    = obj.generateSession(os.environ["ANGEL_CLIENT_ID"], os.environ["ANGEL_MPIN"], totp)
        if not s.get("status"):
            return None, "LOGIN FAILED"
        SESSION_CACHE.update({"obj": obj, "logged_in_at": now})
        return obj, None
    except Exception as e:
        return None, str(e)

# ═══════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════

def _tg(msg):
    tok  = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat: return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": chat, "text": msg, "parse_mode": "HTML"}, timeout=4)
    except Exception:
        pass

def tg(msg):
    threading.Thread(target=_tg, args=(msg,), daemon=True).start()

# ═══════════════════════════════════════════════
# TECHNICAL INDICATORS
# ═══════════════════════════════════════════════

_price_history = []

def update_price_history(price):
    global _price_history
    _price_history.append(price)
    if len(_price_history) > 100:
        _price_history.pop(0)

def calc_ema(prices, period):
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return round(ema, 2)

def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0: return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)

def calc_vwap(candles):
    """Simple VWAP from 5-min candles (using typical price, no volume)"""
    if not candles: return 0.0
    tp_sum = sum((c['high'] + c['low'] + c['close']) / 3 for c in candles)
    return round(tp_sum / len(candles), 2)

def detect_market_condition(prices, vix, vel):
    if vix > 20: return "VOLATILE"
    if len(prices) < 10: return "UNKNOWN"
    ema9  = calc_ema(prices, 9)
    ema21 = calc_ema(prices, 21) if len(prices) >= 21 else ema9
    price = prices[-1]
    if ema9 > ema21 and price > ema9 and vel > 0: return "TRENDING_UP"
    if ema9 < ema21 and price < ema9 and vel < 0: return "TRENDING_DOWN"
    return "SIDEWAYS"

# ═══════════════════════════════════════════════
# STRATEGY ENGINE
# ═══════════════════════════════════════════════

def build_checklist(spot, vel, vix, ema9, ema21, rsi, orb_signal, vwap):
    """
    5-point relaxed checklist.
    Trade fires when MIN_CHECKLIST_PASS (3) out of 5 pass.
    """
    chk = [
        vel > 0.3 or vel < -0.3,                       # 0: Momentum active
        vix < 20.0,                                     # 1: VIX not extreme
        ema9 > 0 and ema21 > 0 and abs(ema9-ema21) > 5, # 2: EMA spread (trend exists)
        35 <= rsi <= 70,                                # 3: RSI not overbought/oversold
        orb_signal is not None,                         # 4: ORB breakout detected
    ]
    labels = [
        "Momentum active (vel > 0.3)",
        "VIX safe (< 20)",
        "EMA trend exists (spread > 5)",
        "RSI in range (35–70)",
        "ORB breakout confirmed",
    ]
    return chk, labels

def determine_direction(spot, vel, ema9, ema21, rsi, orb_signal):
    """
    Direction logic — ORB primary, then trend confirmation.
    Returns: ('LONG','CE') or ('SHORT','PE')
    """
    # ORB breakout takes priority
    if orb_signal == "LONG":
        return "LONG", "CE"
    if orb_signal == "SHORT":
        return "SHORT", "PE"
    # Trend fallback
    if vel > 0.5 and ema9 > ema21 and rsi < 65:
        return "LONG", "CE"
    if vel < -0.5 and ema9 < ema21 and rsi > 40:
        return "SHORT", "PE"
    return None, None

def calc_sl_target(spot, direction, vix, atr_pts=None):
    """VIX-adjusted SL and Target"""
    vix_mult = max(0.8, min(2.0, vix / 15.0))
    sl_pts   = round(35 * vix_mult, 1)
    tgt_pts  = round(80 * vix_mult, 1)
    if direction == "LONG":
        sl     = round(spot - sl_pts, 2)
        target = round(spot + tgt_pts, 2)
    else:
        sl     = round(spot + sl_pts, 2)
        target = round(spot - tgt_pts, 2)
    return sl, target, sl_pts, tgt_pts

def build_brain_reasons(direction, spot, vix, vel, chk, chk_labels, atm, option_type, orb, vwap, rsi, ema9, ema21):
    reasons = []
    reasons.append(f"Direction: {direction} → {atm} {option_type}")
    if orb["set"]:
        reasons.append(f"ORB Range: {orb['high']:.0f} – {orb['low']:.0f} | Breakout: {'UP' if direction=='LONG' else 'DOWN'}")
    if vwap > 0:
        side = "above" if spot > vwap else "below"
        reasons.append(f"Price {spot} is {side} VWAP {vwap:.0f}")
    reasons.append(f"EMA9={ema9:.0f} {'>' if ema9>ema21 else '<'} EMA21={ema21:.0f} | RSI={rsi:.1f}")
    reasons.append(f"VIX={vix:.1f} | Velocity={vel:+.2f}")
    passed = [chk_labels[i] for i,v in enumerate(chk) if v]
    reasons.append(f"Checklist passed: {', '.join(passed)}")
    return reasons

# ═══════════════════════════════════════════════
# ENGINE STATE
# ═══════════════════════════════════════════════

ENGINE = {
    "last_update": 0, "tick_ttl": 5, "payload": None,
    "last_spot": 0.0, "velocity": 0.0,
    "status": "BLOCKED", "direction": "LONG", "option_type": "CE",
    "entry": 0.0, "target": 0.0, "sl": 0.0,
    "signal": "SYSTEM INITIALIZING...", "brain_reasons": [],
    "atm_strike": 0, "session_pnl": 0.0,
    "trades_total": 0, "trades_won": 0, "trades_lost": 0,
    "last_signal_sent": "", "data_source": "—",
    "last_candle_signal": 0,
    "ce_premium": 0.0, "pe_premium": 0.0, "option_entry_premium": 0.0,
    "lot_size": 65, "active_index": "NIFTY", "session_pnl_rs": 0.0,
    "trades_today": 0, "last_trade_time": 0, "last_trade_date": "",
    "market_condition": "UNKNOWN", "strategy_mode": "ORB",
    "opening_gap": 0.0, "prev_close": 0.0, "today_open": 0.0, "opening_done": False,
    "ema9": 0.0, "ema21": 0.0, "rsi": 50.0, "vwap": 0.0,
    "bn_spot": 0.0, "fn_spot": 0.0, "sx_spot": 0.0,
    "day_high": 0.0, "day_low": 0.0, "day_open": 0.0,
}

# ═══════════════════════════════════════════════
# MARKET DATA FETCHERS
# ═══════════════════════════════════════════════

def fetch_spot(index_name, exchange, token, symbol):
    try:
        obj, err = get_session()
        if err or not obj: return None
        resp = obj.ltpData(exchange, symbol, token)
        if resp and resp.get("data"):
            return float(resp["data"]["ltp"])
    except Exception:
        SESSION_CACHE["logged_in_at"] = 0
    return None

def get_nifty_yfinance():
    if not YFINANCE_AVAILABLE: return None
    try:
        t = yf.Ticker("^NSEI")
        return float(t.fast_info['last_price'])
    except Exception:
        return None

def get_vix_yfinance():
    if not YFINANCE_AVAILABLE: return None
    try:
        t = yf.Ticker("^INDIAVIX")
        return float(t.fast_info['last_price'])
    except Exception:
        return None

# ═══════════════════════════════════════════════
# MAIN PIPELINE
# ═══════════════════════════════════════════════

def run_pipeline():
    global ENGINE

    now = time.time()
    if ENGINE["payload"] and (now - ENGINE["last_update"] < ENGINE["tick_ttl"]):
        return ENGINE["payload"]

    mstatus, mmsg = market_status()
    expiry_label  = get_expiry_label()
    now_dt        = datetime.datetime.now()
    now_time      = now_dt.time()

    # ── Market Closed ──
    if mstatus != "OPEN":
        ENGINE["status"] = "BLOCKED"
        ENGINE["signal"] = mmsg
        payload = _build_closed_payload(mstatus, mmsg, expiry_label)
        ENGINE.update({"last_update": now, "payload": payload})
        return payload

    # ── Fetch NIFTY spot ──
    spot = fetch_spot("NIFTY", "NSE", TOKENS["NIFTY"]["token"], "NIFTY")
    vix  = fetch_spot("VIX",   "NSE", TOKENS["VIX"]["token"],   "INDIAVIX")
    data_source = "Angel One"

    if spot is None:
        spot = get_nifty_yfinance()
        data_source = "yfinance" if spot else "—"
    if vix is None:
        vix = get_vix_yfinance() or 15.0
    if spot is None:
        return ENGINE["payload"] or {"error": "All data sources failed"}

    ENGINE["data_source"] = data_source

    # ── Day OHLC tracking ──
    if ENGINE["day_open"] == 0.0:
        ENGINE["day_open"] = spot
    ENGINE["day_high"] = max(ENGINE.get("day_high", spot), spot)
    ENGINE["day_low"]  = min(ENGINE.get("day_low", spot) or spot, spot)
    if ENGINE["day_low"] == 0.0:
        ENGINE["day_low"] = spot

    # ── Candle + ORB ──
    update_candle(spot)
    update_price_history(spot)
    update_orb(spot, now_time)

    # ── Mini markets ──
    _fetch_mini_markets(vix)

    # ── Velocity ──
    if ENGINE["last_spot"] > 0:
        ENGINE["velocity"] = round(spot - ENGINE["last_spot"], 2)
    ENGINE["last_spot"] = spot
    vel = ENGINE["velocity"]

    # ── Indicators ──
    ema9  = calc_ema(_price_history, 9)  if len(_price_history) >= 9  else 0.0
    ema21 = calc_ema(_price_history, 21) if len(_price_history) >= 21 else 0.0
    rsi   = calc_rsi(_price_history)     if len(_price_history) >= 15 else 50.0
    vwap  = calc_vwap(CANDLE_5MIN)
    ENGINE.update({"ema9": ema9, "ema21": ema21, "rsi": rsi, "vwap": vwap})

    # ── Market condition ──
    condition = detect_market_condition(_price_history, vix, vel)
    ENGINE["market_condition"] = condition

    # ── ORB signal ──
    orb_signal = get_orb_signal(spot)

    # ── Checklist ──
    chk, chk_labels = build_checklist(spot, vel, vix, ema9, ema21, rsi, orb_signal, vwap)
    pass_count = sum(chk)

    # ── ATM ──
    atm = get_atm_strike(spot, interval=50)
    ENGINE["atm_strike"] = atm

    # ── Lot size ──
    active_index = ENGINE.get("active_index", "NIFTY")
    lot_size = LOT_SIZES.get(active_index, 65)
    ENGINE["lot_size"] = lot_size

    # ── Opening gap (once per day) ──
    if not ENGINE.get("opening_done"):
        try:
            ticker = yf.Ticker("^NSEI")
            hist   = ticker.history(period="2d")
            if len(hist) >= 2:
                ENGINE["prev_close"]    = float(hist['Close'].iloc[-2])
                ENGINE["today_open"]    = float(hist['Open'].iloc[-1])
                ENGINE["opening_gap"]   = round(spot - ENGINE["prev_close"], 2)
                ENGINE["opening_done"]  = True
        except Exception:
            pass

    # ── Direction ──
    direction, option_type = determine_direction(spot, vel, ema9, ema21, rsi, orb_signal)

    # ── Trade management ──
    open_t = db_open_trade()
    today_str = datetime.date.today().isoformat()

    # Reset daily count
    if ENGINE["last_trade_date"] != today_str:
        ENGINE["trades_today"]    = 0
        ENGINE["last_trade_date"] = today_str
        ENGINE["day_high"]  = spot
        ENGINE["day_low"]   = spot
        ENGINE["day_open"]  = spot

    cooldown_ok   = (now - ENGINE["last_trade_time"]) > TRADE_COOLDOWN_SECS
    daily_limit_ok = ENGINE["trades_today"] < MAX_TRADES_PER_DAY
    # Don't trade in last 15 min
    power_ok = now_time < datetime.time(15, 15)

    # ── Manage active trade ──
    if ENGINE["status"] == "TRADE_ACTIVE" and open_t and open_t['source'] == 'AUTO':
        _manage_active_trade(open_t, spot, lot_size)

    # ── Entry logic ──
    elif ENGINE["status"] in ("BLOCKED", "SETUP_READY"):
        if pass_count >= MIN_CHECKLIST_PASS and direction and cooldown_ok and daily_limit_ok and not open_t and power_ok:
            sl, target, sl_pts, tgt_pts = calc_sl_target(spot, direction, vix)
            brain = build_brain_reasons(direction, spot, vix, vel, chk, chk_labels, atm, option_type, ORB, vwap, rsi, ema9, ema21)

            sig_key = f"{direction}_{atm}_{int(spot)}"
            if ENGINE["last_signal_sent"] != sig_key:
                ENGINE.update({
                    "status":    "TRADE_ACTIVE",
                    "direction": direction,
                    "option_type": option_type,
                    "entry":     spot,
                    "target":    target,
                    "sl":        sl,
                    "signal":    f"AUTO {direction} | {atm} {option_type} | ORB {'UP' if orb_signal=='LONG' else 'DN' if orb_signal else ''}",
                    "brain_reasons": brain,
                    "last_signal_sent": sig_key,
                    "last_trade_time":  now,
                })
                ENGINE["trades_today"] += 1

                setup_name = f"ORB-{orb_signal or 'TREND'}"
                db_insert_trade({
                    "direction":   direction,
                    "entry_price": spot,
                    "target":      target,
                    "sl":          sl,
                    "qty":         lot_size,
                    "setup":       setup_name,
                    "source":      "AUTO",
                    "note":        "\n".join(brain),
                    "entry_time":  datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "atm_strike":  atm,
                    "option_type": option_type,
                    "strategy":    setup_name,
                })
                tg(f"🚀 AUTO {direction}\nSpot:{spot} | ATM:{atm} {option_type}\nTGT:{target} | SL:{sl}\nSetup:{setup_name} | Lot:{lot_size}")

        elif pass_count >= MIN_CHECKLIST_PASS and direction:
            ENGINE.update({
                "status": "SETUP_READY",
                "direction": direction,
                "option_type": option_type,
                "signal": f"SETUP READY — {direction} {atm} {option_type} | Waiting cooldown/limit",
            })
        else:
            ENGINE.update({
                "status": "BLOCKED",
                "signal": f"Watching — {pass_count}/5 conditions | ORB: {'SET ✓' if ORB['set'] else 'Building...'}",
            })

    # ── Stats ──
    total    = ENGINE["trades_total"]
    win_rate = round(ENGINE["trades_won"] / total * 100) if total else 0

    payload = _build_payload(spot, vix, vel, mstatus, mmsg, expiry_label, atm,
                             direction or ENGINE["direction"],
                             option_type or ENGINE["option_type"],
                             chk, chk_labels, pass_count, total, win_rate,
                             lot_size, data_source, ema9, ema21, rsi, vwap, condition)
    ENGINE.update({"last_update": now, "payload": payload})
    return payload


def _manage_active_trade(open_t, spot, lot_size):
    global ENGINE
    direction = ENGINE["direction"]
    hit_tgt = (spot >= ENGINE["target"]) if direction == "LONG" else (spot <= ENGINE["target"])
    hit_sl  = (spot <= ENGINE["sl"])     if direction == "LONG" else (spot >= ENGINE["sl"])

    if hit_tgt or hit_sl:
        pnl_pts = round(abs(ENGINE["target"] - open_t['entry_price']), 1) if hit_tgt \
                  else round(abs(open_t['entry_price'] - ENGINE["sl"]), 1)
        pnl_sign = 1 if hit_tgt else -1
        pnl_rs   = round(pnl_pts * lot_size * pnl_sign, 0)
        reason   = "Target Hit ✅" if hit_tgt else "Stoploss Hit ❌"

        db_close_trade(open_t['id'], spot, reason, 'Auto-closed', 'Calm',
                       pnl_pts * pnl_sign, pnl_rs=pnl_rs)

        if hit_tgt:
            ENGINE["trades_won"]   += 1
            ENGINE["signal"]        = f"✅ TARGET HIT +{pnl_pts} pts | +₹{int(pnl_rs):,}"
        else:
            ENGINE["trades_lost"]  += 1
            ENGINE["signal"]        = f"❌ SL HIT -{pnl_pts} pts | -₹{int(abs(pnl_rs)):,}"

        ENGINE["trades_total"]     += 1
        ENGINE["session_pnl"]      = round(ENGINE["session_pnl"] + pnl_pts * pnl_sign, 1)
        ENGINE["session_pnl_rs"]   = round(ENGINE.get("session_pnl_rs", 0) + pnl_rs, 0)
        ENGINE["status"]            = "BLOCKED"
        tg(f"{reason}\n{pnl_pts} pts | ₹{int(abs(pnl_rs))} at {spot}")


def _fetch_mini_markets(vix):
    global ENGINE
    # BankNifty
    try:
        bn = fetch_spot("BANKNIFTY","NSE",TOKENS["BANKNIFTY"]["token"],"Nifty Bank")
        if bn:
            ENGINE["bn_spot"] = bn
            h = ENGINE.get("_bn_h", [])
            h.append(bn); h = h[-30:]
            ENGINE["_bn_h"] = h
            ENGINE["bn_rsi"]  = calc_rsi(h)
            ENGINE["bn_ema9"] = calc_ema(h, 9)
            ENGINE["bn_atm"]  = get_atm_strike(bn, 100)
            ENGINE["bn_condition"] = detect_market_condition(h, vix, bn-(h[-2] if len(h)>1 else bn))
    except Exception: pass
    # FinNifty
    try:
        fn = fetch_spot("FINNIFTY","NSE",TOKENS["FINNIFTY"]["token"],"Nifty Fin Service")
        if fn:
            ENGINE["fn_spot"] = fn
            h = ENGINE.get("_fn_h", [])
            h.append(fn); h = h[-30:]
            ENGINE["_fn_h"] = h
            ENGINE["fn_rsi"] = calc_rsi(h)
            ENGINE["fn_atm"] = get_atm_strike(fn, 50)
            ENGINE["fn_condition"] = detect_market_condition(h, vix, fn-(h[-2] if len(h)>1 else fn))
    except Exception: pass
    # Sensex
    try:
        sx = fetch_spot("SENSEX","BSE",TOKENS["SENSEX"]["token"],"SENSEX")
        if sx:
            ENGINE["sx_spot"] = sx
            h = ENGINE.get("_sx_h", [])
            h.append(sx); h = h[-30:]
            ENGINE["_sx_h"] = h
            ENGINE["sx_rsi"] = calc_rsi(h)
            ENGINE["sx_condition"] = detect_market_condition(h, vix, sx-(h[-2] if len(h)>1 else sx))
    except Exception: pass


def _build_closed_payload(mstatus, mmsg, expiry_label):
    return {
        "spot": ENGINE.get("last_spot", 0), "vix": 15.0, "velocity": 0,
        "status": "BLOCKED", "signal": mmsg, "brain_reasons": [],
        "entry": 0, "target": 0, "sl": 0,
        "chk": [False]*5, "chk_labels": [], "pass_count": 0,
        "total": ENGINE["trades_total"], "wins": ENGINE["trades_won"],
        "losses": ENGINE["trades_lost"], "pnl": round(ENGINE["session_pnl"],1),
        "win_rate": 0, "market_status": mstatus, "market_msg": mmsg,
        "atm_strike": ENGINE.get("atm_strike",0), "option_type": ENGINE.get("option_type","CE"),
        "direction": ENGINE.get("direction","LONG"),
        "data_source": ENGINE["data_source"], "expiry": expiry_label,
        "candles": CANDLE_5MIN[-50:], "current_candle": _candle_current,
        "orb_high": ORB["high"], "orb_low": ORB["low"], "orb_set": ORB["set"],
        "day_high": ENGINE.get("day_high",0), "day_low": ENGINE.get("day_low",0),
        "day_open": ENGINE.get("day_open",0),
        "bn_spot": ENGINE.get("bn_spot",0), "fn_spot": ENGINE.get("fn_spot",0),
        "sx_spot": ENGINE.get("sx_spot",0),
        "session_pnl_rs": ENGINE.get("session_pnl_rs",0),
        "ema9": ENGINE.get("ema9",0), "ema21": ENGINE.get("ema21",0),
        "rsi": ENGINE.get("rsi",50), "vwap": ENGINE.get("vwap",0),
        "trades_today": ENGINE.get("trades_today",0),
        "market_condition": ENGINE.get("market_condition","UNKNOWN"),
        "lot_size": ENGINE.get("lot_size",65),
        "session_name": get_session_name(),
        "ce_premium": 0.0, "pe_premium": 0.0, "live_pnl_rs": 0,
        "prev_close": ENGINE.get("prev_close",0),
        "today_open": ENGINE.get("today_open",0),
        "opening_gap": ENGINE.get("opening_gap",0),
        "bn_rsi": ENGINE.get("bn_rsi",50), "bn_ema9": ENGINE.get("bn_ema9",0),
        "bn_atm": ENGINE.get("bn_atm",0), "bn_condition": ENGINE.get("bn_condition","UNKNOWN"),
        "fn_rsi": ENGINE.get("fn_rsi",50), "fn_atm": ENGINE.get("fn_atm",0),
        "fn_condition": ENGINE.get("fn_condition","UNKNOWN"),
        "sx_rsi": ENGINE.get("sx_rsi",50), "sx_condition": ENGINE.get("sx_condition","UNKNOWN"),
    }


def _build_payload(spot, vix, vel, mstatus, mmsg, expiry_label,
                   atm, direction, option_type, chk, chk_labels,
                   pass_count, total, win_rate, lot_size, data_source,
                   ema9, ema21, rsi, vwap, condition):
    live_pnl_rs = 0
    if ENGINE["status"] == "TRADE_ACTIVE":
        entry = ENGINE["entry"]
        if direction == "LONG":
            live_pnl_rs = round((spot - entry) * lot_size, 0)
        else:
            live_pnl_rs = round((entry - spot) * lot_size, 0)

    return {
        "spot": round(spot,2), "vix": round(vix,2), "velocity": vel,
        "status": ENGINE["status"], "signal": ENGINE["signal"],
        "brain_reasons": ENGINE["brain_reasons"],
        "entry": ENGINE["entry"], "target": ENGINE["target"], "sl": ENGINE["sl"],
        "chk": chk, "chk_labels": chk_labels, "pass_count": pass_count,
        "total": total, "wins": ENGINE["trades_won"], "losses": ENGINE["trades_lost"],
        "pnl": round(ENGINE["session_pnl"],1), "win_rate": win_rate,
        "market_status": mstatus, "market_msg": mmsg,
        "atm_strike": atm, "option_type": option_type, "direction": direction,
        "data_source": data_source, "expiry": expiry_label,
        "candles": CANDLE_5MIN[-50:], "current_candle": _candle_current,
        "orb_high": round(ORB["high"],1), "orb_low": round(ORB["low"],1), "orb_set": ORB["set"],
        "day_high": round(ENGINE.get("day_high",spot),1),
        "day_low":  round(ENGINE.get("day_low",spot),1),
        "day_open": round(ENGINE.get("day_open",spot),1),
        "bn_spot": round(ENGINE.get("bn_spot",0),2),
        "fn_spot": round(ENGINE.get("fn_spot",0),2),
        "sx_spot": round(ENGINE.get("sx_spot",0),2),
        "bn_rsi": round(ENGINE.get("bn_rsi",50),1),
        "bn_ema9": round(ENGINE.get("bn_ema9",0),2),
        "bn_atm": ENGINE.get("bn_atm",0),
        "bn_condition": ENGINE.get("bn_condition","UNKNOWN"),
        "fn_rsi": round(ENGINE.get("fn_rsi",50),1),
        "fn_atm": ENGINE.get("fn_atm",0),
        "fn_condition": ENGINE.get("fn_condition","UNKNOWN"),
        "sx_rsi": round(ENGINE.get("sx_rsi",50),1),
        "sx_condition": ENGINE.get("sx_condition","UNKNOWN"),
        "ema9": round(ema9,2), "ema21": round(ema21,2),
        "rsi": round(rsi,1), "vwap": round(vwap,2),
        "market_condition": condition,
        "strategy_mode": "ORB+TREND",
        "session_pnl_rs": round(ENGINE.get("session_pnl_rs",0),0),
        "live_pnl_rs": live_pnl_rs,
        "lot_size": lot_size,
        "ce_premium": 0.0, "pe_premium": 0.0,
        "option_entry_premium": ENGINE.get("option_entry_premium",0),
        "prev_close": round(ENGINE.get("prev_close",0),2),
        "today_open": round(ENGINE.get("today_open",0),2),
        "opening_gap": round(ENGINE.get("opening_gap",0),1),
        "trades_today": ENGINE.get("trades_today",0),
        "max_trades_day": MAX_TRADES_PER_DAY,
        "session_name": get_session_name(),
        "active_index": ENGINE.get("active_index","NIFTY"),
    }


# ═══════════════════════════════════════════════
# HTML TEMPLATE
# ═══════════════════════════════════════════════

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>⚡ GOAT PRO</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=JetBrains+Mono:wght@300;400;700&family=Rajdhani:wght@400;600;700&display=swap');
:root{
  --bg:#f0f4ff;--panel:#fff;--panel2:#f7f9ff;--border:#dde4f5;
  --accent:#1a56db;--accent2:#0e3fa8;
  --green:#0a9e5c;--green2:#e6f9f1;
  --red:#e02d3c;--red2:#fdeef0;
  --gold:#b45309;--gold2:#fef3c7;
  --dim:#6b7280;--text:#1e2a3a;
  --shadow:0 2px 12px rgba(26,86,219,0.08);
  --shadow2:0 6px 28px rgba(26,86,219,0.18);
}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:'Rajdhani',sans-serif;min-height:100vh;}
.wrap{max-width:1400px;margin:0 auto;padding:12px 14px;}

/* TOPBAR */
.topbar{display:flex;align-items:center;justify-content:space-between;
  background:linear-gradient(135deg,#1a56db,#0e3fa8);
  border-radius:12px;padding:14px 22px;margin-bottom:10px;
  box-shadow:var(--shadow2);flex-wrap:wrap;gap:10px;}
.topbar h1{font-family:'Bebas Neue',sans-serif;font-size:clamp(20px,4vw,32px);
  letter-spacing:5px;color:#fff;}
.topbar small{font-family:'JetBrains Mono',monospace;font-size:9px;
  color:rgba(255,255,255,0.5);letter-spacing:2px;display:block;}
.tb-right{display:flex;gap:12px;align-items:center;flex-wrap:wrap;}
.tb-stat{text-align:center;}
.tb-val{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;color:#fff;}
.tb-label{font-size:9px;color:rgba(255,255,255,0.5);letter-spacing:1px;}
.tb-div{width:1px;height:26px;background:rgba(255,255,255,0.2);}
.live-pill{display:flex;align-items:center;gap:6px;
  background:rgba(255,255,255,0.15);border:1px solid rgba(255,255,255,0.3);
  border-radius:20px;padding:5px 14px;
  font-family:'JetBrains Mono',monospace;font-size:10px;color:#fff;}
.ldot{width:7px;height:7px;border-radius:50%;background:#4ade80;
  box-shadow:0 0 8px #4ade80;animation:blink 1s infinite;}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.2}}

/* LEGAL */
.legal-banner{background:var(--gold2);border:1.5px solid #f59e0b;border-radius:8px;
  padding:8px 14px;margin-bottom:10px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.legal-banner span{font-size:11px;color:#92400e;flex:1;}

/* SESSION STRIP */
.sess-strip{display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap;}
.sess{flex:1;min-width:90px;border:1.5px solid var(--border);border-radius:8px;
  padding:7px 10px;text-align:center;background:var(--panel);}
.sess.active{border-color:var(--accent);background:#eef2ff;}
.sess-name{font-size:9px;color:var(--dim);letter-spacing:1px;font-weight:700;}
.sess-time{font-family:'JetBrains Mono',monospace;font-size:10px;margin:2px 0;}
.sess-heat{font-size:12px;}

/* ORB STRIP */
.orb-strip{background:linear-gradient(135deg,#1e40af,#1a56db);border-radius:10px;
  padding:10px 16px;margin-bottom:10px;display:flex;gap:20px;align-items:center;flex-wrap:wrap;
  box-shadow:var(--shadow2);}
.orb-label{font-size:9px;color:rgba(255,255,255,0.6);letter-spacing:2px;font-weight:700;}
.orb-val{font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:700;color:#fff;}
.orb-status{padding:4px 12px;border-radius:20px;font-size:10px;font-weight:700;
  background:rgba(255,255,255,0.2);color:#fff;}
.orb-status.set{background:#4ade80;color:#14532d;}
.orb-status.building{background:#fbbf24;color:#78350f;}

/* MARKET TABS */
.market-tabs{display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap;}
.mktab{padding:7px 14px;border-radius:8px;border:1.5px solid var(--border);
  background:var(--panel);font-family:'JetBrains Mono',monospace;font-size:10px;
  cursor:pointer;color:var(--dim);font-weight:700;text-align:center;}
.mktab.on{background:linear-gradient(135deg,#1a56db,#0e3fa8);color:#fff;border-color:var(--accent);}
.mktab .chg{font-size:9px;margin-top:2px;}

/* LAYOUT */
.layout{display:grid;grid-template-columns:1fr 300px;gap:10px;}
@media(max-width:900px){.layout{grid-template-columns:1fr;}}
.left,.right{display:flex;flex-direction:column;gap:10px;}

/* CARD */
.card{background:var(--panel);border:1.5px solid var(--border);border-radius:10px;box-shadow:var(--shadow);overflow:hidden;}
.chdr{display:flex;align-items:center;justify-content:space-between;
  padding:8px 14px;border-bottom:1.5px solid var(--border);background:var(--panel2);}
.ctitle{font-family:'Bebas Neue',sans-serif;font-size:13px;letter-spacing:2px;color:var(--accent);}

/* HERO */
.hero{background:linear-gradient(135deg,#1a56db,#0e3fa8);border-radius:10px;
  padding:16px 20px;box-shadow:var(--shadow2);color:#fff;display:flex;gap:18px;align-items:center;flex-wrap:wrap;}
.hero-price{font-family:'JetBrains Mono',monospace;font-size:clamp(28px,5vw,44px);font-weight:700;}
.hero-meta{display:flex;gap:14px;flex-wrap:wrap;margin-top:4px;}
.hm{text-align:center;}
.hm-v{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;}
.hm-l{font-size:9px;color:rgba(255,255,255,0.55);letter-spacing:1px;}

/* SIGNAL */
.signal-box{padding:12px 16px;border-radius:8px;margin:10px;font-weight:700;
  font-size:13px;text-align:center;border:1.5px solid;font-family:'JetBrains Mono',monospace;}
.sig-bull{background:var(--green2);color:var(--green);border-color:rgba(10,158,92,.3);}
.sig-bear{background:var(--red2);color:var(--red);border-color:rgba(224,45,60,.3);}
.sig-wait{background:var(--gold2);color:var(--gold);border-color:rgba(180,83,9,.3);}
.sig-neu{background:#eef2ff;color:var(--accent);border-color:rgba(26,86,219,.3);}
.sig-ready{background:#f5f3ff;color:#7c3aed;border-color:rgba(124,58,237,.3);}

/* CHECKLIST */
.chk-row{display:flex;align-items:center;gap:8px;padding:7px 14px;
  border-bottom:1px solid var(--border);font-size:12px;}
.chk-icon{width:20px;height:20px;border-radius:50%;display:flex;align-items:center;
  justify-content:center;font-size:10px;font-weight:700;flex-shrink:0;}
.chk-pass{background:var(--green2);color:var(--green);}
.chk-fail{background:var(--red2);color:var(--red);}

/* TRADE CARD */
.trade-card{margin:10px;padding:12px;border-radius:8px;border:1.5px solid;font-size:12px;}
.trade-bull{background:var(--green2);border-color:rgba(10,158,92,.3);}
.trade-bear{background:var(--red2);border-color:rgba(224,45,60,.3);}
.tr{display:flex;justify-content:space-between;margin:4px 0;}
.tl{color:var(--dim);}
.tv{font-family:'JetBrains Mono',monospace;font-weight:700;}

/* STAT */
.stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:10px;}
.stat-box{background:var(--panel2);border-radius:8px;padding:10px;text-align:center;border:1.5px solid var(--border);}
.stat-val{font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:700;}
.stat-lbl{font-size:10px;color:var(--dim);margin-top:2px;}

/* BRAIN */
.brain-box{padding:10px 14px;}
.brain-item{display:flex;align-items:flex-start;gap:6px;margin:5px 0;font-size:12px;line-height:1.4;}
.brain-dot{width:6px;height:6px;border-radius:50%;background:var(--accent);margin-top:5px;flex-shrink:0;}

/* MINI MARKET */
.mini{background:var(--panel);border:1.5px solid var(--border);border-radius:10px;overflow:hidden;}
.mini-hdr{display:flex;justify-content:space-between;align-items:center;
  padding:8px 12px;background:var(--panel2);border-bottom:1px solid var(--border);}
.mini-name{font-family:'Bebas Neue',sans-serif;font-size:13px;letter-spacing:2px;color:var(--accent);}
.mini-px{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;}
.mini-body{padding:6px 12px;}
.ms{display:flex;justify-content:space-between;padding:3px 0;font-size:11px;border-bottom:1px solid rgba(0,0,0,.04);}
.ms-l{color:var(--dim);}
.ms-v{font-family:'JetBrains Mono',monospace;font-weight:700;}
.mini-bias{margin:8px 10px;padding:5px 10px;border-radius:6px;font-size:11px;font-weight:700;text-align:center;border:1px solid;}

/* INDICATOR BAR */
.ind-row{display:flex;align-items:center;gap:8px;padding:6px 14px;border-bottom:1px solid var(--border);font-size:11px;}
.iname{width:55px;font-weight:700;}
.ibar{flex:1;height:6px;background:var(--border);border-radius:3px;overflow:hidden;}
.ibfill{height:100%;border-radius:3px;}
.ival{width:65px;font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;text-align:right;}

/* ALERT */
.alert-item{display:flex;align-items:center;gap:8px;padding:7px 12px;border-bottom:1px solid var(--border);font-size:11px;}
.alert-time{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--dim);white-space:nowrap;}
.alert-badge{padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700;}
.bull{background:var(--green2);color:var(--green);}
.bear{background:var(--red2);color:var(--red);}
.neu{background:#eef2ff;color:var(--accent);}
</style>
</head>
<body>
<div class="wrap">

<!-- TOPBAR -->
<div class="topbar">
  <div>
    <h1>⚡ GOAT PRO</h1>
    <small>VIRTUAL PAPER TRADING SYSTEM</small>
  </div>
  <div class="tb-right">
    <div class="tb-stat"><div class="tb-val" id="tb-time">--:--:--</div><div class="tb-label">IST</div></div>
    <div class="tb-div"></div>
    <div class="tb-stat"><div class="tb-val" id="tb-spot">--</div><div class="tb-label">NIFTY</div></div>
    <div class="tb-div"></div>
    <div class="tb-stat"><div class="tb-val" id="tb-vix" style="color:#fbbf24">--</div><div class="tb-label">VIX</div></div>
    <div class="tb-div"></div>
    <div class="tb-stat"><div class="tb-val" id="tb-pnl" style="color:#4ade80">₹0</div><div class="tb-label">P&L</div></div>
    <div class="tb-div"></div>
    <div class="tb-stat"><div class="tb-val" id="tb-trades">0/8</div><div class="tb-label">TRADES</div></div>
    <div class="tb-div"></div>
    <div class="live-pill"><div class="ldot" id="live-dot"></div><span id="market-pill">LOADING</span></div>
  </div>
</div>

<!-- LEGAL -->
<div class="legal-banner">
  <span>⚖️ <strong>GOAT PRO</strong> — Personal Educational Paper Trading Only. SEBI registered advice nahi hai. Virtual money simulation.</span>
</div>

<!-- SESSION STRIP -->
<div class="sess-strip">
  <div class="sess" id="s1"><div class="sess-name">PRE-OPEN</div><div class="sess-time">9:00–9:15</div><div class="sess-heat">🌅</div></div>
  <div class="sess" id="s2"><div class="sess-name">MORNING</div><div class="sess-time">9:15–11:00</div><div class="sess-heat">🔥</div></div>
  <div class="sess" id="s3"><div class="sess-name">MIDDAY</div><div class="sess-time">11:00–2:00</div><div class="sess-heat">⚡</div></div>
  <div class="sess" id="s4"><div class="sess-name">POWER HOUR</div><div class="sess-time">2:00–3:30</div><div class="sess-heat">🚀</div></div>
</div>

<!-- ORB STRIP -->
<div class="orb-strip">
  <div>
    <div class="orb-label">OPENING RANGE BREAKOUT</div>
    <div style="font-size:9px;color:rgba(255,255,255,.5);margin-top:2px;">9:15–9:30 AM range • Primary strategy</div>
  </div>
  <div>
    <div class="orb-label">ORB HIGH</div>
    <div class="orb-val" id="orb-high">--</div>
  </div>
  <div>
    <div class="orb-label">ORB LOW</div>
    <div class="orb-val" id="orb-low">--</div>
  </div>
  <div>
    <div class="orb-label">RANGE</div>
    <div class="orb-val" id="orb-range">--</div>
  </div>
  <div id="orb-status-badge" class="orb-status building">⏳ Building...</div>
</div>

<!-- MARKET TABS -->
<div class="market-tabs">
  <div class="mktab on" onclick="switchTab(this,'nifty')">🔵 NIFTY<div class="chg" id="nf-val">--</div></div>
  <div class="mktab" onclick="switchTab(this,'banknifty')">🏦 BANKNIFTY<div class="chg" id="bn-val">--</div></div>
  <div class="mktab" onclick="switchTab(this,'finnifty')">📊 FINNIFTY<div class="chg" id="fn-val">--</div></div>
  <div class="mktab" onclick="switchTab(this,'sensex')">📈 SENSEX<div class="chg" id="sx-val">--</div></div>
</div>

<!-- LAYOUT -->
<div class="layout">
<div class="left">

  <!-- HERO -->
  <div class="hero">
    <div style="flex:1">
      <div style="font-size:10px;color:rgba(255,255,255,.6);letter-spacing:2px;margin-bottom:4px" id="hero-label">🔵 NIFTY 50</div>
      <div class="hero-price" id="hero-price">--</div>
      <div class="hero-meta">
        <div class="hm"><div class="hm-v" id="h-open">--</div><div class="hm-l">OPEN</div></div>
        <div class="hm"><div class="hm-v" id="h-high" style="color:#4ade80">--</div><div class="hm-l">HIGH</div></div>
        <div class="hm"><div class="hm-v" id="h-low" style="color:#f87171">--</div><div class="hm-l">LOW</div></div>
        <div class="hm"><div class="hm-v" id="h-vwap" style="color:#fbbf24">--</div><div class="hm-l">VWAP</div></div>
        <div class="hm"><div class="hm-v" id="h-atm">--</div><div class="hm-l">ATM</div></div>
        <div class="hm"><div class="hm-v" id="h-expiry" style="color:#a78bfa">--</div><div class="hm-l">EXPIRY</div></div>
      </div>
    </div>
    <div style="text-align:right">
      <div style="font-size:9px;color:rgba(255,255,255,.5);margin-bottom:4px">CONDITION</div>
      <div id="cond-badge" style="font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;color:#fff;background:rgba(255,255,255,.2);padding:4px 12px;border-radius:20px;">--</div>
      <div style="font-size:9px;color:rgba(255,255,255,.5);margin-top:8px">SESSION</div>
      <div id="sess-badge" style="font-family:'JetBrains Mono',monospace;font-size:11px;color:#fbbf24;">--</div>
    </div>
  </div>

  <!-- SIGNAL -->
  <div class="card">
    <div class="chdr">
      <div class="ctitle">🎯 GOAT SIGNAL</div>
      <div style="font-size:11px;color:var(--dim)" id="signal-src">--</div>
    </div>
    <div id="signal-display"><div class="signal-box sig-neu">⏳ Loading...</div></div>

    <!-- Trade counters -->
    <div style="display:flex;gap:0;border-top:1px solid var(--border)">
      <div style="flex:1;padding:8px;text-align:center;border-right:1px solid var(--border)">
        <div style="font-family:'JetBrains Mono',monospace;font-weight:700;font-size:14px" id="ctr-today">0/8</div>
        <div style="font-size:9px;color:var(--dim)">TODAY'S TRADES</div>
      </div>
      <div style="flex:1;padding:8px;text-align:center;border-right:1px solid var(--border)">
        <div style="font-family:'JetBrains Mono',monospace;font-weight:700;font-size:14px;color:var(--green)" id="ctr-wins">0</div>
        <div style="font-size:9px;color:var(--dim)">WINS</div>
      </div>
      <div style="flex:1;padding:8px;text-align:center;border-right:1px solid var(--border)">
        <div style="font-family:'JetBrains Mono',monospace;font-weight:700;font-size:14px;color:var(--red)" id="ctr-losses">0</div>
        <div style="font-size:9px;color:var(--dim)">LOSSES</div>
      </div>
      <div style="flex:1;padding:8px;text-align:center">
        <div style="font-family:'JetBrains Mono',monospace;font-weight:700;font-size:14px" id="ctr-wr">0%</div>
        <div style="font-size:9px;color:var(--dim)">WIN RATE</div>
      </div>
    </div>
  </div>

  <!-- CHECKLIST -->
  <div class="card">
    <div class="chdr">
      <div class="ctitle">✅ SIGNAL CHECKLIST</div>
      <div style="font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700" id="chk-score">0/5</div>
    </div>
    <div id="chk-list">
      <div class="chk-row"><div class="chk-icon chk-fail" id="c0">✗</div><span id="cl0">Momentum active</span></div>
      <div class="chk-row"><div class="chk-icon chk-fail" id="c1">✗</div><span id="cl1">VIX safe (< 20)</span></div>
      <div class="chk-row"><div class="chk-icon chk-fail" id="c2">✗</div><span id="cl2">EMA trend exists</span></div>
      <div class="chk-row"><div class="chk-icon chk-fail" id="c3">✗</div><span id="cl3">RSI in range (35–70)</span></div>
      <div class="chk-row"><div class="chk-icon chk-fail" id="c4">✗</div><span id="cl4">ORB breakout confirmed</span></div>
    </div>
    <div style="padding:8px 14px;font-size:11px;color:var(--dim);border-top:1px solid var(--border)">
      ⚡ Trade fires when <strong>3 or more</strong> conditions pass
    </div>
  </div>

  <!-- ACTIVE TRADE -->
  <div class="card">
    <div class="chdr">
      <div class="ctitle">📊 ACTIVE TRADE</div>
      <div id="live-pnl-badge" style="font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700">--</div>
    </div>
    <div id="trade-display"><div style="padding:20px;text-align:center;color:var(--dim);font-size:12px">No active trade</div></div>
  </div>

  <!-- GOAT BRAIN -->
  <div class="card">
    <div class="chdr"><div class="ctitle">🧠 GOAT BRAIN — KYUN LIYA?</div></div>
    <div class="brain-box" id="brain-list">
      <div style="color:var(--dim);font-size:12px">Waiting for signal...</div>
    </div>
  </div>

  <!-- INDICATORS -->
  <div class="card">
    <div class="chdr"><div class="ctitle">📡 INDICATORS</div></div>
    <div class="ind-row">
      <div class="iname">EMA9</div>
      <div class="ibar"><div class="ibfill" id="ema9-bar" style="width:50%;background:var(--accent)"></div></div>
      <div class="ival" id="ema9-val">--</div>
    </div>
    <div class="ind-row">
      <div class="iname">EMA21</div>
      <div class="ibar"><div class="ibfill" id="ema21-bar" style="width:50%;background:var(--accent)"></div></div>
      <div class="ival" id="ema21-val">--</div>
    </div>
    <div class="ind-row">
      <div class="iname">RSI</div>
      <div class="ibar"><div class="ibfill" id="rsi-bar" style="width:50%;background:var(--gold)"></div></div>
      <div class="ival" id="rsi-val">50</div>
    </div>
    <div class="ind-row">
      <div class="iname">VWAP</div>
      <div class="ibar"><div class="ibfill" id="vwap-bar" style="width:50%;background:#7c3aed"></div></div>
      <div class="ival" id="vwap-val">--</div>
    </div>
  </div>

  <!-- SESSION STATS -->
  <div class="card">
    <div class="chdr"><div class="ctitle">📈 SESSION STATS</div></div>
    <div class="stat-grid">
      <div class="stat-box"><div class="stat-val" id="s-total">0</div><div class="stat-lbl">TOTAL TRADES</div></div>
      <div class="stat-box"><div class="stat-val" id="s-wr" style="color:var(--green)">0%</div><div class="stat-lbl">WIN RATE</div></div>
      <div class="stat-box"><div class="stat-val" id="s-pnl-pts">0</div><div class="stat-lbl">P&L POINTS</div></div>
      <div class="stat-box"><div class="stat-val" id="s-pnl-rs" style="color:var(--green)">₹0</div><div class="stat-lbl">P&L RUPEES</div></div>
    </div>
  </div>

</div><!-- /left -->

<!-- RIGHT PANEL -->
<div class="right">

  <!-- BANKNIFTY -->
  <div class="mini">
    <div class="mini-hdr"><div class="mini-name">🏦 BANKNIFTY</div><div class="mini-px" id="bn-px">--</div></div>
    <div class="mini-body">
      <div class="ms"><div class="ms-l">RSI</div><div class="ms-v" id="bn-rsi">--</div></div>
      <div class="ms"><div class="ms-l">EMA9</div><div class="ms-v" id="bn-ema">--</div></div>
      <div class="ms"><div class="ms-l">ATM</div><div class="ms-v" id="bn-atm">--</div></div>
      <div class="ms"><div class="ms-l">TREND</div><div class="ms-v" id="bn-tr">--</div></div>
    </div>
    <div class="mini-bias" id="bn-bias" style="background:#eef2ff;color:var(--accent);border-color:rgba(26,86,219,.3)">⏳ Loading...</div>
  </div>

  <!-- FINNIFTY -->
  <div class="mini">
    <div class="mini-hdr"><div class="mini-name">📊 FINNIFTY</div><div class="mini-px" id="fn-px">--</div></div>
    <div class="mini-body">
      <div class="ms"><div class="ms-l">RSI</div><div class="ms-v" id="fn-rsi">--</div></div>
      <div class="ms"><div class="ms-l">ATM</div><div class="ms-v" id="fn-atm">--</div></div>
      <div class="ms"><div class="ms-l">TREND</div><div class="ms-v" id="fn-tr">--</div></div>
    </div>
    <div class="mini-bias" id="fn-bias" style="background:#eef2ff;color:var(--accent);border-color:rgba(26,86,219,.3)">⏳ Loading...</div>
  </div>

  <!-- SENSEX -->
  <div class="mini">
    <div class="mini-hdr"><div class="mini-name">📈 SENSEX</div><div class="mini-px" id="sx-px">--</div></div>
    <div class="mini-body">
      <div class="ms"><div class="ms-l">RSI</div><div class="ms-v" id="sx-rsi">--</div></div>
      <div class="ms"><div class="ms-l">TREND</div><div class="ms-v" id="sx-tr">--</div></div>
      <div class="ms"><div class="ms-l">VIX</div><div class="ms-v" id="sx-vix">--</div></div>
    </div>
    <div class="mini-bias" id="sx-bias" style="background:#eef2ff;color:var(--accent);border-color:rgba(26,86,219,.3)">⏳ Loading...</div>
  </div>

  <!-- OPENING ANALYSIS -->
  <div class="card">
    <div class="chdr"><div class="ctitle">🌅 OPENING ANALYSIS</div></div>
    <div style="padding:10px 12px;font-size:12px">
      <div class="ms"><div class="ms-l">Prev Close</div><div class="ms-v" id="oa-prev">--</div></div>
      <div class="ms"><div class="ms-l">Today Open</div><div class="ms-v" id="oa-open">--</div></div>
      <div class="ms"><div class="ms-l">Gap</div><div class="ms-v" id="oa-gap">--</div></div>
      <div class="ms"><div class="ms-l">Bias</div><div class="ms-v" id="oa-bias">--</div></div>
    </div>
  </div>

  <!-- LIVE ALERTS -->
  <div class="card">
    <div class="chdr"><div class="ctitle">🔔 ALERTS</div></div>
    <div id="alert-list" style="max-height:220px;overflow-y:auto">
      <div style="padding:14px;text-align:center;color:var(--dim);font-size:11px">Waiting for signals...</div>
    </div>
  </div>

  <!-- TRADE JOURNAL -->
  <div class="card">
    <div class="chdr"><div class="ctitle">📓 TRADE LOG</div></div>
    <div id="journal-list" style="max-height:280px;overflow-y:auto">
      <div style="padding:14px;text-align:center;color:var(--dim);font-size:11px">No trades yet</div>
    </div>
  </div>

</div><!-- /right -->
</div><!-- /layout -->

<div style="background:var(--panel);border:1.5px solid var(--border);border-radius:8px;padding:10px 14px;margin-top:10px;display:flex;gap:8px;align-items:center">
  <span style="background:var(--gold2);color:var(--gold);border:1px solid #f59e0b;padding:3px 8px;border-radius:4px;font-size:10px;font-weight:700;white-space:nowrap">⚖️ DISCLAIMER</span>
  <span style="font-size:11px;color:var(--dim)">GOAT PRO is a virtual paper trading simulation for educational purposes only. Not SEBI registered. No real money involved. Past performance does not guarantee future results.</span>
</div>
</div><!-- /wrap -->

<script>
const $ = id => document.getElementById(id);
const setText = (id,v) => { const e=$(id); if(e) e.textContent=v; };
const setHtml = (id,v) => { const e=$(id); if(e) e.innerHTML=v; };
const setColor = (id,c) => { const e=$(id); if(e) e.style.color=c; };
const fmt = v => v.toLocaleString('en-IN');
const fmtPts = v => (v>=0?'+':'')+v.toFixed(1)+' pts';
const fmtRs  = v => (v>=0?'+₹':'-₹')+Math.abs(v).toLocaleString('en-IN');

// Clock
function tick(){
  const n=new Date();
  const t=`${String(n.getHours()).padStart(2,'0')}:${String(n.getMinutes()).padStart(2,'0')}:${String(n.getSeconds()).padStart(2,'0')}`;
  setText('tb-time',t);
  const m=n.getHours()*60+n.getMinutes();
  [['s1',900,915],['s2',915,1100],['s3',1100,1400],['s4',1400,1530]].forEach(([id,a,b])=>{
    const e=$(id); if(e) e.classList.toggle('active',m>=a&&m<b);
  });
}
setInterval(tick,1000); tick();

// Market switch
let currentMkt='nifty';
function switchTab(el,key){
  document.querySelectorAll('.mktab').forEach(t=>t.classList.remove('on'));
  el.classList.add('on'); currentMkt=key;
  const labels={nifty:'🔵 NIFTY 50',banknifty:'🏦 BANKNIFTY',finnifty:'📊 FINNIFTY',sensex:'📈 SENSEX'};
  setText('hero-label',labels[key]);
}

let lastSignal='', lastAlertKey='';

async function fetchData(){
  try{
    const d=await(await fetch('/api/data')).json();
    render(d);
  }catch(e){ setText('market-pill','ERROR'); }
}

async function fetchTrades(){
  try{
    const d=await(await fetch('/api/trades')).json();
    renderTrades(d);
  }catch(e){}
}

function render(d){
  const ms=d.market_status||'CLOSED';
  setText('market-pill',ms);
  const dot=$('live-dot');
  if(dot) dot.style.background=ms==='OPEN'?'#4ade80':'#f87171';

  if(!d.spot) return;

  // Topbar
  setText('tb-spot',fmt(d.spot));
  const vixEl=$('tb-vix');
  if(vixEl){ vixEl.textContent=d.vix; vixEl.style.color=d.vix>18?'#f87171':d.vix>14?'#fbbf24':'#4ade80'; }
  const pnlRs=d.session_pnl_rs||0;
  setText('tb-pnl',fmtRs(pnlRs)); setColor('tb-pnl',pnlRs>=0?'#4ade80':'#f87171');
  setText('tb-trades',(d.trades_today||0)+'/'+(d.max_trades_day||8));

  // Hero
  setText('hero-price',fmt(d.spot));
  setText('h-open',d.day_open?fmt(d.day_open):'--');
  setText('h-high',d.day_high?fmt(d.day_high):'--');
  setText('h-low', d.day_low?fmt(d.day_low):'--');
  setText('h-vwap',d.vwap?fmt(d.vwap):'--');
  setText('h-atm', d.atm_strike||'--');
  setText('h-expiry',d.expiry||'--');
  setText('sess-badge',d.session_name||'--');

  const cond=d.market_condition||'UNKNOWN';
  const cb=$('cond-badge');
  if(cb){
    cb.textContent=cond.replace('_',' ');
    const cols={TRENDING_UP:'#4ade80',TRENDING_DOWN:'#f87171',SIDEWAYS:'#fbbf24',VOLATILE:'#f87171',UNKNOWN:'#fff'};
    cb.style.color=cols[cond]||'#fff';
  }

  // ORB
  if(d.orb_set){
    setText('orb-high',d.orb_high?fmt(d.orb_high):'--');
    setText('orb-low', d.orb_low?fmt(d.orb_low):'--');
    const range=d.orb_high&&d.orb_low?Math.round(d.orb_high-d.orb_low)+'pts':'--';
    setText('orb-range',range);
    const b=$('orb-status-badge');
    if(b){ b.textContent='✅ Range Set'; b.className='orb-status set'; }
  } else {
    const b=$('orb-status-badge');
    if(b){ b.textContent='⏳ Building 9:15–9:30'; b.className='orb-status building'; }
  }

  // Market tabs spot values
  setText('nf-val',fmt(d.spot));
  if(d.bn_spot>0) setText('bn-val',fmt(d.bn_spot));
  if(d.fn_spot>0) setText('fn-val',fmt(d.fn_spot));
  if(d.sx_spot>0) setText('sx-val',fmt(d.sx_spot));

  // Signal
  const status=d.status||'BLOCKED';
  const signal=d.signal||'--';
  let cls='sig-neu';
  if(status==='TRADE_ACTIVE') cls=d.direction==='LONG'?'sig-bull':'sig-bear';
  else if(status==='SETUP_READY') cls='sig-ready';
  else if(signal.includes('TARGET')) cls='sig-bull';
  else if(signal.includes('SL HIT')) cls='sig-bear';
  else if(status==='BLOCKED') cls='sig-wait';
  setHtml('signal-display',`<div class="signal-box ${cls}">${signal}</div>`);
  setText('signal-src',d.data_source||'--');

  // Trade counters
  setText('ctr-today',(d.trades_today||0)+'/'+(d.max_trades_day||8));
  setText('ctr-wins',d.wins||0); setColor('ctr-wins','var(--green)');
  setText('ctr-losses',d.losses||0); setColor('ctr-losses','var(--red)');
  const wr=d.wins&&d.total?Math.round(d.wins/d.total*100):0;
  setText('ctr-wr',wr+'%'); setColor('ctr-wr',wr>=60?'var(--green)':wr>=40?'var(--gold)':'var(--red)');

  // Checklist
  const chk=d.chk||[false,false,false,false,false];
  const labels=d.chk_labels||['Momentum active','VIX safe','EMA trend','RSI range','ORB breakout'];
  chk.forEach((v,i)=>{
    const ic=$('c'+i), lb=$('cl'+i);
    if(ic){ ic.className='chk-icon '+(v?'chk-pass':'chk-fail'); ic.textContent=v?'✓':'✗'; }
    if(lb) lb.textContent=labels[i]||'';
  });
  const pass=d.pass_count||0;
  const scoreEl=$('chk-score');
  if(scoreEl){ scoreEl.textContent=pass+'/5'; scoreEl.style.color=pass>=3?'var(--green)':pass>=2?'var(--gold)':'var(--red)'; }

  // Active trade
  if(status==='TRADE_ACTIVE'&&d.entry>0){
    const isLong=d.direction==='LONG';
    const livePts=isLong?(d.spot-d.entry):(d.entry-d.spot);
    const liveRs=Math.round(livePts*(d.lot_size||65));
    const col=liveRs>=0?'var(--green)':'var(--red)';
    setHtml('trade-display',`
      <div class="trade-card ${isLong?'trade-bull':'trade-bear'}">
        <div class="tr"><span class="tl">Direction</span><span class="tv" style="color:${isLong?'var(--green)':'var(--red)'}">${d.direction} ${d.atm_strike} ${d.option_type}</span></div>
        <div class="tr"><span class="tl">Entry</span><span class="tv">${fmt(d.entry)}</span></div>
        <div class="tr"><span class="tl">LTP</span><span class="tv">${fmt(d.spot)}</span></div>
        <div class="tr"><span class="tl">Target</span><span class="tv" style="color:var(--green)">${fmt(d.target)}</span></div>
        <div class="tr"><span class="tl">Stop Loss</span><span class="tv" style="color:var(--red)">${fmt(d.sl)}</span></div>
        <div class="tr"><span class="tl">Live P&L</span><span class="tv" style="color:${col}">${fmtPts(livePts)} | ${fmtRs(liveRs)}</span></div>
        <div class="tr"><span class="tl">Lot</span><span class="tv">${d.lot_size||65} qty</span></div>
        <div class="tr"><span class="tl">Setup</span><span class="tv" style="color:var(--accent)">${d.strategy_mode||'ORB'}</span></div>
      </div>`);
    const badge=$('live-pnl-badge');
    if(badge){ badge.textContent=fmtRs(liveRs); badge.style.color=liveRs>=0?'var(--green)':'var(--red)'; }
    // New trade alert
    const ak=d.direction+'_'+d.entry;
    if(ak!==lastAlertKey){ addAlert(signal,isLong); lastAlertKey=ak; }
  } else {
    setHtml('trade-display','<div style="padding:20px;text-align:center;color:var(--dim);font-size:12px">No active trade</div>');
    setText('live-pnl-badge','--');
  }

  // Brain
  const br=d.brain_reasons||[];
  setHtml('brain-list', br.length?br.map(r=>`<div class="brain-item"><div class="brain-dot"></div><span>${r}</span></div>`).join('')
    : '<div style="color:var(--dim);font-size:12px">Waiting for signal...</div>');

  // Indicators
  const e9=d.ema9||0, e21=d.ema21||0, rsi=d.rsi||50, vwap=d.vwap||0, spot=d.spot||1;
  setText('ema9-val', e9?fmt(e9):'--');
  setText('ema21-val',e21?fmt(e21):'--');
  setText('rsi-val',rsi.toFixed(1));
  setText('vwap-val',vwap?fmt(vwap):'--');
  const rb=$('rsi-bar');
  if(rb){ rb.style.width=rsi+'%'; rb.style.background=rsi>70?'#e02d3c':rsi<30?'#0a9e5c':'#b45309'; }
  const vwapPct=vwap?Math.min(100,Math.max(0,(spot/vwap)*50)):50;
  const vb=$('vwap-bar'); if(vb) vb.style.width=vwapPct+'%';

  // Stats
  const pnl=d.pnl||0;
  setText('s-total',d.total||0);
  setText('s-wr',wr+'%'); setColor('s-wr',wr>=60?'var(--green)':wr>=40?'var(--gold)':'var(--red)');
  setText('s-pnl-pts',fmtPts(pnl)); setColor('s-pnl-pts',pnl>=0?'var(--green)':'var(--red)');
  setText('s-pnl-rs',fmtRs(pnlRs)); setColor('s-pnl-rs',pnlRs>=0?'var(--green)':'var(--red)');

  // Mini markets
  renderMini('bn',d.bn_spot,d.bn_rsi,d.bn_ema9,d.bn_atm,d.bn_condition,'BANKNIFTY');
  renderMini('fn',d.fn_spot,d.fn_rsi,null,d.fn_atm,d.fn_condition,'FINNIFTY');
  renderMini('sx',d.sx_spot,d.sx_rsi,null,null,d.sx_condition,'SENSEX');
  setText('sx-vix',d.vix||'--');

  // Opening analysis
  setText('oa-prev',d.prev_close?fmt(d.prev_close):'--');
  setText('oa-open',d.today_open?fmt(d.today_open):'--');
  const gap=d.opening_gap||0;
  setText('oa-gap',(gap>=0?'+':'')+gap+' pts');
  setColor('oa-gap',gap>0?'var(--green)':gap<0?'var(--red)':'var(--dim)');
  setText('oa-bias',gap>100?'🟢 GAP UP':gap<-100?'🔴 GAP DOWN':'⏳ FLAT OPEN');
}

function renderMini(prefix,spot,rsi,ema9,atm,trend,name){
  if(!spot||spot===0) return;
  setText(prefix+'-px',fmt(spot));
  setText(prefix+'-rsi',rsi?rsi.toFixed(1):'--');
  if(ema9!==null&&ema9!==undefined) setText(prefix+'-ema',fmt(ema9));
  if(atm!==null&&atm!==undefined) setText(prefix+'-atm',atm);
  const tr=$(prefix+'-tr');
  if(tr){ tr.textContent=(trend||'--').replace('_',' '); tr.style.color=trend==='TRENDING_UP'?'var(--green)':trend==='TRENDING_DOWN'?'var(--red)':'var(--gold)'; }
  const bias=$(prefix+'-bias');
  if(bias){
    if(trend==='TRENDING_UP'){ bias.textContent='🟢 BULLISH — CE side'; bias.style.background='var(--green2)'; bias.style.color='var(--green)'; bias.style.borderColor='rgba(10,158,92,.3)'; }
    else if(trend==='TRENDING_DOWN'){ bias.textContent='🔴 BEARISH — PE side'; bias.style.background='var(--red2)'; bias.style.color='var(--red)'; bias.style.borderColor='rgba(224,45,60,.3)'; }
    else{ bias.textContent='⏳ SIDEWAYS — No clear bias'; bias.style.background='var(--gold2)'; bias.style.color='var(--gold)'; bias.style.borderColor='rgba(180,83,9,.3)'; }
  }
}

function renderTrades(d){
  const closed=d.closed||[];
  if(!closed.length){
    setHtml('journal-list','<div style="padding:14px;text-align:center;color:var(--dim);font-size:11px">No trades yet</div>');
    return;
  }
  setHtml('journal-list',closed.slice(0,15).map(t=>{
    const pnl=t.pnl||0, pnlRs=t.pnl_rs||0, col=pnl>=0?'var(--green)':'var(--red)';
    return `<div style="padding:8px 10px;border-bottom:1px solid var(--border);font-size:11px">
      <div style="display:flex;justify-content:space-between;margin-bottom:2px">
        <span style="font-weight:700;color:${t.direction==='LONG'?'var(--green)':'var(--red)'}">${t.direction} ${t.atm_strike||''} ${t.option_type||''}</span>
        <span style="font-family:'JetBrains Mono',monospace;font-weight:700;color:${col}">${fmtPts(pnl)}</span>
      </div>
      <div style="display:flex;justify-content:space-between;color:var(--dim)">
        <span>${(t.entry_time||'').slice(11,16)} → ${(t.exit_time||'').slice(11,16)}</span>
        <span style="color:${col};font-weight:700">${fmtRs(pnlRs)}</span>
      </div>
      <div style="color:var(--dim);margin-top:1px;font-size:10px">${t.exit_reason||''} | ${t.strategy||'ORB'}</div>
    </div>`;
  }).join(''));
}

function addAlert(msg,isBull){
  const list=$('alert-list'); if(!list) return;
  const n=new Date();
  const t=`${String(n.getHours()).padStart(2,'0')}:${String(n.getMinutes()).padStart(2,'0')}`;
  const item=document.createElement('div');
  item.className='alert-item';
  item.innerHTML=`<div class="alert-time">${t}</div>
    <div class="alert-badge ${isBull?'bull':'bear'}">${isBull?'🟢 LONG':'🔴 SHORT'}</div>
    <div style="flex:1;font-size:11px;line-height:1.3">${msg}</div>`;
  if(list.querySelector('div[style*="Waiting"]')) list.innerHTML='';
  list.insertBefore(item,list.firstChild);
  if(list.children.length>10) list.removeChild(list.lastChild);
}

fetchData(); fetchTrades();
setInterval(fetchData,5000);
setInterval(fetchTrades,15000);
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════
# FLASK ROUTES
# ═══════════════════════════════════════════════

@app.route("/")
def index():
    from flask import Response
    return Response(TEMPLATE, mimetype='text/html; charset=utf-8')

@app.route("/api/data")
def api_data():
    data = run_pipeline()
    if "error" in data:
        return jsonify({"error": data["error"], "spot": 0, "market_status": "CLOSED",
                        "signal": data["error"], "status": "BLOCKED"})
    return jsonify(data)

@app.route("/api/trades")
def api_trades():
    closed = db_closed_trades()
    return jsonify({"open": db_open_trade(), "closed": closed, "stats": calc_stats(closed)})

@app.route("/ping")
def ping():
    return jsonify({"status": "alive", "time": datetime.datetime.now().strftime("%H:%M:%S"), "service": "GOAT PRO"})

@app.route("/paper/exit", methods=["POST"])
def paper_exit():
    d = request.get_json()
    tid = d.get("trade_id")
    ep  = float(d.get("exit_price", 0))
    dir_= d.get("direction", "LONG")
    enp = float(d.get("entry_price", 0))
    pnl = round((ep-enp) if dir_=="LONG" else (enp-ep), 2)
    lot = ENGINE.get("lot_size", 65)
    pnl_rs = round(pnl * lot, 0)
    db_close_trade(tid, ep, d.get("exit_reason","Manual"), d.get("post_note",""), d.get("emotion",""), pnl, pnl_rs=pnl_rs)
    if pnl > 0: ENGINE["trades_won"] += 1
    else:        ENGINE["trades_lost"] += 1
    ENGINE["trades_total"] += 1
    ENGINE["session_pnl"]    = round(ENGINE["session_pnl"] + pnl, 1)
    ENGINE["session_pnl_rs"] = round(ENGINE.get("session_pnl_rs", 0) + pnl_rs, 0)
    ENGINE["status"] = "BLOCKED"
    return jsonify({"status": "ok", "pnl": pnl, "pnl_rs": pnl_rs})

@app.route("/paper/intro", methods=["POST"])
def paper_intro():
    d = request.get_json()
    today = datetime.date.today().isoformat()
    db_add_intro({**d, "date": today})
    dq_score, breakdown = calc_dq_score(d.get("rule_followed",3), d.get("sl_skip","No"),
                                         d.get("revenge","No"), d.get("discipline",3))
    db_add_dq({"date": today, "dq_score": dq_score, "breakdown": breakdown})
    return jsonify({"status": "ok", "dq_score": dq_score, "breakdown": breakdown})

@app.route("/paper/clear", methods=["POST"])
def paper_clear():
    db_clear_trades()
    ENGINE.update({"trades_total":0,"trades_won":0,"trades_lost":0,"session_pnl":0.0,
                   "session_pnl_rs":0.0,"trades_today":0,"status":"BLOCKED"})
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
