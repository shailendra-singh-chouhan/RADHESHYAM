# -*- coding: utf-8 -*-
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
    "NIFTY":     {"token": "99926000", "exchange": "NSE"},
    "BANKNIFTY": {"token": "99926009", "exchange": "NSE"},
    "FINNIFTY":  {"token": "99926037", "exchange": "NSE"},
    "SENSEX":    {"token": "99919000", "exchange": "BSE"},
    "VIX":       {"token": "99926017", "exchange": "NSE"},
}

# ATM intervals per index
ATM_INTERVALS = {
    "NIFTY":     50,
    "BANKNIFTY": 100,
    "FINNIFTY":  50,
    "SENSEX":    100,
}

# Max trades per day per index
MAX_TRADES_PER_DAY = 5
TRADE_COOLDOWN_SECS = 300  # 5 min between trades

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


def get_both_premiums(index, atm_strike, expiry_date):
    """
    Fetch CE and PE live premium from Angel One NFO.
    Tries multiple symbol formats automatically.
    Returns (ce_ltp, pe_ltp) as floats.
    """
    expiry_str = expiry_date  # keep for compatibility
    try:
        now = time.time()
        if now - OPTION_CACHE["last_fetch"] < OPTION_CACHE["ttl"]:
            return OPTION_CACHE["ce_ltp"], OPTION_CACHE["pe_ltp"]

        obj, err = get_session()
        if err or not obj:
            return 0.0, 0.0

        ce_ltp, pe_ltp = 0.0, 0.0

        # Get all possible symbol formats
        ce_formats, pe_formats = get_angel_option_symbols(index, atm_strike, expiry_str)

        # Fetch CE
        for sym in ce_formats:
            try:
                r = obj.searchScrip("NFO", sym)
                if r and r.get("data") and len(r["data"]) > 0:
                    tok = r["data"][0]["symboltoken"]
                    actual_sym = r["data"][0].get("tradingsymbol", sym)
                    lr = obj.ltpData("NFO", actual_sym, tok)
                    if lr and lr.get("data"):
                        ce_ltp = float(lr["data"]["ltp"])
                        break
            except Exception:
                continue

        # Fetch PE
        for sym in pe_formats:
            try:
                r = obj.searchScrip("NFO", sym)
                if r and r.get("data") and len(r["data"]) > 0:
                    tok = r["data"][0]["symboltoken"]
                    actual_sym = r["data"][0].get("tradingsymbol", sym)
                    lr = obj.ltpData("NFO", actual_sym, tok)
                    if lr and lr.get("data"):
                        pe_ltp = float(lr["data"]["ltp"])
                        break
            except Exception:
                continue

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
    Returns expiry date object for Nifty weekly (Thursday)
    """
    today = datetime.date.today()
    days_until_thursday = (3 - today.weekday()) % 7
    if days_until_thursday == 0 and datetime.datetime.now().time() > datetime.time(15, 30):
        days_until_thursday = 7
    expiry_date = today + datetime.timedelta(days=days_until_thursday + (weeks_ahead * 7))
    return expiry_date

def get_angel_option_symbols(index, strike, expiry_date):
    """
    Generate all possible Angel One NFO symbol formats for options.
    Angel One weekly format: NIFTY2461824100CE (YY+Month+DD)
    """
    yy = str(expiry_date.year)[2:]  # 26
    dd = f"{expiry_date.day:02d}"   # 18
    mm = f"{expiry_date.month}"     # 6
    mm2 = f"{expiry_date.month:02d}" # 06
    months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
    mon = months[expiry_date.month - 1]  # JUN
    
    formats = [
        # Angel One weekly short format (most common)
        f"{index}{yy}{mm}{dd}{strike}",      # NIFTY26618 24100
        f"{index}{yy}{mm2}{dd}{strike}",     # NIFTY2606 1824100
        f"{index}{yy}{mon}{dd}{strike}",     # NIFTY26JUN1824100
        # Full date formats
        f"{index}{dd}{mon}{expiry_date.year}{strike}",  # NIFTY18JUN202624100
        f"{index}{dd}{mon}{yy}{strike}",     # NIFTY18JUN2624100
    ]
    
    ce_symbols = [f + "CE" for f in formats]
    pe_symbols = [f + "PE" for f in formats]
    return ce_symbols, pe_symbols




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
    # Multi-trade support
    "trades_today": 0,
    "last_trade_time": 0,
    "last_trade_date": "",
    # Market condition
    "market_condition": "UNKNOWN",
    "strategy_mode": "TREND",
    # Opening analysis
    "opening_gap": 0.0,
    "prev_close": 0.0,
    "opening_done": False,
    # Technical indicators
    "ema9": 0.0,
    "ema21": 0.0,
    "rsi": 50.0,
    "risk_per_trade_rs": 0.0,
}


# ═══════════════════════════════════════════════════════
# TECHNICAL INDICATORS
# ═══════════════════════════════════════════════════════

# Price history for EMA/RSI
_price_history = []

def update_price_history(price):
    global _price_history
    _price_history.append(price)
    if len(_price_history) > 50:
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
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)

def detect_market_condition(prices, vix, vel):
    """
    Detect: TRENDING_UP / TRENDING_DOWN / SIDEWAYS / VOLATILE
    """
    if vix > 20:
        return "VOLATILE"
    if len(prices) < 10:
        return "UNKNOWN"
    ema9 = calc_ema(prices, 9)
    ema21 = calc_ema(prices, 21) if len(prices) >= 21 else ema9
    price = prices[-1]
    if ema9 > ema21 and price > ema9 and vel > 0:
        return "TRENDING_UP"
    elif ema9 < ema21 and price < ema9 and vel < 0:
        return "TRENDING_DOWN"
    else:
        return "SIDEWAYS"

def select_strategy(condition, vix):
    """
    Returns: strategy name + checklist adjustments
    TREND: momentum follow
    RANGE: buy dips sell tops
    WAIT: no trade
    """
    if condition == "VOLATILE" or vix > 20:
        return "WAIT", "VIX high — no trade"
    elif condition == "TRENDING_UP":
        return "TREND", "Trend UP — Buy CE on dips"
    elif condition == "TRENDING_DOWN":
        return "TREND", "Trend DOWN — Buy PE on rallies"
    elif condition == "SIDEWAYS":
        return "RANGE", "Sideways — Range trading mode"
    return "WAIT", "Market unclear — waiting"

def detect_opening_gap(spot, prev_close):
    """Gap up/down analysis at market open"""
    if prev_close <= 0:
        return 0.0, "Normal"
    gap_pts = round(spot - prev_close, 2)
    gap_pct = round((gap_pts / prev_close) * 100, 2)
    if gap_pct > 0.5:
        label = f"GAP UP +{gap_pct}%"
    elif gap_pct < -0.5:
        label = f"GAP DOWN {gap_pct}%"
    else:
        label = f"Flat open {gap_pct}%"
    return gap_pts, label

def get_banknifty_spot():
    """Fetch BankNifty spot from Angel One"""
    try:
        obj, err = get_session()
        if err or not obj:
            return None
        resp = obj.ltpData("NSE", "Nifty Bank", TOKENS["BANKNIFTY"]["token"])
        if resp and resp.get("data"):
            return float(resp["data"]["ltp"])
    except Exception:
        pass
    return None

def get_sensex_spot():
    """Fetch Sensex from Angel One BSE"""
    try:
        obj, err = get_session()
        if err or not obj:
            return None
        resp = obj.ltpData("BSE", "SENSEX", TOKENS["SENSEX"]["token"])
        if resp and resp.get("data"):
            return float(resp["data"]["ltp"])
    except Exception:
        pass
    return None


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
    update_price_history(spot)

    # ── BankNifty mini fetch ──
    try:
        bn_spot = get_banknifty_spot()
        if bn_spot:
            ENGINE["bn_spot"] = bn_spot
            bn_history = ENGINE.get("_bn_history", [])
            bn_history.append(bn_spot)
            if len(bn_history) > 30: bn_history.pop(0)
            ENGINE["_bn_history"] = bn_history
            ENGINE["bn_rsi"] = calc_rsi(bn_history)
            ENGINE["bn_ema9"] = calc_ema(bn_history, 9)
            ENGINE["bn_atm"] = get_atm_strike(bn_spot, 100)
            ENGINE["bn_condition"] = detect_market_condition(bn_history, vix, bn_spot - (bn_history[-2] if len(bn_history)>1 else bn_spot))
    except Exception:
        pass

    # Velocity calculation
    if ENGINE["last_spot"] > 0:
        ENGINE["velocity"] = round(spot - ENGINE["last_spot"], 2)
    ENGINE["last_spot"] = spot
    vel = ENGINE["velocity"]

    # ── EMA + RSI from price history ──
    if len(_price_history) >= 9:
        ENGINE["ema9"] = calc_ema(_price_history, 9)
    if len(_price_history) >= 21:
        ENGINE["ema21"] = calc_ema(_price_history, 21)
    if len(_price_history) >= 15:
        ENGINE["rsi"] = calc_rsi(_price_history)

    # ── Market condition + strategy ──
    condition = detect_market_condition(_price_history, vix, vel)
    strategy, strategy_msg = select_strategy(condition, vix)
    ENGINE["market_condition"] = condition
    ENGINE["strategy_mode"] = strategy

    # ── Opening gap analysis ──
    if not ENGINE.get("opening_done") and mstatus == "OPEN":
        try:
            import yfinance as yf
            ticker = yf.Ticker("^NSEI")
            hist = ticker.history(period="2d")
            if len(hist) >= 2:
                ENGINE["prev_close"] = float(hist['Close'].iloc[-2])
                ENGINE["today_open"] = float(hist['Open'].iloc[-1])
                ENGINE["opening_gap"] = round(spot - ENGINE["prev_close"], 2)
                ENGINE["opening_done"] = True
        except Exception:
            pass

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
        expiry_date = get_expiry_str_for_angel(0)
        ce_ltp, pe_ltp = get_both_premiums(active_index, atm, expiry_date)
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

    ema9 = ENGINE.get("ema9", 0.0)
    ema21 = ENGINE.get("ema21", 0.0)
    rsi = ENGINE.get("rsi", 50.0)

    chk = [
        spot > base100,                          # 0: NIFTY above round level
        vel > 0,                                  # 1: Velocity positive
        dist_round > 10,                          # 2: Away from round number
        vix < 18.0,                               # 3: VIX safe
        (spot - base100) < (0.008 * spot)        # 4: Within valid strike range
    ]
    # Extended checklist for strategy
    ema_ok = ema9 > 0 and ema21 > 0 and ema9 > ema21
    rsi_ok = 40 <= rsi <= 65
    all_pass = all(chk) and (strategy != "WAIT")
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
        # ── BankNifty mini data ──
        "bn_spot": round(ENGINE.get("bn_spot", 0.0), 2),
        "bn_rsi": round(ENGINE.get("bn_rsi", 50.0), 1),
        "bn_ema9": round(ENGINE.get("bn_ema9", 0.0), 2),
        "bn_atm": ENGINE.get("bn_atm", 0),
        "bn_condition": ENGINE.get("bn_condition", "UNKNOWN"),
        # ── Technical indicators ──
        "ema9": round(ENGINE.get("ema9", 0.0), 2),
        "ema21": round(ENGINE.get("ema21", 0.0), 2),
        "rsi": round(ENGINE.get("rsi", 50.0), 1),
        # ── Market condition + strategy ──
        "market_condition": ENGINE.get("market_condition", "UNKNOWN"),
        "strategy_mode": ENGINE.get("strategy_mode", "WAIT"),
        # ── Opening analysis ──
        "opening_gap": round(ENGINE.get("opening_gap", 0.0), 1),
        "prev_close": round(ENGINE.get("prev_close", 0.0), 2),
        "today_open": round(ENGINE.get("today_open", 0.0), 2),
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
<title>⚡ GOAT PRO — Multi Market Command Center</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=JetBrains+Mono:wght@300;400;700&family=Rajdhani:wght@400;600;700&display=swap');
:root{
  --bg:#f0f4ff;--panel:#fff;--panel2:#f7f9ff;--border:#dde4f5;
  --accent:#1a56db;--accent2:#0e3fa8;
  --green:#0a9e5c;--green2:#e6f9f1;
  --red:#e02d3c;--red2:#fdeef0;
  --blue:#1a56db;--blue2:#eef2ff;
  --gold:#b45309;--gold2:#fef3c7;
  --purple:#7c3aed;--purple2:#f5f3ff;
  --dim:#6b7280;--text:#1e2a3a;
  --shadow:0 2px 12px rgba(26,86,219,0.08);
  --shadow2:0 6px 28px rgba(26,86,219,0.18);
}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:'Rajdhani',sans-serif;min-height:100vh;}
body::before{content:'';position:fixed;inset:0;
  background:radial-gradient(ellipse 70% 40% at 10% 0%,rgba(26,86,219,0.07),transparent 70%),
             radial-gradient(ellipse 50% 50% at 90% 100%,rgba(26,86,219,0.05),transparent 70%);
  pointer-events:none;z-index:0;}
.wrap{max-width:1400px;margin:0 auto;padding:12px 14px;position:relative;z-index:1;}
.topbar{display:flex;align-items:center;justify-content:space-between;
  background:linear-gradient(135deg,#1a56db,#0e3fa8);
  border-radius:12px;padding:14px 22px;margin-bottom:12px;
  box-shadow:var(--shadow2);flex-wrap:wrap;gap:10px;}
.topbar h1{font-family:'Bebas Neue',sans-serif;font-size:clamp(20px,4vw,34px);
  letter-spacing:5px;color:#fff;}
.topbar small{font-family:'JetBrains Mono',monospace;font-size:9px;
  color:rgba(255,255,255,0.55);letter-spacing:2px;display:block;}
.tb-right{display:flex;gap:12px;align-items:center;flex-wrap:wrap;}
.tb-stat{text-align:center;}
.tb-val{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;color:#fff;}
.tb-label{font-size:9px;color:rgba(255,255,255,0.5);letter-spacing:1px;text-transform:uppercase;}
.tb-div{width:1px;height:26px;background:rgba(255,255,255,0.2);}
.live-pill{display:flex;align-items:center;gap:6px;
  background:rgba(255,255,255,0.15);border:1px solid rgba(255,255,255,0.3);
  border-radius:20px;padding:5px 14px;
  font-family:'JetBrains Mono',monospace;font-size:10px;color:#fff;}
.ldot{width:7px;height:7px;border-radius:50%;background:#4ade80;
  box-shadow:0 0 8px #4ade80;animation:blink 1s infinite;}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0.2}}
.legal-banner{background:linear-gradient(135deg,#fef3c7,#fde68a);
  border:1.5px solid #f59e0b;border-radius:8px;padding:10px 16px;
  margin-bottom:12px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.legal-banner span{font-size:12px;color:#92400e;line-height:1.5;flex:1;}
.legal-badge{background:#f59e0b;color:#fff;border-radius:4px;
  padding:3px 10px;font-size:10px;font-weight:700;white-space:nowrap;
  font-family:'JetBrains Mono',monospace;}
.sess-strip{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap;}
.sess{flex:1;min-width:100px;border:1.5px solid var(--border);border-radius:8px;
  padding:8px 10px;text-align:center;background:var(--panel);
  box-shadow:var(--shadow);position:relative;overflow:hidden;}
.sess.active{border-color:var(--accent);background:var(--blue2);}
.sess.active::after{content:'';position:absolute;bottom:0;left:0;right:0;height:3px;background:var(--accent);}
.sess-name{font-size:10px;color:var(--dim);letter-spacing:1px;font-weight:600;}
.sess-time{font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;margin:2px 0;}
.sess-heat{font-size:13px;}
.market-tabs{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap;}
.mktab{padding:7px 14px;border-radius:8px;border:1.5px solid var(--border);
  background:var(--panel);font-family:'JetBrains Mono',monospace;font-size:10px;
  cursor:pointer;color:var(--dim);font-weight:700;box-shadow:var(--shadow);text-align:center;}
.mktab.on{background:linear-gradient(135deg,#1a56db,#0e3fa8);color:#fff;
  border-color:var(--accent);box-shadow:0 4px 16px rgba(26,86,219,0.3);}
.layout{display:grid;grid-template-columns:1fr 320px;gap:12px;}
@media(max-width:900px){.layout{grid-template-columns:1fr;}}
.left{display:flex;flex-direction:column;gap:12px;}
.right{display:flex;flex-direction:column;gap:10px;}
.card{background:var(--panel);border:1.5px solid var(--border);
  border-radius:10px;box-shadow:var(--shadow);overflow:hidden;}
.chdr{display:flex;align-items:center;justify-content:space-between;
  padding:9px 14px;border-bottom:1.5px solid var(--border);background:var(--panel2);}
.ctitle{font-family:'Bebas Neue',sans-serif;font-size:14px;letter-spacing:2px;color:var(--accent);}
.hero{background:linear-gradient(135deg,#1a56db,#0e3fa8);border-radius:10px;
  padding:16px 20px;box-shadow:var(--shadow2);color:#fff;
  display:flex;gap:18px;align-items:center;flex-wrap:wrap;}
.hero-price{font-family:'JetBrains Mono',monospace;font-size:clamp(28px,5vw,44px);font-weight:700;}
.hero-meta{display:flex;gap:16px;flex-wrap:wrap;margin-top:4px;}
.hm{text-align:center;}
.hm-v{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;}
.hm-l{font-size:9px;color:rgba(255,255,255,0.6);letter-spacing:1px;}
.signal-box{padding:14px 16px;border-radius:8px;margin:10px;font-weight:700;
  font-size:13px;text-align:center;border:1.5px solid;}
.signal-bull{background:var(--green2);color:var(--green);border-color:rgba(10,158,92,0.3);}
.signal-bear{background:var(--red2);color:var(--red);border-color:rgba(224,45,60,0.3);}
.signal-wait{background:var(--gold2);color:var(--gold);border-color:rgba(180,83,9,0.3);}
.signal-neu{background:var(--blue2);color:var(--blue);border-color:rgba(26,86,219,0.3);}
.chk-row{display:flex;align-items:center;gap:8px;padding:6px 14px;
  border-bottom:1px solid var(--border);font-size:12px;}
.chk-icon{width:18px;height:18px;border-radius:50%;display:flex;align-items:center;
  justify-content:center;font-size:10px;font-weight:700;flex-shrink:0;}
.chk-pass{background:var(--green2);color:var(--green);}
.chk-fail{background:var(--red2);color:var(--red);}
.trade-card{margin:10px;padding:12px;border-radius:8px;border:1.5px solid;font-size:12px;}
.trade-bull{background:var(--green2);border-color:rgba(10,158,92,0.3);}
.trade-bear{background:var(--red2);border-color:rgba(224,45,60,0.3);}
.tr{display:flex;justify-content:space-between;margin:3px 0;}
.tl{color:var(--dim);}
.tv{font-family:'JetBrains Mono',monospace;font-weight:700;}
.stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:12px;}
.stat-box{background:var(--panel2);border-radius:8px;padding:10px;text-align:center;
  border:1.5px solid var(--border);}
.stat-val{font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:700;}
.stat-lbl{font-size:10px;color:var(--dim);margin-top:2px;}
.brain-box{padding:10px 14px;}
.brain-item{display:flex;align-items:flex-start;gap:6px;margin:5px 0;font-size:12px;line-height:1.4;}
.brain-dot{width:6px;height:6px;border-radius:50%;background:var(--accent);margin-top:5px;flex-shrink:0;}
.mini{background:var(--panel);border:1.5px solid var(--border);border-radius:10px;
  box-shadow:var(--shadow);overflow:hidden;}
.mini-hdr{display:flex;justify-content:space-between;align-items:center;
  padding:8px 12px;background:var(--panel2);border-bottom:1px solid var(--border);}
.mini-name{font-family:'Bebas Neue',sans-serif;font-size:13px;letter-spacing:2px;color:var(--accent);}
.mini-px{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;}
.mini-body{padding:8px 12px;}
.ms{display:flex;justify-content:space-between;padding:3px 0;font-size:11px;
  border-bottom:1px solid rgba(0,0,0,0.04);}
.ms-l{color:var(--dim);}
.ms-v{font-family:'JetBrains Mono',monospace;font-weight:700;}
.mini-jadui{margin:8px 12px 10px;padding:6px 10px;border-radius:6px;
  font-size:11px;font-weight:700;text-align:center;border:1px solid;}
.ind-row{display:flex;align-items:center;gap:8px;padding:7px 14px;
  border-bottom:1px solid var(--border);font-size:12px;}
.iname{width:60px;font-weight:700;}
.ibar{flex:1;height:6px;background:var(--border);border-radius:3px;overflow:hidden;}
.ibfill{height:100%;border-radius:3px;}
.ival{width:70px;font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;text-align:right;}
.isig{padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700;white-space:nowrap;}
.bull{background:var(--green2);color:var(--green);}
.bear{background:var(--red2);color:var(--red);}
.neu{background:var(--blue2);color:var(--blue);}
.alert-item{display:flex;align-items:center;gap:8px;padding:8px 12px;
  border-bottom:1px solid var(--border);font-size:11px;}
.alert-time{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--dim);white-space:nowrap;}
.alert-badge{padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700;white-space:nowrap;}
.alert-msg{flex:1;line-height:1.3;}
.footer{background:var(--panel);border:1.5px solid var(--border);border-radius:8px;
  padding:10px 16px;display:flex;gap:10px;align-items:flex-start;}
.footer-badge{background:var(--gold2);color:var(--gold);border:1px solid rgba(180,83,9,0.3);
  padding:3px 8px;border-radius:4px;font-size:10px;font-weight:700;white-space:nowrap;}
.footer-text{font-size:11px;color:var(--dim);line-height:1.5;}
.strategy-badge{display:inline-block;padding:3px 10px;border-radius:20px;
  font-size:10px;font-weight:700;font-family:'JetBrains Mono',monospace;}
.market-closed{text-align:center;padding:40px 20px;color:var(--dim);}
.mc-icon{font-size:48px;margin-bottom:12px;}
.mc-title{font-family:'Bebas Neue',sans-serif;font-size:24px;color:var(--accent);letter-spacing:3px;}
.mc-sub{font-size:12px;margin-top:6px;}
.option-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:12px;}
.opt-box{border-radius:8px;padding:12px;text-align:center;border:1.5px solid;}
.opt-lbl{font-size:10px;color:var(--dim);margin-bottom:4px;}
.opt-price{font-family:'JetBrains Mono',monospace;font-size:20px;font-weight:700;}
.opt-type{font-size:10px;font-weight:700;margin-top:4px;}
.risk-box{margin:10px;padding:12px;background:var(--panel2);border-radius:8px;
  border:1.5px solid var(--border);font-size:12px;}
</style>
</head>
<body>
<div class="wrap">

<!-- TOPBAR -->
<div class="topbar">
  <div>
    <h1>⚡ GOAT PRO</h1>
    <small>MULTI MARKET COMMAND CENTER</small>
  </div>
  <div class="tb-right">
    <div class="tb-stat">
      <div class="tb-val" id="tb-time">--:--:--</div>
      <div class="tb-label">IST</div>
    </div>
    <div class="tb-div"></div>
    <div class="tb-stat">
      <div class="tb-val" id="tb-spot">--</div>
      <div class="tb-label">NIFTY</div>
    </div>
    <div class="tb-div"></div>
    <div class="tb-stat">
      <div class="tb-val" id="tb-vix" style="color:#fbbf24">--</div>
      <div class="tb-label">VIX</div>
    </div>
    <div class="tb-div"></div>
    <div class="tb-stat">
      <div class="tb-val" id="tb-pnl" style="color:#4ade80">₹0</div>
      <div class="tb-label">SESSION P&L</div>
    </div>
    <div class="tb-div"></div>
    <div class="tb-stat">
      <div class="tb-val" id="tb-trades">0</div>
      <div class="tb-label">TRADES</div>
    </div>
    <div class="tb-div"></div>
    <div class="live-pill">
      <div class="ldot" id="live-dot"></div>
      <span id="market-status-pill">LOADING</span>
    </div>
  </div>
</div>

<!-- LEGAL BANNER -->
<div class="legal-banner">
  <div class="legal-badge">⚖️ DISCLAIMER</div>
  <span>Yeh tool sirf <strong>Personal Educational Use</strong> ke liye hai. SEBI registered financial advice nahi hai. Trading mein substantial risk hota hai. Paper Trading Only.</span>
</div>

<!-- SESSION STRIPS -->
<div class="sess-strip">
  <div class="sess" id="s1"><div class="sess-name">PRE-OPEN</div><div class="sess-time">9:00–9:15</div><div class="sess-heat">🌅</div></div>
  <div class="sess" id="s2"><div class="sess-name">MORNING</div><div class="sess-time">9:15–11:00</div><div class="sess-heat">🔥</div></div>
  <div class="sess" id="s3"><div class="sess-name">MIDDAY</div><div class="sess-time">11:00–2:00</div><div class="sess-heat">⚡</div></div>
  <div class="sess" id="s4"><div class="sess-name">POWER HOUR</div><div class="sess-time">2:00–3:30</div><div class="sess-heat">🚀</div></div>
</div>

<!-- MARKET TABS -->
<div class="market-tabs">
  <div class="mktab on" onclick="switchMarket(this,'nifty')">🔵 NIFTY<div class="chg" id="nf-chg">--</div></div>
  <div class="mktab" onclick="switchMarket(this,'banknifty')">🏦 BANKNIFTY<div class="chg" id="bn-chg">--</div></div>
  <div class="mktab" onclick="switchMarket(this,'finnifty')">📊 FINNIFTY<div class="chg" id="fn-chg">--</div></div>
  <div class="mktab" onclick="switchMarket(this,'sensex')">📈 SENSEX<div class="chg" id="sx-chg">--</div></div>
</div>

<!-- MAIN LAYOUT -->
<div class="layout">
<div class="left">

  <!-- HERO PRICE CARD -->
  <div class="hero" id="hero-card">
    <div style="flex:1">
      <div style="font-size:10px;color:rgba(255,255,255,0.6);letter-spacing:2px;margin-bottom:4px;" id="hero-label">🔵 NIFTY 50 · INDEX</div>
      <div class="hero-price" id="hero-price">--</div>
      <div class="hero-meta">
        <div class="hm"><div class="hm-v" id="h-open">--</div><div class="hm-l">OPEN</div></div>
        <div class="hm"><div class="hm-v" id="h-high" style="color:#4ade80">--</div><div class="hm-l">HIGH</div></div>
        <div class="hm"><div class="hm-v" id="h-low" style="color:#f87171">--</div><div class="hm-l">LOW</div></div>
        <div class="hm"><div class="hm-v" id="h-expiry" style="color:#fbbf24">--</div><div class="hm-l">EXPIRY</div></div>
        <div class="hm"><div class="hm-v" id="h-atm">--</div><div class="hm-l">ATM</div></div>
      </div>
    </div>
    <div style="text-align:center">
      <div style="font-size:10px;color:rgba(255,255,255,0.6);margin-bottom:4px;">STRATEGY</div>
      <div class="strategy-badge" id="strategy-badge" style="background:rgba(255,255,255,0.2);color:#fff">LOADING</div>
      <div style="font-size:10px;color:rgba(255,255,255,0.6);margin-top:8px;" id="market-condition-lbl">--</div>
    </div>
  </div>

  <!-- SIGNAL + OPTIONS -->
  <div class="card">
    <div class="chdr">
      <div class="ctitle">🎯 GOAT SIGNAL</div>
      <div style="font-size:11px;color:var(--dim)" id="signal-source">Angel One</div>
    </div>
    <div id="signal-display">
      <div class="signal-box signal-neu">⏳ Loading market data...</div>
    </div>

    <!-- CE/PE Option Prices -->
    <div class="option-grid">
      <div class="opt-box" style="background:var(--green2);border-color:rgba(10,158,92,0.3);">
        <div class="opt-lbl" id="ce-label">-- CE</div>
        <div class="opt-price" style="color:var(--green)" id="ce-price">₹--</div>
        <div class="opt-type" style="color:var(--green)">📈 CALL</div>
        <div id="ce-dir" style="font-size:10px;color:var(--green);margin-top:4px;">--</div>
      </div>
      <div class="opt-box" style="background:var(--red2);border-color:rgba(224,45,60,0.3);">
        <div class="opt-lbl" id="pe-label">-- PE</div>
        <div class="opt-price" style="color:var(--red)" id="pe-price">₹--</div>
        <div class="opt-type" style="color:var(--red)">📉 PUT</div>
        <div id="pe-dir" style="font-size:10px;color:var(--red);margin-top:4px;">--</div>
      </div>
    </div>

    <!-- Risk per trade -->
    <div class="risk-box">
      <div style="display:flex;justify-content:space-between;margin-bottom:6px;">
        <span style="color:var(--dim)">⚠️ Max Risk (1 lot)</span>
        <span id="risk-amt" style="font-family:'JetBrains Mono',monospace;font-weight:700;color:var(--red)">₹--</span>
      </div>
      <div style="display:flex;justify-content:space-between;margin-bottom:6px;">
        <span style="color:var(--dim)">🎯 Max Reward (1 lot)</span>
        <span id="reward-amt" style="font-family:'JetBrains Mono',monospace;font-weight:700;color:var(--green)">₹--</span>
      </div>
      <div style="display:flex;justify-content:space-between;">
        <span style="color:var(--dim)">📦 Lot Size</span>
        <span id="lot-size-lbl" style="font-family:'JetBrains Mono',monospace;font-weight:700;color:var(--blue)">65 qty</span>
      </div>
    </div>
  </div>

  <!-- 5-POINT CHECKLIST -->
  <div class="card">
    <div class="chdr">
      <div class="ctitle">✅ SIGNAL CHECKLIST</div>
      <div style="font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;" id="chk-score">0/5</div>
    </div>
    <div id="chk-list">
      <div class="chk-row"><div class="chk-icon chk-fail" id="c0">✗</div><span>Index above round level</span></div>
      <div class="chk-row"><div class="chk-icon chk-fail" id="c1">✗</div><span>Velocity / Momentum positive</span></div>
      <div class="chk-row"><div class="chk-icon chk-fail" id="c2">✗</div><span>Away from round number trap</span></div>
      <div class="chk-row"><div class="chk-icon chk-fail" id="c3">✗</div><span>VIX safe (below 18)</span></div>
      <div class="chk-row"><div class="chk-icon chk-fail" id="c4">✗</div><span>Within valid ATM strike range</span></div>
      <div class="chk-row"><div class="chk-icon chk-fail" id="c5">✗</div><span>EMA9 > EMA21 (trend confirm)</span></div>
      <div class="chk-row"><div class="chk-icon chk-fail" id="c6">✗</div><span>RSI between 40–65 (safe zone)</span></div>
    </div>
  </div>

  <!-- ACTIVE TRADE -->
  <div class="card" id="trade-section">
    <div class="chdr">
      <div class="ctitle">📊 ACTIVE TRADE</div>
      <div id="live-pnl-badge" style="font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;color:var(--green)">--</div>
    </div>
    <div id="trade-display">
      <div style="padding:20px;text-align:center;color:var(--dim);font-size:12px;">No active trade</div>
    </div>
  </div>

  <!-- GOAT BRAIN -->
  <div class="card">
    <div class="chdr"><div class="ctitle">🧠 GOAT BRAIN — KYUN LIYA?</div></div>
    <div class="brain-box" id="brain-list">
      <div style="color:var(--dim);font-size:12px;">Waiting for signal...</div>
    </div>
  </div>

  <!-- SESSION STATS -->
  <div class="card">
    <div class="chdr"><div class="ctitle">📈 SESSION PERFORMANCE</div></div>
    <div class="stat-grid">
      <div class="stat-box">
        <div class="stat-val" id="stat-trades">0</div>
        <div class="stat-lbl">TOTAL TRADES</div>
      </div>
      <div class="stat-box">
        <div class="stat-val" id="stat-wr" style="color:var(--green)">0%</div>
        <div class="stat-lbl">WIN RATE</div>
      </div>
      <div class="stat-box">
        <div class="stat-val" id="stat-pnl-pts">+0</div>
        <div class="stat-lbl">P&L (POINTS)</div>
      </div>
      <div class="stat-box">
        <div class="stat-val" id="stat-pnl-rs" style="color:var(--green)">₹0</div>
        <div class="stat-lbl">P&L (RUPEES)</div>
      </div>
    </div>
  </div>

</div><!-- /left -->

<!-- RIGHT PANEL -->
<div class="right">

  <!-- BANKNIFTY MINI -->
  <div class="mini">
    <div class="mini-hdr">
      <div class="mini-name">🏦 BANKNIFTY</div>
      <div class="mini-px" id="bn-px">--</div>
    </div>
    <div class="mini-body">
      <div class="ms"><div class="ms-l">📊 RSI</div><div class="ms-v" id="bn-rsi">--</div></div>
      <div class="ms"><div class="ms-l">📈 EMA9</div><div class="ms-v" id="bn-ema">--</div></div>
      <div class="ms"><div class="ms-l">🎯 ATM</div><div class="ms-v" id="bn-atm">--</div></div>
      <div class="ms"><div class="ms-l">🌊 TREND</div><div class="ms-v" id="bn-tr">--</div></div>
    </div>
    <div class="mini-jadui" id="bn-jadui" style="background:var(--blue2);color:var(--blue);border-color:rgba(26,86,219,0.3)">⏳ Loading...</div>
  </div>

  <!-- FINNIFTY MINI -->
  <div class="mini">
    <div class="mini-hdr">
      <div class="mini-name">📊 FINNIFTY</div>
      <div class="mini-px" id="fn-px">--</div>
    </div>
    <div class="mini-body">
      <div class="ms"><div class="ms-l">📊 RSI</div><div class="ms-v" id="fn-rsi">--</div></div>
      <div class="ms"><div class="ms-l">🎯 ATM</div><div class="ms-v" id="fn-atm">--</div></div>
      <div class="ms"><div class="ms-l">🌊 TREND</div><div class="ms-v" id="fn-tr">--</div></div>
    </div>
    <div class="mini-jadui" id="fn-jadui" style="background:var(--blue2);color:var(--blue);border-color:rgba(26,86,219,0.3)">⏳ Loading...</div>
  </div>

  <!-- SENSEX MINI -->
  <div class="mini">
    <div class="mini-hdr">
      <div class="mini-name">📈 SENSEX</div>
      <div class="mini-px" id="sx-px">--</div>
    </div>
    <div class="mini-body">
      <div class="ms"><div class="ms-l">📊 RSI</div><div class="ms-v" id="sx-rsi">--</div></div>
      <div class="ms"><div class="ms-l">🌊 TREND</div><div class="ms-v" id="sx-tr">--</div></div>
      <div class="ms"><div class="ms-l">📏 VIX</div><div class="ms-v" id="sx-vix">--</div></div>
    </div>
    <div class="mini-jadui" id="sx-jadui" style="background:var(--blue2);color:var(--blue);border-color:rgba(26,86,219,0.3)">⏳ Loading...</div>
  </div>

  <!-- LIVE ALERTS -->
  <div class="card">
    <div class="chdr"><div class="ctitle">🔔 LIVE ALERTS</div></div>
    <div id="alert-list" style="max-height:200px;overflow-y:auto;">
      <div style="padding:16px;text-align:center;color:var(--dim);font-size:11px;">Waiting for signals...</div>
    </div>
  </div>

  <!-- PAPER TRADE JOURNAL -->
  <div class="card">
    <div class="chdr"><div class="ctitle">📓 TRADE JOURNAL</div></div>
    <div id="journal-list" style="max-height:250px;overflow-y:auto;padding:8px;">
      <div style="padding:8px;text-align:center;color:var(--dim);font-size:11px;">No trades yet today</div>
    </div>
  </div>

  <!-- OPENING ANALYSIS -->
  <div class="card">
    <div class="chdr"><div class="ctitle">🌅 OPENING ANALYSIS</div></div>
    <div style="padding:12px;font-size:12px;">
      <div class="ms"><div class="ms-l">Gap</div><div class="ms-v" id="open-gap">--</div></div>
      <div class="ms"><div class="ms-l">Prev Close</div><div class="ms-v" id="prev-close">--</div></div>
      <div class="ms"><div class="ms-l">Today Open</div><div class="ms-v" id="today-open">--</div></div>
      <div class="ms"><div class="ms-l">Bias</div><div class="ms-v" id="open-bias">--</div></div>
    </div>
  </div>

  <!-- MARKET CLOSED STATE -->
  <div id="market-closed-card" class="card" style="display:none;">
    <div class="market-closed">
      <div class="mc-icon">🌙</div>
      <div class="mc-title">MARKET CLOSED</div>
      <div class="mc-sub">Market opens at 9:15 AM IST<br>Monday to Friday</div>
    </div>
  </div>

</div><!-- /right -->
</div><!-- /layout -->

<!-- FOOTER -->
<div class="footer" style="margin-top:12px;">
  <div class="footer-badge">⚖️ LEGAL</div>
  <div class="footer-text">
    ⚠️ GOAT PRO sirf <strong>Personal Educational Paper Trading</strong> ke liye hai.
    SEBI registered financial advice nahi hai. Real money invest mat karo is tool ke basis pe.
    Trading mein substantial risk hota hai. Paper Trading Only.
  </div>
</div>

</div><!-- /wrap -->

<script>
// ══════════════════════════════════════════════════
// CLOCK + SESSION
// ══════════════════════════════════════════════════
function updateClock(){
  const n=new Date();
  const t=`${String(n.getHours()).padStart(2,'0')}:${String(n.getMinutes()).padStart(2,'0')}:${String(n.getSeconds()).padStart(2,'0')}`;
  document.getElementById('tb-time').textContent=t;
  const m=n.getHours()*60+n.getMinutes();
  [['s1',9*60,9*60+15],['s2',9*60+15,11*60],['s3',11*60,14*60],['s4',14*60,15*60+30]].forEach(([id,from,to])=>{
    const el=document.getElementById(id);
    if(el) el.classList.toggle('active',m>=from&&m<to);
  });
}
setInterval(updateClock,1000); updateClock();

// ══════════════════════════════════════════════════
// HELPERS
// ══════════════════════════════════════════════════
function setText(id,val){const e=document.getElementById(id);if(e)e.textContent=val;}
function setHtml(id,val){const e=document.getElementById(id);if(e)e.innerHTML=val;}
function setColor(id,col){const e=document.getElementById(id);if(e)e.style.color=col;}

function fmtPts(v){return (v>=0?'+':'')+v.toFixed(1)+' pts';}
function fmtRs(v){return (v>=0?'+₹':'-₹')+Math.abs(v).toLocaleString('en-IN');}
function fmtNum(v){return v.toLocaleString('en-IN');}

// ══════════════════════════════════════════════════
// MARKET SWITCH (tabs)
// ══════════════════════════════════════════════════
let currentMarket='nifty';
function switchMarket(el,key){
  document.querySelectorAll('.mktab').forEach(t=>t.classList.remove('on'));
  el.classList.add('on');
  currentMarket=key;
  // Update hero label
  const labels={nifty:'🔵 NIFTY 50 · INDEX',banknifty:'🏦 BANKNIFTY · INDEX',
    finnifty:'📊 FINNIFTY · INDEX',sensex:'📈 SENSEX · BSE INDEX'};
  setText('hero-label',labels[key]||labels.nifty);
}

// ══════════════════════════════════════════════════
// MAIN DATA FETCH — REAL API
// ══════════════════════════════════════════════════
let lastStatus='';
let lastSignal='';

async function fetchData(){
  try{
    const res=await fetch('/api/data');
    const d=await res.json();
    updateDashboard(d);
  }catch(e){
    setText('market-status-pill','ERROR');
  }
}

async function fetchTrades(){
  try{
    const res=await fetch('/api/trades');
    const d=await res.json();
    updateTrades(d);
  }catch(e){}
}

function updateDashboard(d){
  // Market status
  const ms=d.market_status||'CLOSED';
  setText('market-status-pill',ms);
  const dot=document.getElementById('live-dot');
  if(dot) dot.style.background=ms==='OPEN'?'#4ade80':'#f87171';

  // Market closed state
  const closedCard=document.getElementById('market-closed-card');
  if(closedCard) closedCard.style.display=ms==='OPEN'?'none':'block';

  if(!d.spot) return;

  // Topbar
  setText('tb-spot',fmtNum(d.spot));
  const vixEl=document.getElementById('tb-vix');
  if(vixEl){
    vixEl.textContent=d.vix||'--';
    vixEl.style.color=d.vix>18?'#f87171':d.vix>14?'#fbbf24':'#4ade80';
  }

  // Hero price
  setText('hero-price',fmtNum(d.spot));
  setText('h-expiry',d.expiry||'--');
  setText('h-atm',d.atm_strike||'--');

  // Strategy + condition
  const cond=d.market_condition||'UNKNOWN';
  const strat=d.strategy_mode||'WAIT';
  const sb=document.getElementById('strategy-badge');
  if(sb){
    sb.textContent=strat;
    const colors={TREND:'background:#d1fae5;color:#065f46',RANGE:'background:#dbeafe;color:#1e40af',WAIT:'background:#fef3c7;color:#92400e'};
    sb.style.cssText=colors[strat]||colors.WAIT;
    sb.style.borderRadius='20px';sb.style.padding='3px 10px';sb.style.fontSize='10px';sb.style.fontWeight='700';
  }
  setText('market-condition-lbl',cond.replace('_',' '));

  // Signal box
  const status=d.status||'BLOCKED';
  const signal=d.signal||'--';
  let sigCls='signal-neu', sigTxt=signal;
  if(status==='TRADE_ACTIVE') sigCls=d.direction==='LONG'?'signal-bull':'signal-bear';
  else if(status==='SETUP_READY') sigCls='signal-neu';
  else if(signal.includes('TARGET')) sigCls='signal-bull';
  else if(signal.includes('SL HIT')) sigCls='signal-bear';
  setHtml('signal-display',`<div class="signal-box ${sigCls}">${sigTxt}</div>`);

  // New signal alert
  if(signal!==lastSignal && status==='TRADE_ACTIVE'){
    addAlert(signal, d.direction==='LONG');
    lastSignal=signal;
  }

  // CE/PE prices
  const ceEl=document.getElementById('ce-price');
  const peEl=document.getElementById('pe-price');
  if(ceEl) ceEl.textContent=d.ce_premium>0?'₹'+d.ce_premium.toFixed(1):'₹--';
  if(peEl) peEl.textContent=d.pe_premium>0?'₹'+d.pe_premium.toFixed(1):'₹--';
  setText('ce-label',(d.atm_strike||'--')+' CE');
  setText('pe-label',(d.atm_strike||'--')+' PE');

  const activeDir=d.direction||'LONG';
  setText('ce-dir',activeDir==='LONG'?'↑ ACTIVE — BUY CE':'↑ Inactive');
  setText('pe-dir',activeDir==='SHORT'?'↓ ACTIVE — BUY PE':'↓ Inactive');
  setColor('ce-dir',activeDir==='LONG'?'var(--green)':'var(--dim)');
  setColor('pe-dir',activeDir==='SHORT'?'var(--red)':'var(--dim)');

  // Risk/Reward
  const ls=d.lot_size||65;
  if(d.sl&&d.entry&&d.entry>0){
    const slPts=Math.abs(d.entry-d.sl);
    const tgtPts=Math.abs(d.target-d.entry);
    setText('risk-amt','-₹'+Math.round(slPts*ls).toLocaleString('en-IN'));
    setText('reward-amt','+₹'+Math.round(tgtPts*ls).toLocaleString('en-IN'));
  }
  setText('lot-size-lbl',ls+' qty (1 lot)');

  // Checklist
  const chk=d.chk||[false,false,false,false,false];
  const ema_ok=d.ema9>0&&d.ema21>0&&d.ema9>d.ema21;
  const rsi_ok=d.rsi>=40&&d.rsi<=65;
  const allChk=[...chk,ema_ok,rsi_ok];
  const chkLabels=['Index above round level','Velocity positive','Away from round number','VIX safe (below 18)','Valid ATM range','EMA9 > EMA21','RSI 40–65 zone'];
  let passCount=0;
  allChk.forEach((v,i)=>{
    const el=document.getElementById('c'+i);
    if(el){
      el.className='chk-icon '+(v?'chk-pass':'chk-fail');
      el.textContent=v?'✓':'✗';
      if(v) passCount++;
    }
  });
  setText('chk-score',passCount+'/7');

  // Active trade display
  if(status==='TRADE_ACTIVE'&&d.entry>0){
    const dir=d.direction==='LONG';
    const livePnlPts=dir?(d.spot-d.entry):(d.entry-d.spot);
    const livePnlRs=Math.round(livePnlPts*ls);
    const pnlColor=livePnlRs>=0?'var(--green)':'var(--red)';
    setHtml('trade-display',`
      <div class="trade-card ${dir?'trade-bull':'trade-bear'}">
        <div class="tr"><span class="tl">Direction</span><span class="tv" style="color:${dir?'var(--green)':'var(--red)'}">${d.direction} ${d.atm_strike} ${d.option_type}</span></div>
        <div class="tr"><span class="tl">Entry</span><span class="tv">₹${d.entry}</span></div>
        <div class="tr"><span class="tl">Current</span><span class="tv">₹${d.spot}</span></div>
        <div class="tr"><span class="tl">Target</span><span class="tv" style="color:var(--green)">₹${d.target}</span></div>
        <div class="tr"><span class="tl">Stop Loss</span><span class="tv" style="color:var(--red)">₹${d.sl}</span></div>
        <div class="tr"><span class="tl">Live P&L</span><span class="tv" style="color:${pnlColor}">${fmtPts(livePnlPts)} | ${fmtRs(livePnlRs)}</span></div>
        <div class="tr"><span class="tl">Lot Size</span><span class="tv">${ls} qty</span></div>
      </div>`);
    const badge=document.getElementById('live-pnl-badge');
    if(badge){badge.textContent=fmtRs(livePnlRs);badge.style.color=livePnlRs>=0?'var(--green)':'var(--red)';}
  } else {
    setHtml('trade-display','<div style="padding:20px;text-align:center;color:var(--dim);font-size:12px;">No active trade</div>');
    setText('live-pnl-badge','--');
  }

  // GOAT Brain
  const reasons=d.brain_reasons||[];
  if(reasons.length>0){
    setHtml('brain-list',reasons.map(r=>`<div class="brain-item"><div class="brain-dot"></div><span>${r}</span></div>`).join(''));
  }

  // Session stats
  const pnl=d.pnl||0;
  const pnlRs=d.session_pnl_rs||0;
  setText('tb-pnl',fmtRs(pnlRs));
  setColor('tb-pnl',pnlRs>=0?'#4ade80':'#f87171');
  setText('tb-trades',d.total||0);
  setText('stat-trades',d.total||0);
  setText('stat-pnl-pts',(pnl>=0?'+':'')+pnl+' pts');
  setText('stat-pnl-rs',fmtRs(pnlRs));
  setColor('stat-pnl-pts',pnl>=0?'var(--green)':'var(--red)');
  setColor('stat-pnl-rs',pnlRs>=0?'var(--green)':'var(--red)');
  const wr=d.wins&&d.total?Math.round(d.wins/d.total*100):0;
  setText('stat-wr',wr+'%');
  setColor('stat-wr',wr>=60?'var(--green)':wr>=40?'var(--gold)':'var(--red)');

  // BankNifty mini
  if(d.bn_spot){
    const bnSpot=d.bn_spot;
    setText('bn-px','₹'+fmtNum(bnSpot));
    setText('bn-rsi',d.bn_rsi||'--');
    setText('bn-ema',d.bn_ema9||'--');
    setText('bn-atm',d.bn_atm||'--');
    const bnTrend=d.bn_condition||'--';
    setText('bn-tr',bnTrend);
    const bnJadui=document.getElementById('bn-jadui');
    if(bnJadui){
      if(bnTrend==='TRENDING_UP'){bnJadui.textContent='🟢 BULLISH — CE Buy setup';bnJadui.style.background='var(--green2)';bnJadui.style.color='var(--green)';}
      else if(bnTrend==='TRENDING_DOWN'){bnJadui.textContent='🔴 BEARISH — PE Buy setup';bnJadui.style.background='var(--red2)';bnJadui.style.color='var(--red)';}
      else{bnJadui.textContent='⏳ SIDEWAYS — No clear setup';bnJadui.style.background='var(--gold2)';bnJadui.style.color='var(--gold)';}
    }
    const bnChg=document.getElementById('bn-chg');
    if(bnChg&&d.bn_spot) bnChg.textContent=fmtNum(d.bn_spot);
  }

  // Opening analysis
  if(d.opening_gap!==undefined){
    setText('open-gap',(d.opening_gap>=0?'+':'')+d.opening_gap+' pts');
    setColor('open-gap',d.opening_gap>0?'var(--green)':d.opening_gap<0?'var(--red)':'var(--dim)');
    setText('prev-close',d.prev_close?fmtNum(d.prev_close):'--');
    const bias=d.opening_gap>50?'🟢 BULLISH GAP':d.opening_gap<-50?'🔴 BEARISH GAP':'⏳ FLAT OPEN';
    setText('open-bias',bias);
  }

  // EMA/RSI display in topbar area
  if(d.ema9&&d.ema21&&d.rsi){
    setText('sx-rsi',d.rsi||'--');
    setText('sx-tr',(d.ema9>d.ema21?'BULL':'BEAR'));
    setText('sx-vix',d.vix||'--');
  }
}

function updateTrades(d){
  const closed=d.closed||[];
  const stats=d.stats||{};

  if(closed.length===0){
    setHtml('journal-list','<div style="padding:8px;text-align:center;color:var(--dim);font-size:11px;">No trades yet today</div>');
    return;
  }

  const rows=closed.slice(0,10).map(t=>{
    const pnl=t.pnl||0;
    const pnlRs=t.pnl_rs||0;
    const col=pnl>=0?'var(--green)':'var(--red)';
    return `<div style="padding:8px 10px;border-bottom:1px solid var(--border);font-size:11px;">
      <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
        <span style="font-weight:700;color:${t.direction==='LONG'?'var(--green)':'var(--red)'}">${t.direction} ${t.atm_strike||''} ${t.option_type||''}</span>
        <span style="font-family:'JetBrains Mono',monospace;font-weight:700;color:${col}">${fmtPts(pnl)}</span>
      </div>
      <div style="display:flex;justify-content:space-between;color:var(--dim);">
        <span>${t.entry_time||''} → ${t.exit_time||''}</span>
        <span style="color:${col};font-weight:700;">${fmtRs(pnlRs)}</span>
      </div>
      <div style="color:var(--dim);margin-top:2px;">${t.exit_reason||''}</div>
    </div>`;
  }).join('');
  setHtml('journal-list',rows);
}

// ══════════════════════════════════════════════════
// ALERTS
// ══════════════════════════════════════════════════
function addAlert(msg, isBull){
  const now=new Date();
  const t=`${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`;
  const list=document.getElementById('alert-list');
  if(!list) return;
  const item=document.createElement('div');
  item.className='alert-item';
  const cls=isBull?'bull':'bear';
  const badge=isBull?'🟢 BUY SIGNAL':'🔴 SELL SIGNAL';
  item.innerHTML=`
    <div class="alert-time">${t}</div>
    <div class="alert-badge ${cls}">${badge}</div>
    <div class="alert-msg">${msg}</div>`;
  if(list.querySelector('div[style*="Waiting"]')) list.innerHTML='';
  list.insertBefore(item,list.firstChild);
  if(list.children.length>8) list.removeChild(list.lastChild);
}

// ══════════════════════════════════════════════════
// INIT + POLLING
// ══════════════════════════════════════════════════
fetchData();
fetchTrades();
setInterval(fetchData, 5000);
setInterval(fetchTrades, 15000);
</script>
</body>
</html>"""


# ═════════════════════════════════════════════════════════

@app.route("/")
def index():
    """Serve the main dashboard UI"""
    from flask import Response
    return Response(TEMPLATE, mimetype='text/html; charset=utf-8')


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
