#!/usr/bin/env python3
"""
  GOAT PRO — Virtual Paper Trading System
  Single-file Flask app for Render deployment.
  
  Required Environment Variables (set in Render Dashboard):
    ANGEL_API_KEY       = Your Angel One API Key
    ANGEL_CLIENT_ID     = Your Angel One Client ID
    ANGEL_MPIN          = Your Angel One MPIN
    ANGEL_TOTP_SECRET   = Your Angel One TOTP Secret (base32)
    TELEGRAM_BOT_TOKEN  = (Optional) Telegram bot token for alerts
    TELEGRAM_CHAT_ID    = (Optional) Telegram chat ID for alerts
    PORT                = Render sets this automatically
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

# ─────────────────────────────────────────────────────────
# Angel One SmartConnect
# ─────────────────────────────────────────────────────────
from SmartApi import SmartConnect

# ─────────────────────────────────────────────────────────
# yFinance fallback
# ─────────────────────────────────────────────────────────
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

app = Flask(__name__)

# ═════════════════════════════════════════════════════════
# CONFIGURATION
# ═════════════════════════════════════════════════════════

# Angel One token mappings
TOKENS = {
    "NIFTY":    {"token": "99926000", "exchange": "NSE"},
    "BANKNIFTY": {"token": "99926009", "exchange": "NSE"},
    "FINNIFTY": {"token": "99926037", "exchange": "NSE"},
    "VIX":      {"token": "99926017", "exchange": "NSE"},
}

# ── Lot Sizes (NSE revised Jan 2026) ──
LOT_SIZES = {
    "NIFTY":     65,
    "BANKNIFTY": 30,
    "FINNIFTY":  60,
}

# ── Option Premium Cache ──
OPTION_CACHE = {
    "ce_ltp": 0.0,
    "pe_ltp": 0.0,
    "ce_token": "",
    "pe_token": "",
    "last_fetch": 0,
    "ttl": 10,
}

# ── Database: PostgreSQL (preferred) → SQLite fallback ──
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_PATH = "/opt/render/project/src/trades.db"
USE_POSTGRES = bool(DATABASE_URL and POSTGRES_AVAILABLE)

if not USE_POSTGRES:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def get_db_conn():
    """Return DB connection — PostgreSQL if available, else SQLite"""
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        return conn, "postgres"
    else:
        conn = sqlite3.connect(DB_PATH)
        return conn, "sqlite"

# Candle tracking
CANDLE_5MIN = []
_candle_current = {"open": 0, "high": 0, "low": 0, "close": 0, "time": None}

# ═════════════════════════════════════════════════════════
# MARKET GUARD — Strict 9:15 AM to 3:30 PM, Mon-Fri only
# ═════════════════════════════════════════════════════════

def market_status():
    """
    Returns (status_code, message).
    STRICT RULES:
      - Weekends (Sat=5, Sun=6): CLOSED
      - Before 9:15 AM: CLOSED (ignores 9:00-9:15 pre-open)
      - 9:15 AM to 3:30 PM: OPEN
      - After 3:30 PM: CLOSED
    """
    now = datetime.datetime.now()
    weekday = now.weekday()  # Monday=0, ..., Friday=4, Saturday=5, Sunday=6
    t = now.time()

    # Weekends
    if weekday >= 5:
        return "CLOSED", "Weekend — Market Closed"

    # Before 9:15 AM (ignores pre-open 9:00-9:15)
    if t < datetime.time(9, 15):
        return "CLOSED", "Market opens at 9:15 AM"

    # After 3:30 PM
    if t > datetime.time(15, 30):
        return "CLOSED", "Market Closed after 3:30 PM"

    # Market open window
    return "OPEN", "Market Open"


def get_atm_strike(spot, interval=50):
    """Calculate ATM strike: round(spot / 50) * 50"""
    return int(round(spot / interval) * interval)


def get_expiry_label():
    """Get current weekly expiry (next Thursday)"""
    today = datetime.date.today()
    days_to_thu = (3 - today.weekday()) % 7
    if days_to_thu == 0:
        now = datetime.datetime.now().time()
        if now > datetime.time(15, 30):
            days_to_thu = 7
    expiry = today + datetime.timedelta(days=days_to_thu)
    return expiry.strftime("%d %b")


# ═════════════════════════════════════════════════════════
# CANDLE BUILDER — 5-minute candles
# ═════════════════════════════════════════════════════════

def update_candle(price):
    global _candle_current, CANDLE_5MIN
    now = datetime.datetime.now()

    if _candle_current["time"] is None:
        _candle_current = {
            "open": price, "high": price, "low": price, "close": price,
            "time": now.isoformat()
        }
        return

    prev_slot = int(datetime.datetime.fromisoformat(_candle_current["time"]).minute / 5)
    curr_slot = int(now.minute / 5)

    if curr_slot != prev_slot:
        CANDLE_5MIN.append(dict(_candle_current))
        if len(CANDLE_5MIN) > 100:
            CANDLE_5MIN.pop(0)
        _candle_current = {
            "open": price, "high": price, "low": price, "close": price,
            "time": now.isoformat()
        }
    else:
        _candle_current["high"] = max(_candle_current["high"], price)
        _candle_current["low"] = min(_candle_current["low"], price)
        _candle_current["close"] = price


# ═════════════════════════════════════════════════════════
# SQLITE DATABASE — Persistent trade logging
# ═════════════════════════════════════════════════════════

def db_init():
    """Initialize all database tables — PostgreSQL or SQLite"""
    conn, db_type = get_db_conn()
    cur = conn.cursor()

    if db_type == "postgres":
        serial = "SERIAL PRIMARY KEY"
        int_default = "INTEGER DEFAULT 1"
    else:
        serial = "INTEGER PRIMARY KEY AUTOINCREMENT"
        int_default = "INTEGER DEFAULT 1"

    cur.execute(f"""CREATE TABLE IF NOT EXISTS paper_trades (
        id {serial},
        direction TEXT, entry_price REAL, exit_price REAL,
        target REAL, sl REAL, qty {int_default},
        setup TEXT, source TEXT DEFAULT 'MANUAL',
        note TEXT, post_note TEXT, exit_reason TEXT,
        emotion TEXT, entry_time TEXT, exit_time TEXT,
        pnl REAL, status TEXT DEFAULT 'OPEN',
        decision_quality TEXT DEFAULT '-',
        emotion_score INTEGER DEFAULT 0,
        atm_strike INTEGER DEFAULT 0,
        option_type TEXT DEFAULT 'CE',
        pnl_rs REAL DEFAULT 0
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
    cur.close()
    conn.close()


def db_open_trade():
    conn, _ = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        return None
    cols = [
        'id', 'direction', 'entry_price', 'exit_price', 'target', 'sl', 'qty', 'setup',
        'source', 'note', 'post_note', 'exit_reason', 'emotion', 'entry_time',
        'exit_time', 'pnl', 'status', 'decision_quality', 'emotion_score',
        'atm_strike', 'option_type', 'pnl_rs'
    ]
    return dict(zip(cols, row))


def db_closed_trades(limit=50):
    conn, _ = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM paper_trades WHERE status='CLOSED' ORDER BY id DESC LIMIT %s" if USE_POSTGRES else
                "SELECT * FROM paper_trades WHERE status='CLOSED' ORDER BY id DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    cols = [
        'id', 'direction', 'entry_price', 'exit_price', 'target', 'sl', 'qty', 'setup',
        'source', 'note', 'post_note', 'exit_reason', 'emotion', 'entry_time',
        'exit_time', 'pnl', 'status', 'decision_quality', 'emotion_score',
        'atm_strike', 'option_type', 'pnl_rs'
    ]
    return [dict(zip(cols, r)) for r in rows]


def db_insert_trade(t):
    conn, db_type = get_db_conn()
    cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    sql = f"""INSERT INTO paper_trades
        (direction, entry_price, target, sl, qty, setup, source, note, entry_time, status,
         decision_quality, emotion_score, atm_strike, option_type, pnl_rs)
        VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})"""
    cur.execute(sql, (
        t['direction'], t['entry_price'], t['target'], t['sl'], t.get('qty', 65),
        t['setup'], t.get('source', 'MANUAL'), t.get('note', ''),
        t['entry_time'], 'OPEN', t.get('decision_quality', '-'),
        t.get('emotion_score', 0), t.get('atm_strike', 0),
        t.get('option_type', 'CE'), t.get('pnl_rs', 0)
    ))
    conn.commit()
    cur.close(); conn.close()


def db_close_trade(trade_id, exit_price, exit_reason, post_note, emotion, pnl,
                   decision_quality='-', emotion_score=0, pnl_rs=0):
    conn, db_type = get_db_conn()
    cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    sql = f"""UPDATE paper_trades SET exit_price={ph}, exit_reason={ph}, post_note={ph},
        emotion={ph}, exit_time={ph}, pnl={ph}, pnl_rs={ph}, status='CLOSED',
        decision_quality={ph}, emotion_score={ph} WHERE id={ph}"""
    cur.execute(sql, (
        exit_price, exit_reason, post_note, emotion,
        time.strftime("%H:%M:%S"), pnl, pnl_rs, decision_quality, emotion_score, trade_id
    ))
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
    sql = f"INSERT INTO introspection (date, rule_followed, sl_skip, revenge, discipline, tomorrow_rule, created_at) VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})"
    cur.execute(sql, (data['date'], data['rule_followed'], data['sl_skip'], data['revenge'],
        data['discipline'], data['tomorrow_rule'], time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    cur.close(); conn.close()


def db_get_intros(limit=10):
    conn, db_type = get_db_conn()
    cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    cur.execute(f"SELECT * FROM introspection ORDER BY id DESC LIMIT {ph}", (limit,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    cols = ['id', 'date', 'rule_followed', 'sl_skip', 'revenge',
            'discipline', 'tomorrow_rule', 'created_at']
    return [dict(zip(cols, r)) for r in rows]


def db_add_dq(data):
    conn, db_type = get_db_conn()
    cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    cur.execute(f"INSERT INTO decision_quality (date, dq_score, breakdown, created_at) VALUES ({ph},{ph},{ph},{ph})",
        (data['date'], data['dq_score'], data['breakdown'], time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    cur.close(); conn.close()


def db_get_dqs(limit=7):
    conn, db_type = get_db_conn()
    cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    cur.execute(f"SELECT * FROM decision_quality ORDER BY id DESC LIMIT {ph}", (limit,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    cols = ['id', 'date', 'dq_score', 'breakdown', 'created_at']
    return [dict(zip(cols, r)) for r in rows]


def calc_dq_score(rule_followed, sl_skip, revenge, discipline):
    base = (int(rule_followed) + int(discipline)) * 10
    score = base
    penalties = []
    if sl_skip == 'Yes':
        score -= 25
        penalties.append("SL Skip: -25")
    if revenge == 'Yes':
        score -= 25
        penalties.append("Revenge: -25")
    score = max(0, min(100, score))
    parts = [f"Base={base}"] + penalties + [f"Final={score}"]
    return score, " | ".join(parts)


def calc_stats(trades):
    if not trades:
        return {
            'total': 0, 'wins': 0, 'losses': 0, 'win_rate': 0, 'total_pnl': 0,
            'avg_win': 0, 'avg_loss': 0, 'best': 0, 'worst': 0, 'expectancy': 0
        }
    wins = [t for t in trades if (t['pnl'] or 0) > 0]
    losses = [t for t in trades if (t['pnl'] or 0) <= 0]
    total = len(trades)
    wr = round(len(wins) / total * 100) if total else 0
    tot_pnl = round(sum(t['pnl'] or 0 for t in trades), 1)
    avg_win = round(sum(t['pnl'] for t in wins) / len(wins), 1) if wins else 0
    avg_loss = round(sum(t['pnl'] for t in losses) / len(losses), 1) if losses else 0
    best = round(max(t['pnl'] or 0 for t in trades), 1)
    worst = round(min(t['pnl'] or 0 for t in trades), 1)
    exp = round((wr / 100) * avg_win - ((100 - wr) / 100) * abs(avg_loss), 2)
    return {
        'total': total, 'wins': len(wins), 'losses': len(losses),
        'win_rate': wr, 'total_pnl': tot_pnl, 'avg_win': avg_win,
        'avg_loss': avg_loss, 'best': best, 'worst': worst, 'expectancy': exp
    }


# Initialize database
db_init()


# ═════════════════════════════════════════════════════════
# ANGEL ONE SESSION MANAGEMENT
# ═════════════════════════════════════════════════════════

SESSION_CACHE = {"obj": None, "logged_in_at": 0, "ttl_seconds": 3600}


def get_session():
    """
    Returns (smart_connect_obj, error_string).
    Checks cache first, then logs in via Angel One.
    """
    now = time.time()
    if SESSION_CACHE["obj"] and (now - SESSION_CACHE["logged_in_at"] < SESSION_CACHE["ttl_seconds"]):
        return SESSION_CACHE["obj"], None

    # Check all required env vars
    for key in ["ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_MPIN", "ANGEL_TOTP_SECRET"]:
        if not os.environ.get(key):
            return None, f"ENV MISSING: {key}"

    try:
        totp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"]).now()
        obj = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
        s = obj.generateSession(os.environ["ANGEL_CLIENT_ID"], os.environ["ANGEL_MPIN"], totp)
        if not s.get("status"):
            return None, "LOGIN FAILED"
        SESSION_CACHE.update({"obj": obj, "logged_in_at": now})
        return obj, None
    except Exception as e:
        return None, str(e)


def get_both_premiums(index, atm_strike, expiry_str):
    """
    Fetch CE and PE live premium from Angel One NFO.
    expiry_str format: '18JUN2025' (Angel One symbol format)
    Returns (ce_ltp, pe_ltp) as floats.
    """
    try:
        now = time.time()
        if now - OPTION_CACHE["last_fetch"] < OPTION_CACHE["ttl"]:
            return OPTION_CACHE["ce_ltp"], OPTION_CACHE["pe_ltp"]

        obj, err = get_session()
        if err or not obj:
            return 0.0, 0.0

        ce_symbol = f"{index}{expiry_str}{atm_strike}CE"
        pe_symbol = f"{index}{expiry_str}{atm_strike}PE"
        ce_ltp, pe_ltp = 0.0, 0.0

        try:
            r = obj.searchScrip("NFO", ce_symbol)
            if r and r.get("data"):
                tok = r["data"][0]["symboltoken"]
                lr = obj.ltpData("NFO", ce_symbol, tok)
                ce_ltp = float(lr["data"]["ltp"]) if lr and lr.get("data") else 0.0
        except Exception:
            pass

        try:
            r = obj.searchScrip("NFO", pe_symbol)
            if r and r.get("data"):
                tok = r["data"][0]["symboltoken"]
                lr = obj.ltpData("NFO", pe_symbol, tok)
                pe_ltp = float(lr["data"]["ltp"]) if lr and lr.get("data") else 0.0
        except Exception:
            pass

        OPTION_CACHE.update({
            "ce_ltp": ce_ltp,
            "pe_ltp": pe_ltp,
            "last_fetch": now
        })
        return ce_ltp, pe_ltp
    except Exception:
        return 0.0, 0.0


def get_expiry_str_for_angel(weeks_ahead=0):
    """
    Returns expiry string in Angel One format: e.g. '18JUN2025'
    Nifty weekly expiry = Thursday
    """
    today = datetime.date.today()
    days_until_thursday = (3 - today.weekday()) % 7
    if days_until_thursday == 0 and datetime.datetime.now().time() > datetime.time(15, 30):
        days_until_thursday = 7
    expiry_date = today + datetime.timedelta(days=days_until_thursday + (weeks_ahead * 7))
    return expiry_date.strftime("%d%b%Y").upper()




def get_nifty_yfinance():
    """Fallback: fetch NIFTY spot from yfinance"""
    if not YFINANCE_AVAILABLE:
        return None
    try:
        t = yf.Ticker("^NSEI")
        return float(t.fast_info['last_price'])
    except Exception:
        return None


def get_vix_yfinance():
    """Fallback: fetch India VIX from yfinance"""
    if not YFINANCE_AVAILABLE:
        return None
    try:
        t = yf.Ticker("^INDIAVIX")
        return float(t.fast_info['last_price'])
    except Exception:
        return None


# ═════════════════════════════════════════════════════════
# TELEGRAM ALERTS
# ═════════════════════════════════════════════════════════

def _tg(msg):
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": chat, "text": msg, "parse_mode": "HTML"},
            timeout=4
        )
    except Exception:
        pass


def tg(msg):
    threading.Thread(target=_tg, args=(msg,), daemon=True).start()


# ═════════════════════════════════════════════════════════
# GOAT BRAIN — AI Trade Reasoning Engine
# ═════════════════════════════════════════════════════════

def goat_brain_reasons(direction, spot, vix, velocity, checklist_passed, atm_strike, option_type):
    """Generate the 5-point 'Kyun Liya?' AI reasoning checklist"""
    reasons = []

    # Checklist item 0: Price vs round level
    base100 = (spot // 100) * 100
    if checklist_passed[0]:
        reasons.append(f"NIFTY {spot} is above round level {base100} \u2014 bullish zone")
    else:
        reasons.append(f"NIFTY {spot} below/at round level {base100} \u2014 caution")

    # Checklist item 1: Velocity / momentum
    if checklist_passed[1]:
        reasons.append(f"Momentum is positive (velocity +{velocity:.1f}) \u2014 buying pressure")
    else:
        reasons.append(f"Momentum weak or negative (velocity {velocity:+.1f})")

    # Checklist item 2: Distance from round number
    if checklist_passed[2]:
        reasons.append("Price is safely away from round number trap \u2014 no whipsaw risk")
    else:
        reasons.append("Too close to round number \u2014 whipsaw risk detected")

    # Checklist item 3: VIX check
    if checklist_passed[3]:
        reasons.append(f"VIX at {vix:.1f} \u2014 fear is low, safe to trade")
    else:
        reasons.append(f"VIX at {vix:.1f} \u2014 elevated volatility, reduce size")

    # Checklist item 4: Within valid strike range
    if checklist_passed[4]:
        reasons.append("Price within valid ATM strike range \u2014 good R:R")
    else:
        reasons.append("Price near expiry danger zone \u2014 reduce exposure")

    # Final signal summary
    reasons.append(f"\u2192 Signal: {direction} {atm_strike} {option_type}")

    return reasons


# ═════════════════════════════════════════════════════════
# TRADING ENGINE — Core State Machine
# ═════════════════════════════════════════════════════════

ENGINE = {
    "last_update": 0,
    "tick_ttl": 5,
    "payload": None,
    "last_spot": 0.0,
    "velocity": 0.0,
    "status": "BLOCKED",
    "direction": "LONG",
    "option_type": "CE",
    "entry": 0.0,
    "target": 0.0,
    "sl": 0.0,
    "signal": "SYSTEM INITIALIZING...",
    "brain_reasons": [],
    "atm_strike": 0,
    "session_pnl": 0.0,
    "trades_total": 0,
    "trades_won": 0,
    "trades_lost": 0,
    "last_signal_sent": "",
    "data_source": "\u2014",
    "last_candle_signal": 0,
    # Option premium tracking
    "ce_premium": 0.0,
    "pe_premium": 0.0,
    "option_entry_premium": 0.0,
    "lot_size": 65,
    "active_index": "NIFTY",
    "session_pnl_rs": 0.0,
}


def run_pipeline():
    """
    Main pipeline: check market hours, fetch data, run strategy, manage trades.
    Returns a dict payload for the frontend.
    """
    global ENGINE

    now = time.time()

    # Cache check
    if ENGINE["payload"] and (now - ENGINE["last_update"] < ENGINE["tick_ttl"]):
        return ENGINE["payload"]

    # Market hours check
    mstatus, mmsg = market_status()
    expiry_label = get_expiry_label()

    if mstatus != "OPEN":
        ENGINE["status"] = "BLOCKED"
        ENGINE["signal"] = mmsg
        payload = {
            "spot": ENGINE.get("last_spot", 0),
            "vix": 15.0,
            "velocity": 0,
            "status": "BLOCKED",
            "signal": mmsg,
            "brain_reasons": [],
            "entry": 0,
            "target": 0,
            "sl": 0,
            "chk": [False] * 5,
            "pass_count": 0,
            "total": ENGINE["trades_total"],
            "wins": ENGINE["trades_won"],
            "losses": ENGINE["trades_lost"],
            "pnl": round(ENGINE["session_pnl"], 1),
            "win_rate": 0,
            "market_status": mstatus,
            "market_msg": mmsg,
            "atm_strike": ENGINE.get("atm_strike", 0),
            "option_type": ENGINE.get("option_type", "CE"),
            "direction": ENGINE.get("direction", "LONG"),
            "data_source": ENGINE["data_source"],
            "expiry": expiry_label,
            "candles": CANDLE_5MIN[-50:],
            "current_candle": _candle_current,
        }
        ENGINE.update({"last_update": now, "payload": payload})
        return payload

    # ── Fetch market data ────────────────────────────
    spot = None
    vix = None
    data_source = "Angel One"

    obj, err = get_session()
    if obj:
        try:
            nr = obj.ltpData("NSE", "NIFTY", TOKENS["NIFTY"]["token"])
            vr = obj.ltpData("NSE", "INDIAVIX", TOKENS["VIX"]["token"])
            if nr.get("status") and "data" in nr:
                spot = float(nr["data"]["ltp"])
            if vr.get("status") and "data" in vr:
                vix = float(vr["data"]["ltp"])
        except Exception:
            SESSION_CACHE["logged_in_at"] = 0  # Force re-login

    # Fallback to yfinance
    if spot is None:
        spot = get_nifty_yfinance()
        data_source = "yfinance" if spot else "\u2014"
    if vix is None:
        vix = get_vix_yfinance() or 15.0

    if spot is None:
        return ENGINE["payload"] or {"error": "All data sources failed"}

    ENGINE["data_source"] = data_source
    update_candle(spot)

    # Velocity calculation
    if ENGINE["last_spot"] > 0:
        ENGINE["velocity"] = round(spot - ENGINE["last_spot"], 2)
    ENGINE["last_spot"] = spot
    vel = ENGINE["velocity"]

    # ── ATM Strike Calculation: round(spot / 50) * 50 ──
    atm = get_atm_strike(spot, interval=50)
    ENGINE["atm_strike"] = atm

    # ── Lot Size (current index) ──
    active_index = ENGINE.get("active_index", "NIFTY")
    lot_size = LOT_SIZES.get(active_index, 65)
    ENGINE["lot_size"] = lot_size
    ENGINE["active_index"] = active_index

    # ── Fetch CE/PE Live Premium ──
    try:
        expiry_str = get_expiry_str_for_angel(0)
        ce_ltp, pe_ltp = get_both_premiums(active_index, atm, expiry_str)
        ENGINE["ce_premium"] = ce_ltp
        ENGINE["pe_premium"] = pe_ltp
    except Exception:
        ce_ltp = ENGINE.get("ce_premium", 0.0)
        pe_ltp = ENGINE.get("pe_premium", 0.0)

    # ── VIX-based SL and Target ──
    vix_mult = vix / 15.0
    sl_pts = round(40 * vix_mult, 1)
    tgt_pts = round(90 * vix_mult, 1)

    # ── 5-Point Checklist ──
    base100 = (spot // 100) * 100
    base50 = (spot // 50) * 50
    dist_round = abs(spot - base50)

    chk = [
        spot > base100,           # 0: NIFTY above round level
        vel > 0,                   # 1: Velocity positive
        dist_round > 10,           # 2: Away from round number
        vix < 18.0,                # 3: VIX safe
        (spot - base100) < (0.008 * spot)  # 4: Within valid strike range
    ]
    all_pass = all(chk)
    pass_count = sum(chk)

    # ── Direction logic: LONG (CE) vs SHORT (PE) ──
    if vel > 0.5 and chk[0]:
        direction = "LONG"
        option_type = "CE"
    elif vel < -0.5 and not chk[0]:
        direction = "SHORT"
        option_type = "PE"
    else:
        direction = ENGINE.get("direction", "LONG")
        option_type = ENGINE.get("option_type", "CE")

    ENGINE["direction"] = direction
    ENGINE["option_type"] = option_type

    open_t = db_open_trade()
    candle_ok = (now - ENGINE["last_candle_signal"]) > 300  # 5-min cooldown

    # ── Manage active trade ──
    if ENGINE["status"] == "TRADE_ACTIVE":
        if open_t and open_t['source'] == 'AUTO':
            if direction == "LONG":
                hit_tgt = spot >= ENGINE["target"]
                hit_sl = spot <= ENGINE["sl"]
            else:
                hit_tgt = spot <= ENGINE["target"]
                hit_sl = spot >= ENGINE["sl"]

            if hit_tgt:
                pnl_pts = round(abs(ENGINE["target"] - open_t['entry_price']), 1)
                pnl_rs = round(pnl_pts * lot_size, 0)
                db_close_trade(open_t['id'], spot, 'Target Hit', 'Auto-closed', 'Calm', pnl_pts, pnl_rs=pnl_rs)
                ENGINE["trades_won"] += 1
                ENGINE["trades_total"] += 1
                ENGINE["session_pnl"] += pnl_pts
                ENGINE["session_pnl_rs"] += pnl_rs
                ENGINE["status"] = "BLOCKED"
                ENGINE["signal"] = f"TARGET HIT +{pnl_pts} pts | +₹{int(pnl_rs):,}"
                tg(f"TARGET HIT +{pnl_pts} pts | +₹{int(pnl_rs)} ({lot_size} qty) at {spot}")

            elif hit_sl:
                pnl_pts = round(abs(open_t['entry_price'] - ENGINE["sl"]), 1)
                pnl_rs = round(pnl_pts * lot_size, 0)
                db_close_trade(open_t['id'], spot, 'Stoploss Hit', 'Auto-closed', 'Calm', -pnl_pts, pnl_rs=-pnl_rs)
                ENGINE["trades_lost"] += 1
                ENGINE["trades_total"] += 1
                ENGINE["session_pnl"] -= pnl_pts
                ENGINE["session_pnl_rs"] -= pnl_rs
                ENGINE["status"] = "BLOCKED"
                ENGINE["signal"] = f"SL HIT -{pnl_pts} pts | -₹{int(pnl_rs):,}"
                tg(f"SL HIT -{pnl_pts} pts | -₹{int(pnl_rs)} ({lot_size} qty) at {spot}")

    # ── Entry signal generation ──
    elif ENGINE["status"] in ("BLOCKED", "SETUP_READY"):
        if not all_pass:
            ENGINE.update({
                "status": "BLOCKED",
                "signal": f"Waiting \u2014 {pass_count}/5 conditions met",
                "entry": 0.0, "target": 0.0, "sl": 0.0,
                "brain_reasons": []
            })
        else:
            if candle_ok:
                if direction == "LONG":
                    entry = round(spot - 5, 2)
                    target = round(entry + tgt_pts, 2)
                    sl = round(entry - sl_pts, 2)
                else:  # SHORT
                    entry = round(spot + 5, 2)
                    target = round(entry - tgt_pts, 2)
                    sl = round(entry + sl_pts, 2)

                ENGINE.update({
                    "status": "SETUP_READY",
                    "entry": entry,
                    "target": target,
                    "sl": sl,
                    "signal": f"SETUP READY \u2014 {direction} | {atm} {option_type}",
                    "brain_reasons": goat_brain_reasons(direction, spot, vix, vel, chk, atm, option_type)
                })
                ENGINE["last_candle_signal"] = now

            # Trigger point logic for SHORT and LONG
            trig_long = direction == "LONG" and spot <= (ENGINE["entry"] + 8) and vel > 0.3
            trig_short = direction == "SHORT" and spot >= (ENGINE["entry"] - 8) and vel < -0.3

            if (trig_long or trig_short) and not open_t and ENGINE["status"] == "SETUP_READY":
                sig_key = f"{direction}_{ENGINE['entry']}"
                if ENGINE["last_signal_sent"] != sig_key:
                    ENGINE["last_signal_sent"] = sig_key
                    ENGINE["status"] = "TRADE_ACTIVE"
                    ENGINE["signal"] = f"AUTO-ENTERED {direction} at {spot} | {atm} {option_type}"
                    reasons = goat_brain_reasons(direction, spot, vix, vel, chk, atm, option_type)
                    ENGINE["brain_reasons"] = reasons

                    # Record option premium at entry
                    entry_premium = ENGINE["ce_premium"] if option_type == "CE" else ENGINE["pe_premium"]
                    ENGINE["option_entry_premium"] = entry_premium

                    db_insert_trade({
                        "direction": direction,
                        "entry_price": spot,
                        "target": ENGINE["target"],
                        "sl": ENGINE["sl"],
                        "qty": lot_size,
                        "setup": "GOAT Signal",
                        "source": "AUTO",
                        "note": "\n".join(reasons) + f"\nPremium Entry: ₹{entry_premium}",
                        "entry_time": time.strftime("%H:%M:%S"),
                        "atm_strike": atm,
                        "option_type": option_type
                    })
                    tg(f"AUTO {direction}\n{spot} | {atm} {option_type}\nPremium: ₹{entry_premium}\nTGT:{ENGINE['target']} SL:{ENGINE['sl']}\nLot: {lot_size} qty")

    # Stats
    total = ENGINE["trades_total"]
    win_rate = round(ENGINE["trades_won"] / total * 100) if total else 0

    payload = {
        "spot": round(spot, 2),
        "vix": round(vix, 2),
        "velocity": vel,
        "status": ENGINE["status"],
        "signal": ENGINE["signal"],
        "brain_reasons": ENGINE["brain_reasons"],
        "entry": ENGINE["entry"],
        "target": ENGINE["target"],
        "sl": ENGINE["sl"],
        "chk": chk,
        "pass_count": pass_count,
        "total": total,
        "wins": ENGINE["trades_won"],
        "losses": ENGINE["trades_lost"],
        "pnl": round(ENGINE["session_pnl"], 1),
        "win_rate": win_rate,
        "market_status": mstatus,
        "market_msg": mmsg,
        "atm_strike": atm,
        "option_type": option_type,
        "direction": direction,
        "data_source": data_source,
        "expiry": expiry_label,
        "candles": CANDLE_5MIN[-50:],
        "current_candle": _candle_current,
        # ── Option Premium (live) ──
        "ce_premium": round(ENGINE.get("ce_premium", 0.0), 2),
        "pe_premium": round(ENGINE.get("pe_premium", 0.0), 2),
        "option_entry_premium": round(ENGINE.get("option_entry_premium", 0.0), 2),
        "lot_size": lot_size,
        "active_index": active_index,
        # ── Real ₹ P&L ──
        "session_pnl_rs": round(ENGINE.get("session_pnl_rs", 0.0), 0),
        "live_pnl_rs": round(
            (ENGINE.get("ce_premium", 0.0) - ENGINE.get("option_entry_premium", 0.0)) * lot_size
            if ENGINE["status"] == "TRADE_ACTIVE" and ENGINE.get("option_entry_premium", 0.0) > 0
            else 0.0, 0
        ),
    }

    ENGINE.update({"last_update": now, "payload": payload})
    return payload


# ═════════════════════════════════════════════════════════
# HTML TEMPLATE — Premium Dark UI (All-in-One)
# ═════════════════════════════════════════════════════════

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GOAT PRO \u2014 Virtual Trading System</title>
<script src="https://cdn.tailwindcss.com"></script>
<script>
tailwind.config = {
  theme: {
    extend: {
      colors: {
        'goat-green': '#00FF41',
        'goat-red': '#FF3B3B',
        'goat-cyan': '#00F0FF',
        'goat-amber': '#FFD700',
        'surface': '#121212',
        'base': '#050505',
      },
      fontFamily: {
        mono: ['"Space Mono"', 'monospace'],
      }
    }
  }
}
</script>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
  * { box-sizing: border-box; }
  body { background: #050505; color: #e2e8f0; font-family: 'Inter', 'Segoe UI', sans-serif; margin: 0; }
  .font-mono-data { font-family: 'Space Mono', monospace; }
  .glass {
    background: rgba(18,18,18,0.85);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    backdrop-filter: blur(12px);
  }
  .glow-green { box-shadow: 0 0 20px rgba(0,255,65,0.12); }
  .glow-red   { box-shadow: 0 0 20px rgba(255,59,59,0.12); }
  .glow-amber { box-shadow: 0 0 20px rgba(255,215,0,0.12); }
  .text-muted { color: #666; }
  .price-up   { color: #00FF41; }
  .price-down { color: #FF3B3B; }
  .tab-btn {
    cursor: pointer; padding: 10px 20px; border-radius: 999px; font-size: 11px; font-weight: 700;
    letter-spacing: 0.1em; text-transform: uppercase; transition: all 0.2s;
    border: 1px solid rgba(255,255,255,0.1);
  }
  .tab-btn.active {
    background: #00FF41; color: #000; border-color: #00FF41;
    box-shadow: 0 0 15px rgba(0,255,65,0.3);
  }
  .tab-btn:not(.active) { background: rgba(18,18,18,0.8); color: #e2e8f0; }
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  .seg-btn {
    cursor: pointer; padding: 10px 20px; font-size: 11px; font-weight: 700;
    letter-spacing: 0.1em; text-transform: uppercase; transition: all 0.2s;
    border-bottom: 2px solid transparent; color: #666; background: transparent;
  }
  .seg-btn.active { border-bottom-color: #00FF41; color: #00FF41; }
  .marquee-track { display: flex; animation: marquee 30s linear infinite; width: max-content; }
  @keyframes marquee { from{transform:translateX(0)} to{transform:translateX(-50%)} }
  .pulse-dot { animation: pulseDot 1.5s ease-in-out infinite; }
  @keyframes pulseDot { 0%,100%{opacity:1;transform:scale(1)} 50%{opacity:0.5;transform:scale(0.8)} }
  .signal-trade  { background: rgba(0,255,65,0.08);  border: 1px solid rgba(0,255,65,0.3); }
  .signal-setup  { background: rgba(255,215,0,0.08); border: 1px solid rgba(255,215,0,0.3); }
  .signal-block  { background: rgba(30,30,30,0.8);   border: 1px solid rgba(255,255,255,0.08); }
  .bg-orbital {
    position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 0; pointer-events: none;
    background:
      radial-gradient(ellipse at 20% 50%, rgba(0,255,65,0.04) 0%, transparent 50%),
      radial-gradient(ellipse at 80% 20%, rgba(0,240,255,0.04) 0%, transparent 50%),
      radial-gradient(ellipse at 50% 80%, rgba(255,215,0,0.03) 0%, transparent 50%);
  }
  .ai-line { animation: fadeInUp 0.3s ease forwards; opacity: 0; }
  @keyframes fadeInUp { from{opacity:0;transform:translateY(4px)} to{opacity:1;transform:translateY(0)} }
  ::-webkit-scrollbar { width: 4px; }
  ::-webkit-scrollbar-track { background: #0a0a0a; }
  ::-webkit-scrollbar-thumb { background: #222; border-radius: 2px; }
  .metric-card {
    background: rgba(18,18,18,0.8); border: 1px solid rgba(255,255,255,0.06);
    border-radius: 8px; padding: 16px;
  }
  .checklist-pass { background: rgba(0,255,65,0.07); color: #86efac; border-left: 3px solid #00FF41; }
  .checklist-fail { background: rgba(255,59,59,0.05); color: #fca5a5; border-left: 3px solid #FF3B3B; }
</style>
</head>
<body>

<!-- Orbital BG -->
<div class="bg-orbital"></div>

<div class="relative z-10 max-w-screen-2xl mx-auto">

<!-- ===== HEADER ===== -->
<header style="border-bottom:1px solid rgba(255,255,255,0.08); background:rgba(18,18,18,0.92); backdrop-filter:blur(12px); position:sticky; top:0; z-index:50;">
  <div class="flex items-center justify-between px-6 py-3">
    <!-- Logo -->
    <div class="flex items-center gap-3">
      <div style="background:rgba(0,255,65,0.1);border:1px solid rgba(0,255,65,0.3);border-radius:8px;padding:8px;">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#00FF41" stroke-width="2">
          <polyline points="23 6 13.5 15.5 8.5 10.5 1 18"></polyline>
          <polyline points="17 6 23 6 23 12"></polyline>
        </svg>
      </div>
      <div>
        <h1 class="text-xl font-bold text-white tracking-tight"><span class="text-goat-green">GOAT</span> PRO</h1>
        <p style="font-size:9px;letter-spacing:0.15em;text-transform:uppercase;color:#555;">Virtual Trading System</p>
      </div>
    </div>
    <!-- Center Disclaimer -->
    <div class="hidden md:flex items-center gap-2 px-4 py-1" style="border:1px solid rgba(255,215,0,0.2);border-radius:6px;background:rgba(255,215,0,0.05);">
      <span style="color:#FFD700;font-size:10px;font-weight:700;letter-spacing:0.1em;">NOT FINANCIAL ADVICE</span>
      <span class="text-muted" style="font-size:9px;">Paper Trading Only</span>
    </div>
    <!-- Clock + Market Status -->
    <div class="flex items-center gap-4">
      <div class="flex items-center gap-2 font-mono-data text-sm text-white">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#00F0FF" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        <span id="clock">--:--:--</span>
        <span style="font-size:9px;color:#555;text-transform:uppercase;">IST</span>
      </div>
      <div id="mkt-badge" class="flex items-center gap-1 px-3 py-1 rounded text-xs font-bold">
        <span class="pulse-dot" style="width:7px;height:7px;border-radius:50%;display:inline-block;"></span>
        <span id="mkt-text">Checking...</span>
      </div>
      <div style="font-size:10px;color:#444;" id="data-src">Data: \u2014</div>
    </div>
  </div>
</header>

<!-- ===== TICKER BAR ===== -->
<div style="border-bottom:1px solid rgba(255,255,255,0.06);background:rgba(10,10,10,0.6);overflow:hidden;">
  <div class="marquee-track py-2" id="ticker-bar">
    <div class="flex items-center gap-3 px-4 whitespace-nowrap" style="border-right:1px solid rgba(255,255,255,0.05);">
      <span style="font-size:10px;font-weight:700;color:#555;text-transform:uppercase;">NIFTY 50</span>
      <span class="font-mono-data text-xs text-white" id="ticker-nifty">\u2014</span>
      <span class="font-mono-data price-up" style="font-size:10px;" id="ticker-vel">\u2014</span>
    </div>
    <div class="flex items-center gap-3 px-4 whitespace-nowrap" style="border-right:1px solid rgba(255,255,255,0.05);">
      <span style="font-size:10px;font-weight:700;color:#555;text-transform:uppercase;">ATM STRIKE</span>
      <span class="font-mono-data text-xs text-white" id="ticker-atm">\u2014</span>
      <span style="font-size:10px;font-weight:700;color:#00FF41;" id="ticker-otype">CE</span>
    </div>
    <div class="flex items-center gap-3 px-4 whitespace-nowrap" style="border-right:1px solid rgba(255,255,255,0.05);">
      <span style="font-size:10px;font-weight:700;color:#555;text-transform:uppercase;">VIX</span>
      <span class="font-mono-data text-xs text-white" id="ticker-vix">\u2014</span>
    </div>
    <div class="flex items-center gap-3 px-4 whitespace-nowrap" style="border-right:1px solid rgba(255,255,255,0.05);">
      <span style="font-size:10px;font-weight:700;color:#555;text-transform:uppercase;">SESSION P&L</span>
      <span class="font-mono-data text-xs font-bold" id="ticker-pnl">—</span>
      <span class="font-mono-data text-xs font-bold" id="session-pnl-rs" style="font-size:10px;"></span>
    </div>
    <div class="flex items-center gap-3 px-4 whitespace-nowrap" style="border-right:1px solid rgba(255,255,255,0.05);">
      <span style="font-size:10px;font-weight:700;color:#555;text-transform:uppercase;">LIVE P&L</span>
      <span class="font-mono-data text-xs font-bold" id="live-pnl-rs" style="font-size:10px;">—</span>
    </div>
    <div class="flex items-center gap-3 px-4 whitespace-nowrap" style="border-right:1px solid rgba(255,255,255,0.05);">
      <span style="font-size:10px;font-weight:700;color:#555;text-transform:uppercase;">WIN RATE</span>
      <span class="font-mono-data text-xs text-white" id="ticker-wr">\u2014</span>
    </div>
    <div class="flex items-center gap-3 px-4 whitespace-nowrap">
      <span style="font-size:10px;font-weight:700;color:#555;text-transform:uppercase;">EXPIRY</span>
      <span class="font-mono-data text-xs text-goat-amber" id="ticker-exp">\u2014</span>
    </div>
    <!-- Duplicate for seamless loop -->
    <div class="flex items-center gap-3 px-4 whitespace-nowrap" style="border-right:1px solid rgba(255,255,255,0.05);">
      <span style="font-size:10px;font-weight:700;color:#555;text-transform:uppercase;">NIFTY 50</span>
      <span class="font-mono-data text-xs text-white" id="ticker-nifty2">\u2014</span>
    </div>
    <div class="flex items-center gap-3 px-4 whitespace-nowrap" style="border-right:1px solid rgba(255,255,255,0.05);">
      <span style="font-size:10px;font-weight:700;color:#555;text-transform:uppercase;">ATM STRIKE</span>
      <span class="font-mono-data text-xs text-white" id="ticker-atm2">\u2014</span>
    </div>
    <div class="flex items-center gap-3 px-4 whitespace-nowrap" style="border-right:1px solid rgba(255,255,255,0.05);">
      <span style="font-size:10px;font-weight:700;color:#555;text-transform:uppercase;">VIX</span>
      <span class="font-mono-data text-xs text-white" id="ticker-vix2">\u2014</span>
    </div>
    <div class="flex items-center gap-3 px-4 whitespace-nowrap" style="border-right:1px solid rgba(255,255,255,0.05);">
      <span style="font-size:10px;font-weight:700;color:#555;text-transform:uppercase;">SESSION P&L</span>
      <span class="font-mono-data text-xs font-bold" id="ticker-pnl2">\u2014</span>
    </div>
    <div class="flex items-center gap-3 px-4 whitespace-nowrap" style="border-right:1px solid rgba(255,255,255,0.05);">
      <span style="font-size:10px;font-weight:700;color:#555;text-transform:uppercase;">WIN RATE</span>
      <span class="font-mono-data text-xs text-white" id="ticker-wr2">\u2014</span>
    </div>
  </div>
</div>

<!-- ===== CONTROL DECK ===== -->
<section class="px-4 pt-5 pb-3">
  <div class="glass glow-green">
    <!-- Segment Selectors -->
    <div style="display:flex;align-items:center;border-bottom:1px solid rgba(255,255,255,0.08);">
      <button class="seg-btn active" onclick="setSegment('NIFTY',this)">NIFTY</button>
      <button class="seg-btn" onclick="setSegment('BANKNIFTY',this)">BANKNIFTY</button>
      <button class="seg-btn" onclick="setSegment('FINNIFTY',this)">FINNIFTY</button>
    </div>
    <!-- Metrics Grid -->
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:rgba(255,255,255,0.04);">
      <!-- Spot -->
      <div style="background:#0d0d0d;padding:20px;">
        <div class="flex items-center gap-2 mb-2">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#00F0FF" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>
          <span style="font-size:10px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:0.1em;">Spot</span>
        </div>
        <div class="font-mono-data text-2xl font-bold text-white" id="spot">\u2014</div>
        <div class="mt-1 font-mono-data text-xs" id="vel-display" style="color:#00FF41;">\u2014</div>
      </div>
      <!-- VIX -->
      <div style="background:#0d0d0d;padding:20px;">
        <div class="flex items-center gap-2 mb-2">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#00F0FF" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
          <span style="font-size:10px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:0.1em;">VIX</span>
        </div>
        <div class="font-mono-data text-2xl font-bold text-goat-cyan" id="vix">\u2014</div>
        <div class="mt-1" id="vix-badge"></div>
      </div>
      <!-- Expiry -->
      <div style="background:#0d0d0d;padding:20px;">
        <div class="flex items-center gap-2 mb-2">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#FFD700" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
          <span style="font-size:10px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:0.1em;">Expiry</span>
        </div>
        <div class="font-mono-data text-2xl font-bold text-goat-amber" id="expiry">\u2014</div>
        <div class="mt-1 text-xs text-muted">Current Week</div>
      </div>
      <!-- ATM Strike -->
      <div style="background:#0d0d0d;padding:20px;">
        <div class="flex items-center gap-2 mb-2">
          <span style="font-size:10px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:0.1em;">ATM Strike</span>
        </div>
        <div class="font-mono-data font-bold text-white" style="font-size:28px;" id="atm">\u2014</div>
        <div class="mt-1 text-xs text-muted">Auto-calculated (round(spot/50)*50)</div>
      </div>
    </div>
    <!-- CE / PE Options Cards -->
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:1px;background:rgba(255,255,255,0.04);">
      <!-- CE Card -->
      <div id="ce-card" style="background:#0d0d0d;padding:20px;border-left:3px solid #00FF41;">
        <div class="flex items-center justify-between mb-2">
          <span id="ce-label" style="font-size:10px;font-weight:700;color:#00FF41;text-transform:uppercase;">\u2014 CE</span>
          <span style="font-size:9px;padding:2px 8px;border-radius:4px;background:rgba(0,255,65,0.1);color:#00FF41;border:1px solid rgba(0,255,65,0.2);">CALL</span>
        </div>
        <div class="font-mono-data font-bold price-up" style="font-size:30px;" id="ce-price">\u20b9\u2014</div>
        <div class="mt-1 text-xs price-up" id="ce-dir">\u2191 BUY signal</div>
      </div>
      <!-- PE Card -->
      <div id="pe-card" style="background:#0d0d0d;padding:20px;border-left:3px solid #FF3B3B;">
        <div class="flex items-center justify-between mb-2">
          <span id="pe-label" style="font-size:10px;font-weight:700;color:#FF3B3B;text-transform:uppercase;">\u2014 PE</span>
          <span style="font-size:9px;padding:2px 8px;border-radius:4px;background:rgba(255,59,59,0.1);color:#FF3B3B;border:1px solid rgba(255,59,59,0.2);">PUT</span>
        </div>
        <div class="font-mono-data font-bold price-down" style="font-size:30px;" id="pe-price">\u20b9\u2014</div>
        <div class="mt-1 text-xs price-down" id="pe-dir">\u2193 SELL signal</div>
      </div>
    </div>
  </div>
</section>

<!-- ===== SIGNAL BOX ===== -->
<section class="px-4 pb-4">
  <div id="signal-box" class="signal-block" style="border-radius:12px;padding:18px;">
    <div class="flex items-center gap-3">
      <span id="signal-icon" style="font-size:20px;">\u23f3</span>
      <div>
        <div id="signal-text" class="font-bold text-white" style="font-size:15px;">SYSTEM INITIALIZING...</div>
        <div id="trade-details" style="display:none;">
          <div class="flex gap-6 mt-2 font-mono-data text-sm">
            <div>Entry: <span id="t-entry" class="text-white font-bold">\u2014</span></div>
            <div>Target: <span id="t-target" class="price-up font-bold">\u2014</span></div>
            <div>SL: <span id="t-sl" class="price-down font-bold">\u2014</span></div>
          </div>
          <div class="mt-1 text-xs" style="color:#888;">
            Direction: <span id="t-dir" class="font-bold">\u2014</span>
            &nbsp;|&nbsp; Strike: <span id="t-strike" class="font-bold text-goat-cyan">\u2014</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ===== STRATEGY ENGINE (4 Tabs) ===== -->
<section class="px-4 pb-4">
  <div class="flex flex-wrap gap-2 mb-4">
    <button class="tab-btn active" onclick="switchStrategy('SCALP',this)">SCALP</button>
    <button class="tab-btn" onclick="switchStrategy('INTRADAY',this)">INTRADAY SCALP</button>
    <button class="tab-btn" onclick="switchStrategy('SWING',this)">SWING</button>
    <button class="tab-btn" onclick="switchStrategy('LONG',this)">LONG TERM</button>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
    <!-- Left: Active Trade Card -->
    <div class="glass glow-green" style="padding:24px;">
      <div class="flex items-center justify-between mb-5">
        <div class="flex items-center gap-2">
          <span class="pulse-dot" id="trade-dot" style="width:10px;height:10px;border-radius:50%;background:#00FF41;display:inline-block;"></span>
          <span id="trade-status-label" style="font-size:10px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:#00FF41;">MONITORING</span>
        </div>
        <span id="dir-badge" style="font-size:9px;padding:3px 10px;border-radius:4px;background:rgba(0,255,65,0.1);color:#00FF41;border:1px solid rgba(0,255,65,0.2);font-weight:700;">LONG</span>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px;">
        <div>
          <div style="font-size:9px;color:#555;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px;">Entry Price</div>
          <div class="font-mono-data text-xl font-bold text-white" id="card-entry">\u20b9\u2014</div>
        </div>
        <div>
          <div style="font-size:9px;color:#555;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px;">Current Price</div>
          <div class="font-mono-data text-xl font-bold text-goat-cyan" id="card-current">\u20b9\u2014</div>
        </div>
      </div>
      <div style="border-top:1px solid rgba(255,255,255,0.08);padding-top:16px;margin-bottom:20px;">
        <div style="font-size:9px;color:#555;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px;">Virtual P&L per lot</div>
        <div class="font-mono-data font-bold" style="font-size:36px;" id="card-pnl">\u20b90</div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px;">
        <div class="flex items-center gap-2">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#00FF41" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
          <div>
            <div style="font-size:9px;color:#555;text-transform:uppercase;">Target</div>
            <div class="font-mono-data text-sm font-bold price-up" id="card-target">\u20b9\u2014</div>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#FF3B3B" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
          <div>
            <div style="font-size:9px;color:#555;text-transform:uppercase;">Stop Loss</div>
            <div class="font-mono-data text-sm font-bold price-down" id="card-sl">\u20b9\u2014</div>
          </div>
        </div>
      </div>
      <div style="border-top:1px solid rgba(255,255,255,0.08);padding-top:12px;display:flex;align-items:center;gap:8px;">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#FFD700" stroke-width="2"><rect x="1" y="6" width="22" height="13" rx="2" ry="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg>
        <span style="font-size:9px;color:#555;text-transform:uppercase;letter-spacing:0.08em;">Virtual Capital</span>
        <span class="font-mono-data text-sm font-bold text-goat-amber">\u20b925,000</span>
      </div>
    </div>

    <!-- Right: GOAT BRAIN AI Reasoning -->
    <div class="glass" style="padding:24px;border:1px solid rgba(0,240,255,0.15);">
      <div class="flex items-center gap-2 mb-5 pb-4" style="border-bottom:1px solid rgba(255,255,255,0.08);">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#00F0FF" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
        <span style="font-size:10px;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:#00F0FF;">&#129302; GOAT BRAIN \u2014 Kyun Liya?</span>
      </div>
      <div id="ai-reasons">
        <div style="color:#444;font-size:13px;text-align:center;padding:40px 0;">Waiting for signal...</div>
      </div>
      <div style="margin-top:24px;padding-top:16px;border-top:1px solid rgba(255,255,255,0.08);">
        <div class="flex items-center justify-between mb-2">
          <span style="font-size:9px;color:#555;text-transform:uppercase;letter-spacing:0.1em;">Signal Strength</span>
          <span class="font-mono-data text-sm font-bold text-goat-green" id="confidence">0%</span>
        </div>
        <div style="height:5px;background:rgba(255,255,255,0.05);border-radius:99px;overflow:hidden;">
          <div id="confidence-bar" style="height:100%;width:0%;background:linear-gradient(90deg,rgba(0,255,65,0.5),#00FF41);border-radius:99px;transition:width 1s ease;"></div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ===== 5-POINT CHECKLIST ===== -->
<section class="px-4 pb-4">
  <div class="glass" style="padding:20px;">
    <div class="flex items-center justify-between mb-4">
      <span style="font-size:11px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:0.1em;">5-Point Signal Checklist</span>
      <span class="font-mono-data font-bold text-sm" id="chk-score" style="color:#FFD700;">0/5</span>
    </div>
    <div id="checklist" style="display:flex;flex-direction:column;gap:6px;"></div>
  </div>
</section>

<!-- ===== NEON CHART ===== -->
<section class="px-4 pb-4">
  <div class="glass" style="overflow:hidden;">
    <div class="flex items-center justify-between px-4 py-3" style="border-bottom:1px solid rgba(255,255,255,0.06);">
      <span style="font-size:9px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:0.12em;">Live Chart \u2014 NIFTY 50 (5-Min)</span>
      <div class="flex items-center gap-1">
        <span class="pulse-dot" style="width:6px;height:6px;border-radius:50%;background:#00FF41;display:inline-block;"></span>
        <span style="font-size:9px;color:#00FF41;font-weight:700;">LIVE</span>
      </div>
    </div>
    <canvas id="neonChart" style="width:100%;height:220px;display:block;background:rgba(5,5,5,0.9);"></canvas>
  </div>
</section>

<!-- ===== PERFORMANCE + JOURNAL ===== -->
<section class="px-4 pb-4">
  <div class="glass" style="padding:20px;">
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px;">
      <div class="metric-card">
        <div style="font-size:9px;color:#555;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px;">Total Trades</div>
        <div class="font-mono-data text-xl font-bold text-white" id="stat-total">0</div>
      </div>
      <div class="metric-card" style="background:rgba(0,255,65,0.04);border-color:rgba(0,255,65,0.15);">
        <div style="font-size:9px;color:#555;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px;">Win Rate</div>
        <div class="font-mono-data text-xl font-bold price-up" id="stat-wr">0%</div>
      </div>
      <div class="metric-card" style="background:rgba(0,255,65,0.04);border-color:rgba(0,255,65,0.15);">
        <div style="font-size:9px;color:#555;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px;">Virtual P&L</div>
        <div class="font-mono-data text-xl font-bold" id="stat-pnl">+\u20b90</div>
      </div>
      <div class="metric-card" style="background:rgba(255,215,0,0.04);border-color:rgba(255,215,0,0.15);">
        <div style="font-size:9px;color:#555;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px;">W / L</div>
        <div class="font-mono-data text-xl font-bold"><span class="price-up" id="stat-w">0</span>/<span class="price-down" id="stat-l">0</span></div>
      </div>
    </div>
    <!-- Journal Tabs -->
    <div class="flex gap-2 mb-4 flex-wrap">
      <button class="tab-btn active" onclick="switchTab('journal',this)">&#128203; Journal</button>
      <button class="tab-btn" onclick="switchTab('exit',this)">&#128682; Exit Trade</button>
      <button class="tab-btn" onclick="switchTab('intro',this)">&#129495; Introspection</button>
    </div>
    <div id="tab-journal" class="tab-content active">
      <div id="open-trade-card"></div>
      <div id="closed-trades"></div>
    </div>
    <div id="tab-exit" class="tab-content">
      <div id="exit-form-wrap"></div>
    </div>
    <div id="tab-intro" class="tab-content">
      <div style="display:flex;flex-direction:column;gap:12px;">
        <div>
          <label style="font-size:11px;color:#888;">Rules follow kiye? (1-5): <span id="rf-v" style="color:#00FF41;">3</span></label>
          <input type="range" min="1" max="5" value="3" id="rf" class="w-full mt-1" oninput="document.getElementById('rf-v').textContent=this.value">
        </div>
        <div>
          <label style="font-size:11px;color:#888;">SL skip kiya?</label>
          <select id="sl-skip" style="width:100%;background:#111;border:1px solid #222;border-radius:6px;padding:8px;color:#fff;margin-top:4px;">
            <option>No</option><option>Yes</option>
          </select>
        </div>
        <div>
          <label style="font-size:11px;color:#888;">Revenge trade?</label>
          <select id="revenge" style="width:100%;background:#111;border:1px solid #222;border-radius:6px;padding:8px;color:#fff;margin-top:4px;">
            <option>No</option><option>Yes</option>
          </select>
        </div>
        <div>
          <label style="font-size:11px;color:#888;">Discipline (1-5): <span id="dis-v" style="color:#00FF41;">3</span></label>
          <input type="range" min="1" max="5" value="3" id="dis" class="w-full mt-1" oninput="document.getElementById('dis-v').textContent=this.value">
        </div>
        <div>
          <label style="font-size:11px;color:#888;">Kal ke liye rule:</label>
          <textarea id="tmr" rows="2" style="width:100%;background:#111;border:1px solid #222;border-radius:6px;padding:8px;color:#fff;margin-top:4px;resize:none;" placeholder="Kal kya better karunga..."></textarea>
        </div>
        <button onclick="saveIntro()" style="background:#1e40af;color:#fff;border:none;border-radius:8px;padding:12px;font-weight:700;cursor:pointer;font-size:13px;">Save Introspection</button>
        <div id="dq-result" style="display:none;text-align:center;font-size:14px;font-weight:700;color:#00FF41;padding:8px;"></div>
      </div>
    </div>
  </div>
</section>

<!-- Footer -->
<div style="text-align:center;font-size:10px;color:#333;padding:20px;">
  &#128016; GOAT PRO \u2014 Virtual Paper Trading System | Not Financial Advice | Paper Trading Only
</div>

</div><!-- end wrapper -->

<script>
// ===== Global State =====
let liveData = {};
let closedTrades = [];
let openTrade = null;

// ===== Clock (IST) =====
function updateClock() {
  const now = new Date();
  const ist = now.toLocaleTimeString('en-IN', {
    timeZone: 'Asia/Kolkata', hour12: false,
    hour: '2-digit', minute: '2-digit', second: '2-digit'
  });
  document.getElementById('clock').textContent = ist;
}
setInterval(updateClock, 1000);
updateClock();

// ===== Neon Candlestick Chart =====
const canvas = document.getElementById('neonChart');
const ctx2d = canvas.getContext('2d');
let chartCandles = [];
let chartCurrent = {open:24150, high:24150, low:24150, close:24150};

function resizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx2d.scale(dpr, dpr);
  drawChart();
}

function drawChart() {
  const W = canvas.getBoundingClientRect().width;
  const H = canvas.getBoundingClientRect().height;
  ctx2d.clearRect(0, 0, canvas.width, canvas.height);

  const allCandles = [...chartCandles, chartCurrent];
  if (allCandles.length < 2) return;

  const prices = allCandles.flatMap(c => [c.high, c.low]);
  const maxP = Math.max(...prices) + 20;
  const minP = Math.min(...prices) - 20;
  const priceRange = maxP - minP;
  const toY = p => H - 20 - ((p - minP) / priceRange) * (H - 40);

  // Grid
  ctx2d.strokeStyle = 'rgba(0,255,65,0.07)';
  ctx2d.lineWidth = 1;
  ctx2d.setLineDash([2, 4]);
  for (let i = 0; i <= 6; i++) {
    const y = 20 + (i / 6) * (H - 40);
    ctx2d.beginPath(); ctx2d.moveTo(0, y); ctx2d.lineTo(W, y); ctx2d.stroke();
    const price = maxP - (i / 6) * priceRange;
    ctx2d.fillStyle = 'rgba(150,150,150,0.4)';
    ctx2d.font = '8px "Space Mono",monospace';
    ctx2d.textAlign = 'right';
    ctx2d.fillText(price.toFixed(0), W - 36, y - 2);
  }
  ctx2d.setLineDash([]);
  ctx2d.textAlign = 'start';

  // Candles
  const cw = 8, gap = 3;
  const startX = W - 30 - allCandles.length * (cw + gap);
  allCandles.forEach((c, i) => {
    const x = startX + i * (cw + gap);
    if (x + cw < 0) return;
    const bull = c.close >= c.open;
    const col = bull ? '#00FF41' : '#FF3B3B';
    ctx2d.strokeStyle = col; ctx2d.fillStyle = col; ctx2d.lineWidth = 1;
    ctx2d.beginPath();
    ctx2d.moveTo(x + cw / 2, toY(c.high));
    ctx2d.lineTo(x + cw / 2, toY(c.low));
    ctx2d.stroke();
    const top = toY(Math.max(c.open, c.close));
    const bot = toY(Math.min(c.open, c.close));
    ctx2d.fillRect(x, top, cw, Math.max(1, bot - top));
  });
}

window.addEventListener('resize', resizeCanvas);
setTimeout(resizeCanvas, 100);

// ===== Fetch Live Data (every 5 seconds) =====
async function fetchData() {
  try {
    const r = await fetch('/api/data');
    liveData = await r.json();
    if (liveData.error) return;

    // Update chart
    if (liveData.candles && liveData.candles.length > 0) {
      chartCandles = liveData.candles;
    }
    if (liveData.current_candle && liveData.current_candle.close > 0) {
      chartCurrent = liveData.current_candle;
    }
    drawChart();

    updateUI(liveData);
  } catch (e) { console.error('fetchData error:', e); }
}

async function fetchTrades() {
  try {
    const r = await fetch('/api/trades');
    const d = await r.json();
    closedTrades = d.closed || [];
    openTrade = d.open || null;
    renderJournal();
    renderExitForm();
    updateStats(d.stats || {});
  } catch (e) {}
}

// ===== Update UI =====
function updateUI(d) {
  // Ticker
  setText('ticker-nifty', d.spot ? d.spot.toLocaleString('en-IN') : '\u2014');
  setText('ticker-nifty2', d.spot ? d.spot.toLocaleString('en-IN') : '\u2014');
  setText('ticker-atm', d.atm_strike || '\u2014');
  setText('ticker-atm2', d.atm_strike || '\u2014');
  setText('ticker-vix', d.vix || '\u2014');
  setText('ticker-vix2', d.vix || '\u2014');
  setText('ticker-exp', d.expiry || '\u2014');
  setText('ticker-otype', d.option_type || 'CE');

  const pnlEl = document.getElementById('ticker-pnl');
  const pnlEl2 = document.getElementById('ticker-pnl2');
  const pnlStr = (d.pnl >= 0 ? '+' : '') + d.pnl + ' pts';
  if (pnlEl)  { pnlEl.textContent = pnlStr; pnlEl.style.color = d.pnl >= 0 ? '#00FF41' : '#FF3B3B'; }
  if (pnlEl2) { pnlEl2.textContent = pnlStr; pnlEl2.style.color = d.pnl >= 0 ? '#00FF41' : '#FF3B3B'; }

  setText('ticker-wr', (d.win_rate || 0) + '%');
  setText('ticker-wr2', (d.win_rate || 0) + '%');

  // Market badge
  const badge = document.getElementById('mkt-badge');
  const mtext = document.getElementById('mkt-text');
  if (d.market_status === 'OPEN') {
    badge.style.cssText = 'display:flex;align-items:center;gap:6px;padding:4px 10px;border-radius:6px;font-size:11px;font-weight:700;background:rgba(0,255,65,0.1);color:#00FF41;border:1px solid rgba(0,255,65,0.3);';
    badge.querySelector('.pulse-dot').style.background = '#00FF41';
    mtext.textContent = 'Market Open';
  } else {
    badge.style.cssText = 'display:flex;align-items:center;gap:6px;padding:4px 10px;border-radius:6px;font-size:11px;font-weight:700;background:rgba(255,59,59,0.1);color:#FF3B3B;border:1px solid rgba(255,59,59,0.3);';
    badge.querySelector('.pulse-dot').style.background = '#FF3B3B';
    mtext.textContent = d.market_status === 'PRE_OPEN' ? 'Pre-Open' : 'Market Closed';
  }

  setText('data-src', 'Data: ' + (d.data_source || '\u2014'));

  // Control Deck
  setText('spot', d.spot ? d.spot.toLocaleString('en-IN') : '\u2014');
  setText('vix', d.vix || '\u2014');
  setText('expiry', d.expiry || '\u2014');
  setText('atm', d.atm_strike ? d.atm_strike.toLocaleString('en-IN') : '\u2014');

  const velEl = document.getElementById('vel-display');
  if (velEl) {
    velEl.textContent = (d.velocity > 0 ? '+' : '') + d.velocity + ' pts/tick';
    velEl.style.color = d.velocity >= 0 ? '#00FF41' : '#FF3B3B';
  }

  const vixBadge = document.getElementById('vix-badge');
  if (vixBadge) {
    const safe = (d.vix || 15) < 18;
    vixBadge.innerHTML = `<span style="font-size:10px;padding:2px 8px;border-radius:4px;background:${safe ? 'rgba(0,255,65,0.1)' : 'rgba(255,59,59,0.1)'};color:${safe ? '#00FF41' : '#FF3B3B'};border:1px solid ${safe ? 'rgba(0,255,65,0.2)' : 'rgba(255,59,59,0.2)'}">${safe ? 'Safe' : 'Danger'}</span>`;
  }

  // CE/PE labels + live premium prices
  setText('ce-label', (d.atm_strike || '—') + ' CE');
  setText('pe-label', (d.atm_strike || '—') + ' PE');

  // Live CE/PE premium display
  const cePriceEl = document.getElementById('ce-price');
  const pePriceEl = document.getElementById('pe-price');
  if (cePriceEl) {
    if (d.ce_premium && d.ce_premium > 0) {
      cePriceEl.textContent = '₹' + d.ce_premium.toFixed(1);
      cePriceEl.style.color = '#00FF41';
    } else {
      cePriceEl.textContent = '₹—';
    }
  }
  if (pePriceEl) {
    if (d.pe_premium && d.pe_premium > 0) {
      pePriceEl.textContent = '₹' + d.pe_premium.toFixed(1);
      pePriceEl.style.color = '#FF3B3B';
    } else {
      pePriceEl.textContent = '₹—';
    }
  }

  // Live P&L in rupees (if trade active)
  const livePnlEl = document.getElementById('live-pnl-rs');
  if (livePnlEl && d.status === 'TRADE_ACTIVE') {
    const rs = d.live_pnl_rs || 0;
    livePnlEl.textContent = (rs >= 0 ? '+₹' : '-₹') + Math.abs(rs).toLocaleString('en-IN');
    livePnlEl.style.color = rs >= 0 ? '#00FF41' : '#FF3B3B';
  } else if (livePnlEl) {
    livePnlEl.textContent = '';
  }

  // Session P&L in rupees
  const sessionRsEl = document.getElementById('session-pnl-rs');
  if (sessionRsEl && d.session_pnl_rs !== undefined) {
    const srs = d.session_pnl_rs || 0;
    sessionRsEl.textContent = (srs >= 0 ? '+₹' : '-₹') + Math.abs(srs).toLocaleString('en-IN');
    sessionRsEl.style.color = srs >= 0 ? '#00FF41' : '#FF3B3B';
  }

  // Lot size display
  const lotEl = document.getElementById('lot-size-display');
  if (lotEl) lotEl.textContent = (d.lot_size || 65) + ' qty (1 lot)';

  // Active direction highlight
  const activeDir = d.direction || 'LONG';
  const ceEl = document.getElementById('ce-dir');
  const peEl = document.getElementById('pe-dir');
  if (ceEl) ceEl.textContent = activeDir === 'LONG' ? '↑ ACTIVE — BUY CE' : '↑ Inactive';
  if (peEl) peEl.textContent = activeDir === 'SHORT' ? '↓ ACTIVE — BUY PE' : '↓ Inactive';

  // Signal Box
  updateSignalBox(d);

  // Strategy Card
  updateStrategyCard(d);

  // Checklist
  updateChecklist(d.chk || [false, false, false, false, false], d.pass_count || 0);

  // Stats
  setText('stat-total', d.total || 0);
  setText('stat-wr', (d.win_rate || 0) + '%');
  const spnl = document.getElementById('stat-pnl');
  if (spnl) {
    spnl.textContent = (d.pnl >= 0 ? '+\u20b9' : '-\u20b9') + Math.abs(d.pnl || 0);
    spnl.style.color = (d.pnl || 0) >= 0 ? '#00FF41' : '#FF3B3B';
  }
  setText('stat-w', d.wins || 0);
  setText('stat-l', d.losses || 0);
}

function updateSignalBox(d) {
  const box  = document.getElementById('signal-box');
  const icon = document.getElementById('signal-icon');
  const txt  = document.getElementById('signal-text');
  const det  = document.getElementById('trade-details');

  if (d.status === 'TRADE_ACTIVE') {
    box.className = 'signal-trade'; box.style.borderRadius = '12px'; box.style.padding = '18px';
    icon.textContent = '\u1f680';
    txt.style.color = '#00FF41';
  } else if (d.status === 'SETUP_READY') {
    box.className = 'signal-setup'; box.style.borderRadius = '12px'; box.style.padding = '18px';
    icon.textContent = '\u1f4ca';
    txt.style.color = '#FFD700';
  } else {
    box.className = 'signal-block'; box.style.borderRadius = '12px'; box.style.padding = '18px';
    icon.textContent = '\u23f3';
    txt.style.color = '#888';
  }
  txt.textContent = d.signal || '\u2014';

  if (d.status === 'TRADE_ACTIVE' && d.entry) {
    det.style.display = 'block';
    setText('t-entry', '\u20b9' + d.entry);
    setText('t-target', '\u20b9' + d.target);
    setText('t-sl', '\u20b9' + d.sl);
    const dirEl = document.getElementById('t-dir');
    if (dirEl) {
      dirEl.textContent = d.direction || '\u2014';
      dirEl.style.color = d.direction === 'LONG' ? '#00FF41' : '#FF3B3B';
    }
    setText('t-strike', (d.atm_strike || '\u2014') + ' ' + (d.option_type || ''));
  } else {
    det.style.display = 'none';
  }
}

function updateStrategyCard(d) {
  const isActive = d.status === 'TRADE_ACTIVE';

  setText('trade-status-label', isActive ? 'TRADE ACTIVE' : 'MONITORING');
  const dot = document.getElementById('trade-dot');
  if (dot) dot.style.background = isActive ? '#00FF41' : '#FFD700';

  const dirBadge = document.getElementById('dir-badge');
  if (dirBadge) {
    const dir = d.direction || 'LONG';
    dirBadge.textContent = dir;
    dirBadge.style.color = dir === 'LONG' ? '#00FF41' : '#FF3B3B';
    dirBadge.style.background = dir === 'LONG' ? 'rgba(0,255,65,0.1)' : 'rgba(255,59,59,0.1)';
    dirBadge.style.border = dir === 'LONG' ? '1px solid rgba(0,255,65,0.2)' : '1px solid rgba(255,59,59,0.2)';
  }

  if (isActive && d.entry) {
    setText('card-entry', '\u20b9' + d.entry);
    setText('card-current', '\u20b9' + d.spot);
    setText('card-target', '\u20b9' + d.target);
    setText('card-sl', '\u20b9' + d.sl);
    const pnl = d.direction === 'LONG' ? d.spot - d.entry : d.entry - d.spot;
    const pEl = document.getElementById('card-pnl');
    if (pEl) {
      pEl.textContent = (pnl >= 0 ? '+\u20b9' : '-\u20b9') + Math.abs(pnl).toFixed(1);
      pEl.style.color = pnl >= 0 ? '#00FF41' : '#FF3B3B';
    }
  } else {
    setText('card-entry', '\u20b9\u2014');
    setText('card-current', '\u20b9' + d.spot);
    setText('card-target', '\u20b9\u2014');
    setText('card-sl', '\u20b9\u2014');
    setText('card-pnl', '\u20b90');
    const pEl = document.getElementById('card-pnl');
    if (pEl) pEl.style.color = '#888';
  }

  // AI Reasons (GOAT BRAIN)
  const reasons = d.brain_reasons || [];
  const aiDiv = document.getElementById('ai-reasons');
  const passCount = d.pass_count || 0;
  const confidence = Math.round((passCount / 5) * 100);
  const confEl = document.getElementById('confidence');
  const confBar = document.getElementById('confidence-bar');
  if (confEl) confEl.textContent = confidence + '%';
  if (confBar) confBar.style.width = confidence + '%';

  if (reasons.length > 0) {
    aiDiv.innerHTML = reasons.map((r, i) => `
      <div class="ai-line flex items-center gap-2" style="padding:6px 0;animation-delay:${i * 0.15}s">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#00FF41" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
        <span style="font-size:12px;color:#ccc;">${r}</span>
      </div>`).join('');
  } else {
    aiDiv.innerHTML = '<div style="color:#444;font-size:12px;text-align:center;padding:30px 0;">Conditions not met yet...</div>';
  }
}

function updateChecklist(chk, passCount) {
  const labels = [
    'NIFTY above round level',
    'Velocity positive (momentum)',
    'Distance from round number OK',
    'VIX safe (below 18)',
    'Within valid strike range'
  ];
  const div = document.getElementById('checklist');
  if (!div) return;
  div.innerHTML = labels.map((lbl, i) => `
    <div style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-radius:6px;font-size:12px;${chk[i] ? 'background:rgba(0,255,65,0.07);color:#86efac;border-left:3px solid #00FF41;' : 'background:rgba(255,59,59,0.05);color:#fca5a5;border-left:3px solid #FF3B3B;'}">
      <span>${chk[i] ? '&#9989;' : '&#10060;'}</span><span>${lbl}</span>
    </div>`).join('');
  const sc = document.getElementById('chk-score');
  if (sc) {
    sc.textContent = passCount + '/5';
    sc.style.color = passCount === 5 ? '#00FF41' : passCount >= 3 ? '#FFD700' : '#FF3B3B';
  }
}

function updateStats(s) {
  setText('stat-total', s.total || 0);
  setText('stat-wr', (s.win_rate || 0) + '%');
  const sp = document.getElementById('stat-pnl');
  if (sp) {
    sp.textContent = (s.total_pnl >= 0 ? '+\u20b9' : '-\u20b9') + Math.abs(s.total_pnl || 0);
    sp.style.color = (s.total_pnl || 0) >= 0 ? '#00FF41' : '#FF3B3B';
  }
  setText('stat-w', s.wins || 0);
  setText('stat-l', s.losses || 0);
}

// ===== Journal =====
function renderJournal() {
  const ot = document.getElementById('open-trade-card');
  if (openTrade) {
    ot.innerHTML = `<div style="background:rgba(0,255,65,0.06);border:1px solid rgba(0,255,65,0.2);border-radius:10px;padding:16px;margin-bottom:12px;">
      <div style="color:#00FF41;font-weight:700;font-size:12px;margin-bottom:10px;">&#128994; OPEN TRADE</div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;font-size:12px;">
        <div>Direction: <span style="font-weight:700;color:${openTrade.direction === 'LONG' ? '#00FF41' : '#FF3B3B'}">${openTrade.direction}</span></div>
        <div>Entry: <span style="font-weight:700;color:#fff">\u20b9${openTrade.entry_price}</span></div>
        <div>Time: <span style="color:#888">${openTrade.entry_time}</span></div>
        <div>Target: <span style="color:#00FF41;font-weight:700">\u20b9${openTrade.target}</span></div>
        <div>SL: <span style="color:#FF3B3B;font-weight:700">\u20b9${openTrade.sl}</span></div>
        <div>Strike: <span style="color:#00F0FF">${openTrade.atm_strike} ${openTrade.option_type}</span></div>
      </div>
    </div>`;
  } else { ot.innerHTML = ''; }

  const cd = document.getElementById('closed-trades');
  if (closedTrades.length === 0) {
    cd.innerHTML = '<div style="text-align:center;color:#444;padding:30px;font-size:13px;">Koi trade nahi hua abhi</div>';
    return;
  }
  cd.innerHTML = closedTrades.slice(0, 15).map(t => {
    const win = (t.pnl || 0) > 0;
    return `<div style="background:rgba(18,18,18,0.8);border:1px solid ${win ? 'rgba(0,255,65,0.15)' : 'rgba(255,59,59,0.15)'};border-radius:8px;padding:12px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;">
      <div>
        <span style="font-size:11px;font-weight:700;color:${t.direction === 'LONG' ? '#00FF41' : '#FF3B3B'};">${t.direction}</span>
        <span style="font-size:10px;color:#555;margin-left:8px;">${t.atm_strike || ''} ${t.option_type || ''}</span>
        <div style="font-size:10px;color:#444;margin-top:2px;">${t.entry_time || ''} \u2192 ${t.exit_time || ''} | ${t.exit_reason || ''}</div>
      </div>
      <div style="font-size:16px;font-weight:700;color:${win ? '#00FF41' : '#FF3B3B'};">${win ? '+' : ''}${t.pnl} pts</div>
    </div>`;
  }).join('');
}

function renderExitForm() {
  const wrap = document.getElementById('exit-form-wrap');
  if (!openTrade) {
    wrap.innerHTML = '<div style="text-align:center;color:#444;padding:40px;font-size:13px;">Koi open trade nahi hai</div>';
    return;
  }
  wrap.innerHTML = `<div style="display:flex;flex-direction:column;gap:12px;">
    <div style="color:#FFD700;font-weight:700;margin-bottom:4px;">Manual Exit \u2014 Trade #${openTrade.id}</div>
    <div><label style="font-size:11px;color:#888;">Exit Price</label>
      <input type="number" id="ep" value="${liveData.spot || 0}" style="width:100%;background:#111;border:1px solid #222;border-radius:6px;padding:8px;color:#fff;margin-top:4px;"></div>
    <div><label style="font-size:11px;color:#888;">Exit Reason</label>
      <select id="er" style="width:100%;background:#111;border:1px solid #222;border-radius:6px;padding:8px;color:#fff;margin-top:4px;">
        <option>Target Hit</option><option>SL Hit</option><option>Manual Exit</option><option>Time Exit</option><option>Trailing SL</option>
      </select></div>
    <div><label style="font-size:11px;color:#888;">Emotion</label>
      <select id="em" style="width:100%;background:#111;border:1px solid #222;border-radius:6px;padding:8px;color:#fff;margin-top:4px;">
        <option>Calm</option><option>Greedy</option><option>Fearful</option><option>Revenge</option><option>Overconfident</option><option>Patient</option>
      </select></div>
    <div><label style="font-size:11px;color:#888;">Post Note</label>
      <textarea id="en" rows="2" style="width:100%;background:#111;border:1px solid #222;border-radius:6px;padding:8px;color:#fff;margin-top:4px;resize:none;" placeholder="Is trade se kya seekha?"></textarea></div>
    <button onclick="doExit(${openTrade.id},'${openTrade.direction}',${openTrade.entry_price})" style="background:#7f1d1d;border:1px solid rgba(255,59,59,0.4);color:#fff;border-radius:8px;padding:12px;font-weight:700;cursor:pointer;font-size:13px;">&#128682; Exit Trade</button>
  </div>`;
}

// ===== Actions =====
async function doExit(tid, dir, entry) {
  const ep = parseFloat(document.getElementById('ep').value);
  const pnl = dir === 'LONG' ? ep - entry : entry - ep;
  await fetch('/paper/exit', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      trade_id: tid, exit_price: ep, direction: dir, entry_price: entry,
      exit_reason: document.getElementById('er').value,
      post_note: document.getElementById('en').value,
      emotion: document.getElementById('em').value
    })
  });
  await fetchTrades();
  switchTab('journal', document.querySelector('.tab-btn.active'));
}

async function saveIntro() {
  const r = await fetch('/paper/intro', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      rule_followed: document.getElementById('rf').value,
      sl_skip: document.getElementById('sl-skip').value,
      revenge: document.getElementById('revenge').value,
      discipline: document.getElementById('dis').value,
      tomorrow_rule: document.getElementById('tmr').value
    })
  });
  const d = await r.json();
  const el = document.getElementById('dq-result');
  el.style.display = 'block';
  el.textContent = 'DQ Score: ' + d.dq_score + '/100 \u2014 ' + d.breakdown;
}

// ===== Tab Switchers =====
function switchTab(name, btn) {
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  const el = document.getElementById('tab-' + name);
  if (el) el.classList.add('active');
  if (btn) {
    document.querySelectorAll('[onclick^="switchTab"]').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  }
}

function switchStrategy(name, btn) {
  document.querySelectorAll('[onclick^="switchStrategy"]').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
}

function setSegment(name, btn) {
  document.querySelectorAll('.seg-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
}

function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

// ===== Startup =====
fetchData();
fetchTrades();
setInterval(fetchData, 5000);
setInterval(fetchTrades, 10000);
</script>
</body>
</html>"""


# ═════════════════════════════════════════════════════════
# FLASK ROUTES
# ═════════════════════════════════════════════════════════

@app.route("/")
def index():
    """Serve the main dashboard UI"""
    return render_template_string(TEMPLATE)


@app.route("/api/data")
def api_data():
    """
    Main data endpoint. Fetches market data, runs strategy engine,
    returns payload for the frontend. Called every 5 seconds by JS.
    """
    data = run_pipeline()
    if "error" in data:
        return jsonify({
            "error": data["error"], "spot": 0, "vix": 15, "velocity": 0,
            "status": "BLOCKED", "signal": data["error"], "brain_reasons": [],
            "entry": 0, "target": 0, "sl": 0, "chk": [False] * 5, "pass_count": 0,
            "total": 0, "wins": 0, "losses": 0, "pnl": 0, "win_rate": 0,
            "market_status": "CLOSED", "market_msg": data["error"],
            "atm_strike": 0, "option_type": "CE", "direction": "LONG",
            "data_source": "\u2014", "expiry": "\u2014",
            "candles": [], "current_candle": {}
        })
    return jsonify(data)


@app.route("/api/trades")
def api_trades():
    """Return open trade + closed trades + computed stats"""
    closed = db_closed_trades()
    return jsonify({
        "open": db_open_trade(),
        "closed": closed,
        "stats": calc_stats(closed)
    })


@app.route("/ping")
def ping():
    """
    UptimeRobot health check endpoint.
    Returns basic status without running full pipeline.
    """
    return jsonify({
        "status": "alive",
        "time": time.strftime("%H:%M:%S"),
        "service": "GOAT PRO"
    })


@app.route("/paper/exit", methods=["POST"])
def paper_exit():
    """Manual trade exit endpoint"""
    d = request.get_json()
    tid = d.get("trade_id")
    ep = float(d.get("exit_price", 0))
    dir_ = d.get("direction", "LONG")
    enp = float(d.get("entry_price", 0))
    pnl = round((ep - enp) if dir_ == "LONG" else (enp - ep), 2)
    db_close_trade(
        tid, ep, d.get("exit_reason", ""), d.get("post_note", ""),
        d.get("emotion", ""), pnl
    )
    if pnl > 0:
        ENGINE["trades_won"] += 1
    else:
        ENGINE["trades_lost"] += 1
    ENGINE["trades_total"] += 1
    ENGINE["session_pnl"] = round(ENGINE["session_pnl"] + pnl, 1)
    return jsonify({"status": "ok", "pnl": pnl})


@app.route("/paper/intro", methods=["POST"])
def paper_intro():
    """Save introspection entry + calculate decision quality score"""
    d = request.get_json()
    today = time.strftime("%d-%m-%Y")
    db_add_intro({**d, "date": today})
    dq_score, breakdown = calc_dq_score(
        d.get("rule_followed", 3),
        d.get("sl_skip", "No"),
        d.get("revenge", "No"),
        d.get("discipline", 3)
    )
    db_add_dq({"date": today, "dq_score": dq_score, "breakdown": breakdown})
    return jsonify({"status": "ok", "dq_score": dq_score, "breakdown": breakdown})


@app.route("/paper/clear", methods=["POST"])
def paper_clear():
    """Clear all paper trades (use with caution)"""
    db_clear_trades()
    return jsonify({"status": "ok"})


# ═════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
