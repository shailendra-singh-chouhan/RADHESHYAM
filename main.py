# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
  GOAT PRO — Virtual Paper Trading System v2.0
  Single-file Flask app for Render deployment.

  FIXES APPLIED:
  1. ORB Strategy (Opening Range Breakout) — 9:15-9:30 range
  2. Auto trade actually triggers on ORB breakout + confirmation
  3. CE/PE premium fetching enabled with multiple symbol formats
  4. Removed: fake GOAT Brain, Introspection, Decision Quality bloat
  5. Clean, focused trading engine

  Required Environment Variables:
    ANGEL_API_KEY       = Your Angel One API Key
    ANGEL_CLIENT_ID     = Your Angel One Client ID
    ANGEL_MPIN          = Your Angel One MPIN
    ANGEL_TOTP_SECRET   = Your Angel One TOTP Secret (base32)
    TELEGRAM_BOT_TOKEN  = (Optional) Telegram bot token
    TELEGRAM_CHAT_ID    = (Optional) Telegram chat ID
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

# Lot Sizes (NSE revised Jan 2026)
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

# ── Database ──
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

# Candle tracking
CANDLE_5MIN = []
_candle_current = {"open": 0, "high": 0, "low": 0, "close": 0, "time": None}

# ═════════════════════════════════════════════════════════
# MARKET GUARD — 9:15 AM to 3:30 PM, Mon-Fri only
# ═════════════════════════════════════════════════════════

def market_status():
    now = datetime.datetime.now()
    weekday = now.weekday()
    t = now.time()
    if weekday >= 5:
        return "CLOSED", "Weekend — Market Closed"
    if t < datetime.time(9, 15):
        return "CLOSED", "Market opens at 9:15 AM"
    if t > datetime.time(15, 30):
        return "CLOSED", "Market Closed after 3:30 PM"
    return "OPEN", "Market Open"


def get_atm_strike(spot, interval=50):
    return int(round(spot / interval) * interval)


def get_expiry_label():
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
# SQLITE DATABASE — Trade logging only (cleaned)
# ═════════════════════════════════════════════════════════

def db_init():
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
        entry_time TEXT, exit_time TEXT,
        pnl REAL, status TEXT DEFAULT 'OPEN',
        atm_strike INTEGER DEFAULT 0,
        option_type TEXT DEFAULT 'CE',
        pnl_rs REAL DEFAULT 0
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
        'source', 'note', 'post_note', 'exit_reason', 'entry_time',
        'exit_time', 'pnl', 'status', 'atm_strike', 'option_type', 'pnl_rs'
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
        'source', 'note', 'post_note', 'exit_reason', 'entry_time',
        'exit_time', 'pnl', 'status', 'atm_strike', 'option_type', 'pnl_rs'
    ]
    return [dict(zip(cols, r)) for r in rows]


def db_insert_trade(t):
    conn, db_type = get_db_conn()
    cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    sql = f"""INSERT INTO paper_trades
        (direction, entry_price, target, sl, qty, setup, source, note, entry_time, status,
         atm_strike, option_type, pnl_rs)
        VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})"""
    cur.execute(sql, (
        t['direction'], t['entry_price'], t['target'], t['sl'], t.get('qty', 65),
        t['setup'], t.get('source', 'MANUAL'), t.get('note', ''),
        t['entry_time'], 'OPEN', t.get('atm_strike', 0),
        t.get('option_type', 'CE'), t.get('pnl_rs', 0)
    ))
    conn.commit()
    cur.close(); conn.close()


def db_close_trade(trade_id, exit_price, exit_reason, post_note, pnl, pnl_rs=0):
    conn, db_type = get_db_conn()
    cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    sql = f"""UPDATE paper_trades SET exit_price={ph}, exit_reason={ph}, post_note={ph},
        exit_time={ph}, pnl={ph}, pnl_rs={ph}, status='CLOSED' WHERE id={ph}"""
    cur.execute(sql, (
        exit_price, exit_reason, post_note,
        time.strftime("%H:%M:%S"), pnl, pnl_rs, trade_id
    ))
    conn.commit()
    cur.close(); conn.close()


def db_clear_trades():
    conn, _ = get_db_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM paper_trades")
    conn.commit()
    cur.close(); conn.close()


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
    now = time.time()
    if SESSION_CACHE["obj"] and (now - SESSION_CACHE["logged_in_at"] < SESSION_CACHE["ttl_seconds"]):
        return SESSION_CACHE["obj"], None
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


# ═════════════════════════════════════════════════════════
# OPTION PREMIUM FETCHING — ENABLED NOW
# ═════════════════════════════════════════════════════════

def get_both_premiums(index, atm_strike, expiry_date):
    """
    Fetch CE and PE live premium from Angel One NFO.
    Returns (ce_ltp, pe_ltp) as floats.
    """
    try:
        now = time.time()
        if now - OPTION_CACHE["last_fetch"] < OPTION_CACHE["ttl"]:
            return OPTION_CACHE["ce_ltp"], OPTION_CACHE["pe_ltp"]

        obj, err = get_session()
        if err or not obj:
            return 0.0, 0.0

        ce_ltp, pe_ltp = 0.0, 0.0
        ce_formats, pe_formats = get_angel_option_symbols(index, atm_strike, expiry_date)

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
                        OPTION_CACHE["ce_token"] = tok
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
                        OPTION_CACHE["pe_token"] = tok
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
    today = datetime.date.today()
    days_until_thursday = (3 - today.weekday()) % 7
    if days_until_thursday == 0 and datetime.datetime.now().time() > datetime.time(15, 30):
        days_until_thursday = 7
    expiry_date = today + datetime.timedelta(days=days_until_thursday + (weeks_ahead * 7))
    return expiry_date


def get_angel_option_symbols(index, strike, expiry_date):
    yy = str(expiry_date.year)[2:]
    dd = f"{expiry_date.day:02d}"
    mm = f"{expiry_date.month}"
    mm2 = f"{expiry_date.month:02d}"
    months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
    mon = months[expiry_date.month - 1]

    formats = [
        f"{index}{yy}{mm}{dd}{strike}",
        f"{index}{yy}{mm2}{dd}{strike}",
        f"{index}{yy}{mon}{dd}{strike}",
        f"{index}{dd}{mon}{expiry_date.year}{strike}",
        f"{index}{dd}{mon}{yy}{strike}",
    ]

    ce_symbols = [f + "CE" for f in formats]
    pe_symbols = [f + "PE" for f in formats]
    return ce_symbols, pe_symbols


def get_nifty_yfinance():
    if not YFINANCE_AVAILABLE:
        return None
    try:
        t = yf.Ticker("^NSEI")
        return float(t.fast_info['last_price'])
    except Exception:
        return None


def get_vix_yfinance():
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
# ORB STRATEGY — Opening Range Breakout
# ═════════════════════════════════════════════════════════

"""
ORB STRATEGY LOGIC:
1. 9:15-9:30 AM: Capture Opening Range (High/Low of first 15 min)
2. After 9:30 AM: Wait for breakout above range-high (LONG) or below range-low (SHORT)
3. Entry: On confirmed breakout with momentum
4. SL: Other side of the range (range low for LONG, range high for SHORT)
5. Target: 1:2 R:R minimum (2x the range width)
6. Max 2 ORB trades per day (one direction only after first trade)

Additional filters:
- VIX < 20 (volatility check)
- Price above/below 9-EMA (trend confirmation)
- No trade if range is too wide (> 1.5% of spot)
"""

# ORB State
ORB_STATE = {
    "active": False,           # Is ORB currently active?
    "range_high": 0.0,         # High of 9:15-9:30
    "range_low": 0.0,          # Low of 9:15-9:30
    "range_set": False,        # Has range been captured?
    "range_width": 0.0,        # High - Low
    "orb_direction": None,     # "LONG" or "SHORT" or None
    "orb_triggered": False,    # Has ORB trade been taken?
    "orb_trades_today": 0,     # Count of ORB trades today
    "last_orb_date": "",       # Date of last ORB
    "confirmation_candles": 0, # Candles confirming breakout
    "breakout_price": 0.0,     # Price where breakout happened
}

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


def get_banknifty_spot():
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


def get_finnifty_spot():
    try:
        obj, err = get_session()
        if err or not obj:
            return None
        resp = obj.ltpData("NSE", "Nifty Fin Service", TOKENS["FINNIFTY"]["token"])
        if resp and resp.get("data"):
            return float(resp["data"]["ltp"])
    except Exception:
        pass
    return None


# ═════════════════════════════════════════════════════════
# TRADING ENGINE — ORB Core State Machine
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
    "atm_strike": 0,
    "session_pnl": 0.0,
    "trades_total": 0,
    "trades_won": 0,
    "trades_lost": 0,
    "data_source": "—",
    "last_candle_signal": 0,
    "ce_premium": 0.0,
    "pe_premium": 0.0,
    "option_entry_premium": 0.0,
    "lot_size": 65,
    "active_index": "NIFTY",
    "session_pnl_rs": 0.0,
    "ema9": 0.0,
    "ema21": 0.0,
    "rsi": 50.0,
    "market_condition": "UNKNOWN",
    "strategy_mode": "ORB_WAIT",
    "bn_spot": 0.0,
    "fn_spot": 0.0,
    "sx_spot": 0.0,
    "bn_rsi": 50.0,
    "fn_rsi": 50.0,
    "sx_rsi": 50.0,
    "bn_ema9": 0.0,
    "bn_condition": "UNKNOWN",
    "fn_condition": "UNKNOWN",
    "sx_condition": "UNKNOWN",
    "bn_atm": 0,
    "fn_atm": 0,
    "today_open": 0.0,
}


def run_pipeline():
    """
    Main pipeline: ORB Strategy + Auto Trade + Premium Fetch
    """
    global ENGINE, ORB_STATE

    now = time.time()
    if ENGINE["payload"] and (now - ENGINE["last_update"] < ENGINE["tick_ttl"]):
        return ENGINE["payload"]

    mstatus, mmsg = market_status()
    expiry_label = get_expiry_label()

    if mstatus != "OPEN":
        ENGINE["status"] = "BLOCKED"
        ENGINE["signal"] = mmsg
        # Reset ORB for next day
        ORB_STATE["range_set"] = False
        ORB_STATE["orb_triggered"] = False
        payload = {
            "spot": ENGINE.get("last_spot", 0),
            "vix": 15.0,
            "velocity": 0,
            "status": "BLOCKED",
            "signal": mmsg,
            "entry": 0, "target": 0, "sl": 0,
            "chk": [False] * 5, "pass_count": 0,
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
            "strategy_mode": "MARKET_CLOSED",
            "market_condition": "CLOSED",
            "ce_premium": 0.0, "pe_premium": 0.0,
            "lot_size": ENGINE.get("lot_size", 65),
            "session_pnl_rs": round(ENGINE.get("session_pnl_rs", 0), 0),
            "orb_state": ORB_STATE,
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
            SESSION_CACHE["logged_in_at"] = 0

    if spot is None:
        spot = get_nifty_yfinance()
        data_source = "yfinance" if spot else "—"
    if vix is None:
        vix = get_vix_yfinance() or 15.0

    if spot is None:
        return ENGINE["payload"] or {"error": "All data sources failed"}

    ENGINE["data_source"] = data_source
    update_candle(spot)
    update_price_history(spot)

    # ── Multi-index data fetch ──
    try:
        bn_spot = get_banknifty_spot()
        if bn_spot and bn_spot > 0:
            ENGINE["bn_spot"] = bn_spot
            bn_history = ENGINE.get("_bn_history", [])
            bn_history.append(bn_spot)
            if len(bn_history) > 30: bn_history.pop(0)
            ENGINE["_bn_history"] = bn_history
            ENGINE["bn_rsi"] = calc_rsi(bn_history)
            ENGINE["bn_ema9"] = calc_ema(bn_history, 9)
            ENGINE["bn_atm"] = get_atm_strike(bn_spot, 100)
    except Exception:
        pass

    try:
        fn_spot = get_finnifty_spot()
        if fn_spot and fn_spot > 0:
            ENGINE["fn_spot"] = fn_spot
            fn_history = ENGINE.get("_fn_history", [])
            fn_history.append(fn_spot)
            if len(fn_history) > 30: fn_history.pop(0)
            ENGINE["_fn_history"] = fn_history
            ENGINE["fn_rsi"] = calc_rsi(fn_history)
            ENGINE["fn_atm"] = get_atm_strike(fn_spot, 50)
    except Exception:
        pass

    try:
        sx_spot = get_sensex_spot()
        if sx_spot and sx_spot > 0:
            ENGINE["sx_spot"] = sx_spot
            sx_history = ENGINE.get("_sx_history", [])
            sx_history.append(sx_spot)
            if len(sx_history) > 30: sx_history.pop(0)
            ENGINE["_sx_history"] = sx_history
            ENGINE["sx_rsi"] = calc_rsi(sx_history)
    except Exception:
        pass

    # Velocity
    if ENGINE["last_spot"] > 0:
        ENGINE["velocity"] = round(spot - ENGINE["last_spot"], 2)
    ENGINE["last_spot"] = spot
    vel = ENGINE["velocity"]

    # EMA + RSI
    if len(_price_history) >= 9:
        ENGINE["ema9"] = calc_ema(_price_history, 9)
    if len(_price_history) >= 21:
        ENGINE["ema21"] = calc_ema(_price_history, 21)
    if len(_price_history) >= 15:
        ENGINE["rsi"] = calc_rsi(_price_history)

    ema9 = ENGINE.get("ema9", 0.0)
    ema21 = ENGINE.get("ema21", 0.0)
    rsi = ENGINE.get("rsi", 50.0)

    # ── ATM Strike ──
    atm = get_atm_strike(spot, interval=50)
    ENGINE["atm_strike"] = atm

    # ── Lot Size ──
    active_index = ENGINE.get("active_index", "NIFTY")
    lot_size = LOT_SIZES.get(active_index, 65)
    ENGINE["lot_size"] = lot_size

    # ── Fetch CE/PE Premium ──
    ce_ltp, pe_ltp = 0.0, 0.0
    try:
        if mstatus == "OPEN":
            expiry_date = get_expiry_str_for_angel(0)
            ce_ltp, pe_ltp = get_both_premiums(active_index, atm, expiry_date)
            ENGINE["ce_premium"] = ce_ltp
            ENGINE["pe_premium"] = pe_ltp
    except Exception:
        ce_ltp = ENGINE.get("ce_premium", 0.0)
        pe_ltp = ENGINE.get("pe_premium", 0.0)

    # ── ORB STRATEGY LOGIC ───────────────────────────
    now_time = datetime.datetime.now().time()
    orb_high = ORB_STATE["range_high"]
    orb_low = ORB_STATE["range_low"]
    orb_set = ORB_STATE["range_set"]
    orb_triggered = ORB_STATE["orb_triggered"]
    orb_direction = ORB_STATE["orb_direction"]
    orb_trades_today = ORB_STATE["orb_trades_today"]
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    # Reset daily counters
    if ORB_STATE["last_orb_date"] != today_str:
        ORB_STATE["orb_trades_today"] = 0
        ORB_STATE["last_orb_date"] = today_str
        ORB_STATE["range_set"] = False
        ORB_STATE["orb_triggered"] = False
        ORB_STATE["orb_direction"] = None
        ORB_STATE["confirmation_candles"] = 0

    # PHASE 1: Capture ORB Range (9:15 - 9:30 AM)
    if now_time >= datetime.time(9, 15) and now_time < datetime.time(9, 30):
        if not orb_set:
            ORB_STATE["range_high"] = spot
            ORB_STATE["range_low"] = spot
            ORB_STATE["range_set"] = True
            ORB_STATE["range_width"] = 0
            ENGINE["strategy_mode"] = "ORB_CAPTURE"
            ENGINE["signal"] = "📊 ORB: Capturing opening range (9:15-9:30)"
        else:
            # Update range
            ORB_STATE["range_high"] = max(ORB_STATE["range_high"], spot)
            ORB_STATE["range_low"] = min(ORB_STATE["range_low"], spot)
            ORB_STATE["range_width"] = ORB_STATE["range_high"] - ORB_STATE["range_low"]
            ENGINE["signal"] = f"📊 ORB Range: {ORB_STATE['range_low']:.0f} - {ORB_STATE['range_high']:.0f} (width: {ORB_STATE['range_width']:.1f})"

    # PHASE 2: Wait for Breakout (after 9:30 AM)
    elif now_time >= datetime.time(9, 30) and now_time <= datetime.time(15, 20):
        if not orb_set:
            # Missed ORB window, use first available candle
            if len(CANDLE_5MIN) > 0:
                first_candle = CANDLE_5MIN[0]
                ORB_STATE["range_high"] = first_candle["high"]
                ORB_STATE["range_low"] = first_candle["low"]
                ORB_STATE["range_set"] = True
                ORB_STATE["range_width"] = first_candle["high"] - first_candle["low"]
            else:
                ENGINE["strategy_mode"] = "ORB_WAIT"
                ENGINE["signal"] = "⏳ Waiting for ORB range data..."

        if orb_set and not orb_triggered and ORB_STATE["orb_trades_today"] < 2:
            range_width = ORB_STATE["range_width"]
            range_pct = (range_width / spot) * 100 if spot > 0 else 0

            # Check if range is valid (not too wide)
            if range_pct > 1.5:
                ENGINE["strategy_mode"] = "ORB_INVALID"
                ENGINE["signal"] = f"❌ ORB Range too wide ({range_pct:.2f}%) — No trade today"
            else:
                # Check for breakout
                breakout_buffer = max(range_width * 0.1, 2.0)  # 10% of range or 2 points

                # LONG breakout: Price above range high + buffer
                if spot > (orb_high + breakout_buffer):
                    ORB_STATE["confirmation_candles"] += 1
                    if ORB_STATE["confirmation_candles"] >= 2:  # 2-tick confirmation
                        direction = "LONG"
                        option_type = "CE"
                        entry = round(orb_high + breakout_buffer, 2)
                        sl = round(orb_low - 2, 2)  # Below range low
                        tgt_pts = max(range_width * 2, 40)  # 2x range or min 40 pts
                        target = round(entry + tgt_pts, 2)

                        # Filters
                        vix_ok = vix < 20
                        ema_ok = ema9 > ema21 if (ema9 > 0 and ema21 > 0) else True
                        rsi_ok = 35 <= rsi <= 70

                        if vix_ok and ema_ok and rsi_ok:
                            ENGINE["direction"] = direction
                            ENGINE["option_type"] = option_type
                            ENGINE["entry"] = entry
                            ENGINE["target"] = target
                            ENGINE["sl"] = sl
                            ENGINE["strategy_mode"] = "ORB_BREAKOUT"
                            ENGINE["signal"] = f"🚀 ORB LONG SETUP | {atm} CE | Entry: {entry} | TGT: {target} | SL: {sl}"

                            # Check auto-trigger
                            open_t = db_open_trade()
                            if not open_t and ENGINE["status"] != "TRADE_ACTIVE":
                                # Auto enter
                                ENGINE["status"] = "TRADE_ACTIVE"
                                ENGINE["signal"] = f"✅ AUTO-ENTERED ORB LONG at {spot} | {atm} CE"
                                ORB_STATE["orb_triggered"] = True
                                ORB_STATE["orb_direction"] = "LONG"
                                ORB_STATE["breakout_price"] = spot
                                ORB_STATE["orb_trades_today"] += 1

                                entry_premium = ce_ltp if ce_ltp > 0 else 0
                                ENGINE["option_entry_premium"] = entry_premium

                                db_insert_trade({
                                    "direction": direction,
                                    "entry_price": spot,
                                    "target": target,
                                    "sl": sl,
                                    "qty": lot_size,
                                    "setup": "ORB Breakout LONG",
                                    "source": "AUTO",
                                    "note": f"ORB Range: {orb_low:.0f}-{orb_high:.0f} | Width: {range_width:.1f} | VIX: {vix:.1f} | EMA9: {ema9} | RSI: {rsi}",
                                    "entry_time": time.strftime("%H:%M:%S"),
                                    "atm_strike": atm,
                                    "option_type": option_type
                                })
                                tg(f"🚀 ORB LONG ENTERED\n{spot} | {atm} CE\nTGT: {target} | SL: {sl}\nLot: {lot_size} qty")
                        else:
                            fail_reasons = []
                            if not vix_ok: fail_reasons.append(f"VIX high ({vix:.1f})")
                            if not ema_ok: fail_reasons.append("EMA bearish")
                            if not rsi_ok: fail_reasons.append(f"RSI {rsi}")
                            ENGINE["signal"] = f"⏳ ORB LONG filtered: {' | '.join(fail_reasons)}"
                            ORB_STATE["confirmation_candles"] = 0

                # SHORT breakout: Price below range low - buffer
                elif spot < (orb_low - breakout_buffer):
                    ORB_STATE["confirmation_candles"] += 1
                    if ORB_STATE["confirmation_candles"] >= 2:
                        direction = "SHORT"
                        option_type = "PE"
                        entry = round(orb_low - breakout_buffer, 2)
                        sl = round(orb_high + 2, 2)
                        tgt_pts = max(range_width * 2, 40)
                        target = round(entry - tgt_pts, 2)

                        vix_ok = vix < 20
                        ema_ok = ema9 < ema21 if (ema9 > 0 and ema21 > 0) else True
                        rsi_ok = 30 <= rsi <= 65

                        if vix_ok and ema_ok and rsi_ok:
                            ENGINE["direction"] = direction
                            ENGINE["option_type"] = option_type
                            ENGINE["entry"] = entry
                            ENGINE["target"] = target
                            ENGINE["sl"] = sl
                            ENGINE["strategy_mode"] = "ORB_BREAKOUT"
                            ENGINE["signal"] = f"🔻 ORB SHORT SETUP | {atm} PE | Entry: {entry} | TGT: {target} | SL: {sl}"

                            open_t = db_open_trade()
                            if not open_t and ENGINE["status"] != "TRADE_ACTIVE":
                                ENGINE["status"] = "TRADE_ACTIVE"
                                ENGINE["signal"] = f"✅ AUTO-ENTERED ORB SHORT at {spot} | {atm} PE"
                                ORB_STATE["orb_triggered"] = True
                                ORB_STATE["orb_direction"] = "SHORT"
                                ORB_STATE["breakout_price"] = spot
                                ORB_STATE["orb_trades_today"] += 1

                                entry_premium = pe_ltp if pe_ltp > 0 else 0
                                ENGINE["option_entry_premium"] = entry_premium

                                db_insert_trade({
                                    "direction": direction,
                                    "entry_price": spot,
                                    "target": target,
                                    "sl": sl,
                                    "qty": lot_size,
                                    "setup": "ORB Breakout SHORT",
                                    "source": "AUTO",
                                    "note": f"ORB Range: {orb_low:.0f}-{orb_high:.0f} | Width: {range_width:.1f} | VIX: {vix:.1f} | EMA9: {ema9} | RSI: {rsi}",
                                    "entry_time": time.strftime("%H:%M:%S"),
                                    "atm_strike": atm,
                                    "option_type": option_type
                                })
                                tg(f"🔻 ORB SHORT ENTERED\n{spot} | {atm} PE\nTGT: {target} | SL: {sl}\nLot: {lot_size} qty")
                        else:
                            fail_reasons = []
                            if not vix_ok: fail_reasons.append(f"VIX high ({vix:.1f})")
                            if not ema_ok: fail_reasons.append("EMA bullish")
                            if not rsi_ok: fail_reasons.append(f"RSI {rsi}")
                            ENGINE["signal"] = f"⏳ ORB SHORT filtered: {' | '.join(fail_reasons)}"
                            ORB_STATE["confirmation_candles"] = 0
                else:
                    # Inside range
                    ORB_STATE["confirmation_candles"] = 0
                    ENGINE["strategy_mode"] = "ORB_WAIT"
                    ENGINE["signal"] = f"⏳ ORB Waiting... Range: {orb_low:.0f} - {orb_high:.0f} | Current: {spot:.0f}"

        elif orb_triggered:
            ENGINE["strategy_mode"] = "ORB_ACTIVE"
            ENGINE["signal"] = f"✅ ORB {orb_direction} Active | Monitoring SL/TGT"

    else:
        ENGINE["strategy_mode"] = "MARKET_CLOSED"
        ENGINE["signal"] = "Market Closed"

    # ── Manage active trade (SL/TGT) ──
    open_t = db_open_trade()
    if ENGINE["status"] == "TRADE_ACTIVE" and open_t and open_t['source'] == 'AUTO':
        direction = ENGINE["direction"]
        if direction == "LONG":
            hit_tgt = spot >= ENGINE["target"]
            hit_sl = spot <= ENGINE["sl"]
        else:
            hit_tgt = spot <= ENGINE["target"]
            hit_sl = spot >= ENGINE["sl"]

        if hit_tgt:
            pnl_pts = round(abs(ENGINE["target"] - open_t['entry_price']), 1)
            pnl_rs = round(pnl_pts * lot_size, 0)
            db_close_trade(open_t['id'], spot, 'Target Hit', pnl_pts, pnl_rs=pnl_rs)
            ENGINE["trades_won"] += 1
            ENGINE["trades_total"] += 1
            ENGINE["session_pnl"] += pnl_pts
            ENGINE["session_pnl_rs"] += pnl_rs
            ENGINE["status"] = "BLOCKED"
            ENGINE["signal"] = f"🎯 TARGET HIT +{pnl_pts} pts | +₹{int(pnl_rs):,}"
            tg(f"🎯 ORB TARGET HIT\n+{pnl_pts} pts | +₹{int(pnl_rs)}\n{atm} {ENGINE['option_type']}")

        elif hit_sl:
            pnl_pts = round(abs(open_t['entry_price'] - ENGINE["sl"]), 1)
            pnl_rs = round(pnl_pts * lot_size, 0)
            db_close_trade(open_t['id'], spot, 'Stoploss Hit', -pnl_pts, pnl_rs=-pnl_rs)
            ENGINE["trades_lost"] += 1
            ENGINE["trades_total"] += 1
            ENGINE["session_pnl"] -= pnl_pts
            ENGINE["session_pnl_rs"] -= pnl_rs
            ENGINE["status"] = "BLOCKED"
            ENGINE["signal"] = f"🛑 SL HIT -{pnl_pts} pts | -₹{int(pnl_rs):,}"
            tg(f"🛑 ORB SL HIT\n-{pnl_pts} pts | -₹{int(pnl_rs)}\n{atm} {ENGINE['option_type']}")
            # Allow reverse ORB after SL
            ORB_STATE["orb_triggered"] = False
            ORB_STATE["confirmation_candles"] = 0

    # ── 5-Point Checklist for UI ──
    base100 = (spot // 100) * 100
    base50 = (spot // 50) * 50
    dist_round = abs(spot - base50)

    chk = [
        spot > base100,                          # Above round level
        vel > 0,                                  # Positive velocity
        dist_round > 10,                          # Away from round
        vix < 20.0,                               # VIX safe
        (spot - base100) < (0.008 * spot)        # Valid range
    ]
    ema_ok = ema9 > 0 and ema21 > 0 and ema9 > ema21
    rsi_ok = 35 <= rsi <= 70
    pass_count = sum(chk)

    # Stats
    total = ENGINE["trades_total"]
    win_rate = round(ENGINE["trades_won"] / total * 100) if total else 0

    # Live P&L calculation
    live_pnl_rs = 0.0
    if ENGINE["status"] == "TRADE_ACTIVE" and ENGINE.get("option_entry_premium", 0) > 0:
        if ENGINE["option_type"] == "CE" and ce_ltp > 0:
            live_pnl_rs = round((ce_ltp - ENGINE["option_entry_premium"]) * lot_size, 0)
        elif ENGINE["option_type"] == "PE" and pe_ltp > 0:
            live_pnl_rs = round((pe_ltp - ENGINE["option_entry_premium"]) * lot_size, 0)

    payload = {
        "spot": round(spot, 2),
        "vix": round(vix, 2),
        "velocity": vel,
        "status": ENGINE["status"],
        "signal": ENGINE["signal"],
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
        "option_type": ENGINE.get("option_type", "CE"),
        "direction": ENGINE.get("direction", "LONG"),
        "data_source": data_source,
        "expiry": expiry_label,
        "candles": CANDLE_5MIN[-50:],
        "current_candle": _candle_current,
        "bn_spot": round(ENGINE.get("bn_spot", 0.0), 2),
        "bn_rsi": round(ENGINE.get("bn_rsi", 50.0), 1),
        "bn_ema9": round(ENGINE.get("bn_ema9", 0.0), 2),
        "bn_atm": ENGINE.get("bn_atm", 0),
        "fn_spot": round(ENGINE.get("fn_spot", 0.0), 2),
        "fn_rsi": round(ENGINE.get("fn_rsi", 50.0), 1),
        "fn_atm": ENGINE.get("fn_atm", 0),
        "sx_spot": round(ENGINE.get("sx_spot", 0.0), 2),
        "sx_rsi": round(ENGINE.get("sx_rsi", 50.0), 1),
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "rsi": round(rsi, 1),
        "market_condition": ENGINE.get("market_condition", "UNKNOWN"),
        "strategy_mode": ENGINE.get("strategy_mode", "WAIT"),
        "ce_premium": round(ce_ltp, 2),
        "pe_premium": round(pe_ltp, 2),
        "option_entry_premium": round(ENGINE.get("option_entry_premium", 0.0), 2),
        "lot_size": lot_size,
        "active_index": active_index,
        "session_pnl_rs": round(ENGINE.get("session_pnl_rs", 0.0), 0),
        "live_pnl_rs": live_pnl_rs,
        "orb_high": round(ORB_STATE.get("range_high", 0), 2),
        "orb_low": round(ORB_STATE.get("range_low", 0), 2),
        "orb_width": round(ORB_STATE.get("range_width", 0), 2),
        "orb_set": ORB_STATE.get("range_set", False),
        "orb_triggered": ORB_STATE.get("orb_triggered", False),
        "orb_trades_today": ORB_STATE.get("orb_trades_today", 0),
    }

    ENGINE.update({"last_update": now, "payload": payload})
    return payload


# ═════════════════════════════════════════════════════════
# HTML TEMPLATE — Clean ORB Dashboard
# ═════════════════════════════════════════════════════════

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>⚡ GOAT PRO v2 — ORB Trading</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=JetBrains+Mono:wght@300;400;700&family=Rajdhani:wght@400;600;700&display=swap');
:root{
  --bg:#0a0e1a;--panel:#111827;--panel2:#1a2236;--border:#1f2937;
  --accent:#3b82f6;--accent2:#2563eb;
  --green:#22c55e;--green2:#064e3b;
  --red:#ef4444;--red2:#7f1d1d;
  --gold:#f59e0b;--gold2:#78350f;
  --blue:#3b82f6;--blue2:#1e3a5f;
  --purple:#a855f7;--purple2:#4c1d95;
  --dim:#6b7280;--text:#f3f4f6;
  --shadow:0 2px 12px rgba(0,0,0,0.4);
  --shadow2:0 6px 28px rgba(59,130,246,0.2);
}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:'Rajdhani',sans-serif;min-height:100vh;}
body::before{content:'';position:fixed;inset:0;
  background:radial-gradient(ellipse 70% 40% at 10% 0%,rgba(59,130,246,0.08),transparent 70%),
             radial-gradient(ellipse 50% 50% at 90% 100%,rgba(168,85,247,0.05),transparent 70%);
  pointer-events:none;z-index:0;}
.wrap{max-width:1400px;margin:0 auto;padding:12px 14px;position:relative;z-index:1;}
.topbar{display:flex;align-items:center;justify-content:space-between;
  background:linear-gradient(135deg,#1e3a5f,#0f172a);
  border:1px solid var(--border);border-radius:12px;padding:14px 22px;margin-bottom:12px;
  box-shadow:var(--shadow2);flex-wrap:wrap;gap:10px;}
.topbar h1{font-family:'Bebas Neue',sans-serif;font-size:clamp(20px,4vw,34px);
  letter-spacing:5px;color:#fff;}
.topbar small{font-family:'JetBrains Mono',monospace;font-size:9px;
  color:rgba(255,255,255,0.4);letter-spacing:2px;display:block;}
.tb-right{display:flex;gap:12px;align-items:center;flex-wrap:wrap;}
.tb-stat{text-align:center;}
.tb-val{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;color:#fff;}
.tb-label{font-size:9px;color:rgba(255,255,255,0.4);letter-spacing:1px;text-transform:uppercase;}
.tb-div{width:1px;height:26px;background:rgba(255,255,255,0.15);}
.live-pill{display:flex;align-items:center;gap:6px;
  background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.15);
  border-radius:20px;padding:5px 14px;
  font-family:'JetBrains Mono',monospace;font-size:10px;color:#fff;}
.ldot{width:7px;height:7px;border-radius:50%;background:#22c55e;
  box-shadow:0 0 8px #22c55e;animation:blink 1.2s infinite;}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0.2}}
.legal-banner{background:linear-gradient(135deg,#78350f,#451a03);
  border:1.5px solid #92400e;border-radius:8px;padding:10px 16px;
  margin-bottom:12px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.legal-banner span{font-size:12px;color:#fbbf24;line-height:1.5;flex:1;}
.legal-badge{background:#92400e;color:#fff;border-radius:4px;
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
.mktab.on{background:linear-gradient(135deg,#1e3a5f,#3b82f6);color:#fff;
  border-color:var(--accent);box-shadow:0 4px 16px rgba(59,130,246,0.3);}
.layout{display:grid;grid-template-columns:1fr 320px;gap:12px;}
@media(max-width:900px){.layout{grid-template-columns:1fr;}}
.left{display:flex;flex-direction:column;gap:12px;}
.right{display:flex;flex-direction:column;gap:10px;}
.card{background:var(--panel);border:1.5px solid var(--border);
  border-radius:10px;box-shadow:var(--shadow);overflow:hidden;}
.chdr{display:flex;align-items:center;justify-content:space-between;
  padding:9px 14px;border-bottom:1.5px solid var(--border);background:var(--panel2);}
.ctitle{font-family:'Bebas Neue',sans-serif;font-size:14px;letter-spacing:2px;color:var(--accent);}
.hero{background:linear-gradient(135deg,#1e3a5f,#0f172a);border-radius:10px;
  padding:16px 20px;box-shadow:var(--shadow2);color:#fff;
  display:flex;gap:18px;align-items:center;flex-wrap:wrap;border:1px solid var(--border);}
.hero-price{font-family:'JetBrains Mono',monospace;font-size:clamp(28px,5vw,44px);font-weight:700;}
.hero-meta{display:flex;gap:16px;flex-wrap:wrap;margin-top:4px;}
.hm{text-align:center;}
.hm-v{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;}
.hm-l{font-size:9px;color:rgba(255,255,255,0.5);letter-spacing:1px;}
.signal-box{padding:14px 16px;border-radius:8px;margin:10px;font-weight:700;
  font-size:13px;text-align:center;border:1.5px solid;}
.signal-bull{background:var(--green2);color:var(--green);border-color:rgba(34,197,94,0.4);}
.signal-bear{background:var(--red2);color:var(--red);border-color:rgba(239,68,68,0.4);}
.signal-wait{background:var(--gold2);color:var(--gold);border-color:rgba(245,158,11,0.4);}
.signal-neu{background:var(--blue2);color:var(--blue);border-color:rgba(59,130,246,0.4);}
.signal-active{background:var(--purple2);color:var(--purple);border-color:rgba(168,85,247,0.4);}
.chk-row{display:flex;align-items:center;gap:8px;padding:6px 14px;
  border-bottom:1px solid var(--border);font-size:12px;}
.chk-icon{width:18px;height:18px;border-radius:50%;display:flex;align-items:center;
  justify-content:center;font-size:10px;font-weight:700;flex-shrink:0;}
.chk-pass{background:rgba(34,197,94,0.15);color:var(--green);}
.chk-fail{background:rgba(239,68,68,0.15);color:var(--red);}
.trade-card{margin:10px;padding:12px;border-radius:8px;border:1.5px solid;font-size:12px;}
.trade-bull{background:rgba(34,197,94,0.08);border-color:rgba(34,197,94,0.3);}
.trade-bear{background:rgba(239,68,68,0.08);border-color:rgba(239,68,68,0.3);}
.tr{display:flex;justify-content:space-between;margin:3px 0;}
.tl{color:var(--dim);}
.tv{font-family:'JetBrains Mono',monospace;font-weight:700;}
.stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:12px;}
.stat-box{background:var(--panel2);border-radius:8px;padding:10px;text-align:center;
  border:1.5px solid var(--border);}
.stat-val{font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:700;}
.stat-lbl{font-size:10px;color:var(--dim);margin-top:2px;}
.orb-box{padding:12px 14px;background:var(--panel2);border-radius:8px;margin:10px;
  border:1.5px solid var(--border);}
.orb-title{font-family:'Bebas Neue',sans-serif;font-size:16px;letter-spacing:2px;color:var(--gold);margin-bottom:8px;}
.orb-row{display:flex;justify-content:space-between;padding:4px 0;font-size:12px;border-bottom:1px solid var(--border);}
.orb-label{color:var(--dim);}
.orb-value{font-family:'JetBrains Mono',monospace;font-weight:700;}
.orb-bar{height:8px;background:var(--border);border-radius:4px;margin:8px 0;overflow:hidden;position:relative;}
.orb-fill{height:100%;border-radius:4px;transition:all 0.5s ease;}
.orb-marker{position:absolute;top:-4px;width:4px;height:16px;background:#fff;border-radius:2px;box-shadow:0 0 4px rgba(255,255,255,0.5);}
.mini{background:var(--panel);border:1.5px solid var(--border);border-radius:10px;
  box-shadow:var(--shadow);overflow:hidden;}
.mini-hdr{display:flex;justify-content:space-between;align-items:center;
  padding:8px 12px;background:var(--panel2);border-bottom:1px solid var(--border);}
.mini-name{font-family:'Bebas Neue',sans-serif;font-size:13px;letter-spacing:2px;color:var(--accent);}
.mini-px{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;}
.mini-body{padding:8px 12px;}
.ms{display:flex;justify-content:space-between;padding:3px 0;font-size:11px;
  border-bottom:1px solid rgba(255,255,255,0.04);}
.ms-l{color:var(--dim);}
.ms-v{font-family:'JetBrains Mono',monospace;font-weight:700;}
.mini-jadui{margin:8px 12px 10px;padding:6px 10px;border-radius:6px;
  font-size:11px;font-weight:700;text-align:center;border:1px solid;}
.alert-item{display:flex;align-items:center;gap:8px;padding:8px 12px;
  border-bottom:1px solid var(--border);font-size:11px;}
.alert-time{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--dim);white-space:nowrap;}
.alert-badge{padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700;white-space:nowrap;}
.alert-msg{flex:1;line-height:1.3;}
.footer{background:var(--panel);border:1.5px solid var(--border);border-radius:8px;
  padding:10px 16px;display:flex;gap:10px;align-items:flex-start;}
.footer-badge{background:var(--gold2);color:var(--gold);border:1px solid rgba(245,158,11,0.3);
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
    <h1>⚡ GOAT PRO v2</h1>
    <small>ORB STRATEGY — OPENING RANGE BREAKOUT</small>
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
      <div class="tb-val" id="tb-pnl" style="color:#22c55e">₹0</div>
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
  <span>Personal Educational Paper Trading only. SEBI registered financial advice nahi hai. Trading mein substantial risk hota hai.</span>
</div>

<!-- SESSION STRIPS -->
<div class="sess-strip">
  <div class="sess" id="s1"><div class="sess-name">PRE-OPEN</div><div class="sess-time">9:00–9:15</div><div class="sess-heat">🌅</div></div>
  <div class="sess" id="s2"><div class="sess-name">ORB WINDOW</div><div class="sess-time">9:15–9:30</div><div class="sess-heat">📊</div></div>
  <div class="sess" id="s3"><div class="sess-name">BREAKOUT</div><div class="sess-time">9:30–11:00</div><div class="sess-heat">🚀</div></div>
  <div class="sess" id="s4"><div class="sess-name">POWER HOUR</div><div class="sess-time">2:00–3:30</div><div class="sess-heat">⚡</div></div>
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
      <div style="font-size:10px;color:rgba(255,255,255,0.5);letter-spacing:2px;margin-bottom:4px;" id="hero-label">🔵 NIFTY 50 · ORB STRATEGY</div>
      <div class="hero-price" id="hero-price">--</div>
      <div class="hero-meta">
        <div class="hm"><div class="hm-v" id="h-open">--</div><div class="hm-l">OPEN</div></div>
        <div class="hm"><div class="hm-v" id="h-high" style="color:#22c55e">--</div><div class="hm-l">HIGH</div></div>
        <div class="hm"><div class="hm-v" id="h-low" style="color:#ef4444">--</div><div class="hm-l">LOW</div></div>
        <div class="hm"><div class="hm-v" id="h-expiry" style="color:#fbbf24">--</div><div class="hm-l">EXPIRY</div></div>
        <div class="hm"><div class="hm-v" id="h-atm">--</div><div class="hm-l">ATM</div></div>
      </div>
    </div>
    <div style="text-align:center">
      <div style="font-size:10px;color:rgba(255,255,255,0.5);margin-bottom:4px;">STRATEGY</div>
      <div class="strategy-badge" id="strategy-badge" style="background:rgba(59,130,246,0.2);color:#60a5fa">ORB</div>
      <div style="font-size:10px;color:rgba(255,255,255,0.5);margin-top:8px;" id="market-condition-lbl">--</div>
    </div>
  </div>

  <!-- ORB STATUS BOX -->
  <div class="card">
    <div class="chdr">
      <div class="ctitle">📊 ORB STATUS</div>
      <div style="font-size:11px;color:var(--dim)" id="orb-mode">WAITING</div>
    </div>
    <div class="orb-box">
      <div class="orb-title">🎯 OPENING RANGE</div>
      <div class="orb-row">
        <span class="orb-label">Range High</span>
        <span class="orb-value" style="color:var(--green)" id="orb-high">--</span>
      </div>
      <div class="orb-row">
        <span class="orb-label">Range Low</span>
        <span class="orb-value" style="color:var(--red)" id="orb-low">--</span>
      </div>
      <div class="orb-row">
        <span class="orb-label">Range Width</span>
        <span class="orb-value" style="color:var(--gold)" id="orb-width">--</span>
      </div>
      <div class="orb-row">
        <span class="orb-label">Trades Today</span>
        <span class="orb-value" id="orb-trades">0/2</span>
      </div>
      <div style="margin-top:8px;font-size:11px;color:var(--dim);" id="orb-status-text">Waiting for 9:15 AM market open...</div>
    </div>
  </div>

  <!-- SIGNAL + OPTIONS -->
  <div class="card">
    <div class="chdr">
      <div class="ctitle">🎯 SIGNAL</div>
      <div style="font-size:11px;color:var(--dim)" id="signal-source">Angel One</div>
    </div>
    <div id="signal-display">
      <div class="signal-box signal-wait">⏳ Waiting for ORB setup...</div>
    </div>

    <!-- CE/PE Option Prices -->
    <div class="option-grid">
      <div class="opt-box" style="background:rgba(34,197,94,0.08);border-color:rgba(34,197,94,0.3);">
        <div class="opt-lbl" id="ce-label">-- CE</div>
        <div class="opt-price" style="color:var(--green)" id="ce-price">₹--</div>
        <div class="opt-type" style="color:var(--green)">📈 CALL</div>
        <div id="ce-dir" style="font-size:10px;color:var(--green);margin-top:4px;">--</div>
      </div>
      <div class="opt-box" style="background:rgba(239,68,68,0.08);border-color:rgba(239,68,68,0.3);">
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
      <div class="chk-row"><div class="chk-icon chk-fail" id="c3">✗</div><span>VIX safe (below 20)</span></div>
      <div class="chk-row"><div class="chk-icon chk-fail" id="c4">✗</div><span>Within valid ATM strike range</span></div>
      <div class="chk-row"><div class="chk-icon chk-fail" id="c5">✗</div><span>EMA9 > EMA21 (trend confirm)</span></div>
      <div class="chk-row"><div class="chk-icon chk-fail" id="c6">✗</div><span>RSI between 35–70 (safe zone)</span></div>
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
    </div>
    <div class="mini-jadui" id="bn-jadui" style="background:var(--blue2);color:var(--blue);border-color:rgba(59,130,246,0.3)">⏳ Loading...</div>
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
    </div>
    <div class="mini-jadui" id="fn-jadui" style="background:var(--blue2);color:var(--blue);border-color:rgba(59,130,246,0.3)">⏳ Loading...</div>
  </div>

  <!-- SENSEX MINI -->
  <div class="mini">
    <div class="mini-hdr">
      <div class="mini-name">📈 SENSEX</div>
      <div class="mini-px" id="sx-px">--</div>
    </div>
    <div class="mini-body">
      <div class="ms"><div class="ms-l">📊 RSI</div><div class="ms-v" id="sx-rsi">--</div></div>
      <div class="ms"><div class="ms-l">📏 VIX</div><div class="ms-v" id="sx-vix">--</div></div>
    </div>
    <div class="mini-jadui" id="sx-jadui" style="background:var(--blue2);color:var(--blue);border-color:rgba(59,130,246,0.3)">⏳ Loading...</div>
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
    GOAT PRO v2 — Personal Educational Paper Trading only. SEBI registered financial advice nahi hai. 
    Real money invest mat karo is tool ke basis pe. Trading mein substantial risk hota hai. ORB Strategy.
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
  [['s1',9*60,9*60+15],['s2',9*60+15,9*60+30],['s3',9*60+30,11*60],['s4',14*60,15*60+30]].forEach(([id,from,to])=>{
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
// MARKET SWITCH
// ══════════════════════════════════════════════════
let currentMarket='nifty';
function switchMarket(el,key){
  document.querySelectorAll('.mktab').forEach(t=>t.classList.remove('on'));
  el.classList.add('on');
  currentMarket=key;
  const labels={nifty:'🔵 NIFTY 50 · ORB',banknifty:'🏦 BANKNIFTY · ORB',
    finnifty:'📊 FINNIFTY · ORB',sensex:'📈 SENSEX · ORB'};
  setText('hero-label',labels[key]||labels.nifty);
}

// ══════════════════════════════════════════════════
// MAIN DATA FETCH
// ══════════════════════════════════════════════════
let lastSignal='';
let lastStatus='';

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
  const ms=d.market_status||'CLOSED';
  setText('market-status-pill',ms);
  const dot=document.getElementById('live-dot');
  if(dot) dot.style.background=ms==='OPEN'?'#22c55e':'#ef4444';

  const closedCard=document.getElementById('market-closed-card');
  if(closedCard) closedCard.style.display=ms==='OPEN'?'none':'block';

  if(!d.spot) return;

  // Topbar
  setText('tb-spot',fmtNum(d.spot));
  const vixEl=document.getElementById('tb-vix');
  if(vixEl){
    vixEl.textContent=d.vix||'--';
    vixEl.style.color=d.vix>20?'#ef4444':d.vix>16?'#fbbf24':'#22c55e';
  }

  // Hero
  setText('hero-price',fmtNum(d.spot));
  setText('h-expiry',d.expiry||'--');
  setText('h-atm',d.atm_strike||'--');

  // Strategy badge
  const strat=d.strategy_mode||'WAIT';
  const sb=document.getElementById('strategy-badge');
  if(sb){
    sb.textContent=strat;
    const colors={
      ORB_CAPTURE:'background:rgba(245,158,11,0.2);color:#fbbf24',
      ORB_WAIT:'background:rgba(59,130,246,0.2);color:#60a5fa',
      ORB_BREAKOUT:'background:rgba(34,197,94,0.2);color:#22c55e',
      ORB_ACTIVE:'background:rgba(168,85,247,0.2);color:#c084fc',
      ORB_INVALID:'background:rgba(239,68,68,0.2);color:#ef4444',
      MARKET_CLOSED:'background:rgba(107,114,128,0.2);color:#9ca3af'
    };
    sb.style.cssText=colors[strat]||colors.ORB_WAIT;
    sb.style.borderRadius='20px';sb.style.padding='3px 10px';sb.style.fontSize='10px';sb.style.fontWeight='700';
  }
  setText('market-condition-lbl',(d.market_condition||'--').replace('_',' '));

  // ORB Box
  setText('orb-high',d.orb_set?fmtNum(d.orb_high):'--');
  setText('orb-low',d.orb_set?fmtNum(d.orb_low):'--');
  setText('orb-width',d.orb_set?d.orb_width.toFixed(1)+' pts':'--');
  setText('orb-trades',(d.orb_trades_today||0)+'/2');
  setText('orb-mode',strat);

  const orbStatus = document.getElementById('orb-status-text');
  if(orbStatus){
    if(strat==='ORB_CAPTURE') orbStatus.textContent='📊 Capturing 9:15-9:30 range...';
    else if(strat==='ORB_WAIT') orbStatus.textContent='⏳ Waiting for breakout above/below range...';
    else if(strat==='ORB_BREAKOUT') orbStatus.textContent='🚀 Breakout detected! Confirming...';
    else if(strat==='ORB_ACTIVE') orbStatus.textContent='✅ ORB trade active — monitoring SL/TGT';
    else if(strat==='ORB_INVALID') orbStatus.textContent='❌ Range too wide — no trade today';
    else orbStatus.textContent='Market status: ' + strat;
  }

  // Signal box
  const status=d.status||'BLOCKED';
  const signal=d.signal||'--';
  let sigCls='signal-wait', sigTxt=signal;
  if(status==='TRADE_ACTIVE') sigCls=d.direction==='LONG'?'signal-bull':'signal-bear';
  else if(strat==='ORB_BREAKOUT') sigCls='signal-active';
  else if(signal.includes('TARGET')) sigCls='signal-bull';
  else if(signal.includes('SL HIT')) sigCls='signal-bear';
  else if(strat==='ORB_INVALID') sigCls='signal-bear';
  setHtml('signal-display',`<div class="signal-box ${sigCls}">${sigTxt}</div>`);

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
  const rsi_ok=d.rsi>=35&&d.rsi<=70;
  const allChk=[...chk,ema_ok,rsi_ok];
  const chkLabels=['Index above round level','Velocity positive','Away from round number','VIX safe (below 20)','Valid ATM range','EMA9 > EMA21','RSI 35–70 zone'];
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

  // Active trade
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
        <div class="tr"><span class="tl">Option Premium</span><span class="tv">₹${d.option_entry_premium||0}</span></div>
      </div>`);
    const badge=document.getElementById('live-pnl-badge');
    if(badge){badge.textContent=fmtRs(livePnlRs);badge.style.color=livePnlRs>=0?'var(--green)':'var(--red)';}
  } else {
    setHtml('trade-display','<div style="padding:20px;text-align:center;color:var(--dim);font-size:12px;">No active trade</div>');
    setText('live-pnl-badge','--');
  }

  // Session stats
  const pnl=d.pnl||0;
  const pnlRs=d.session_pnl_rs||0;
  setText('tb-pnl',fmtRs(pnlRs));
  setColor('tb-pnl',pnlRs>=0?'#22c55e':'#ef4444');
  setText('tb-trades',d.total||0);
  setText('stat-trades',d.total||0);
  setText('stat-pnl-pts',(pnl>=0?'+':'')+pnl+' pts');
  setText('stat-pnl-rs',fmtRs(pnlRs));
  setColor('stat-pnl-pts',pnl>=0?'var(--green)':'var(--red)');
  setColor('stat-pnl-rs',pnlRs>=0?'var(--green)':'var(--red)');
  const wr=d.wins&&d.total?Math.round(d.wins/d.total*100):0;
  setText('stat-wr',wr+'%');
  setColor('stat-wr',wr>=60?'var(--green)':wr>=40?'var(--gold)':'var(--red)');

  // BankNifty
  if(d.bn_spot&&d.bn_spot>0){
    setText('bn-px','₹'+fmtNum(d.bn_spot));
    setText('bn-rsi',d.bn_rsi||'--');
    setText('bn-ema',d.bn_ema9?fmtNum(d.bn_ema9):'--');
    setText('bn-atm',d.bn_atm||'--');
    const bnJadui=document.getElementById('bn-jadui');
    if(bnJadui){
      if(d.bn_rsi>60){bnJadui.textContent='🟢 STRONG — CE setup';bnJadui.style.background='rgba(34,197,94,0.15)';bnJadui.style.color='var(--green)';}
      else if(d.bn_rsi<40){bnJadui.textContent='🔴 WEAK — PE setup';bnJadui.style.background='rgba(239,68,68,0.15)';bnJadui.style.color='var(--red)';}
      else{bnJadui.textContent='⏳ NEUTRAL — Wait';bnJadui.style.background='var(--blue2)';bnJadui.style.color='var(--blue)';}
    }
  }

  // FinNifty
  if(d.fn_spot&&d.fn_spot>0){
    setText('fn-px','₹'+fmtNum(d.fn_spot));
    setText('fn-rsi',d.fn_rsi||'--');
    setText('fn-atm',d.fn_atm||'--');
    const fnJadui=document.getElementById('fn-jadui');
    if(fnJadui){
      if(d.fn_rsi>60){fnJadui.textContent='🟢 STRONG — CE setup';fnJadui.style.background='rgba(34,197,94,0.15)';fnJadui.style.color='var(--green)';}
      else if(d.fn_rsi<40){fnJadui.textContent='🔴 WEAK — PE setup';fnJadui.style.background='rgba(239,68,68,0.15)';fnJadui.style.color='var(--red)';}
      else{fnJadui.textContent='⏳ NEUTRAL — Wait';fnJadui.style.background='var(--blue2)';fnJadui.style.color='var(--blue)';}
    }
  }

  // Sensex
  if(d.sx_spot&&d.sx_spot>0){
    setText('sx-px','₹'+fmtNum(d.sx_spot));
    setText('sx-rsi',d.sx_rsi||'--');
    setText('sx-vix',d.vix||'--');
    const sxJadui=document.getElementById('sx-jadui');
    if(sxJadui){
      if(d.sx_rsi>60){sxJadui.textContent='🟢 BULLISH — Nifty support';sxJadui.style.background='rgba(34,197,94,0.15)';sxJadui.style.color='var(--green)';}
      else if(d.sx_rsi<40){sxJadui.textContent='🔴 BEARISH — Caution';sxJadui.style.background='rgba(239,68,68,0.15)';sxJadui.style.color='var(--red)';}
      else{sxJadui.textContent='⏳ NEUTRAL — No bias';sxJadui.style.background='var(--blue2)';sxJadui.style.color='var(--blue)';}
    }
  }
}

function updateTrades(d){
  const closed=d.closed||[];
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
      <div style="color:var(--dim);margin-top:2px;">${t.exit_reason||''} | ${t.setup||''}</div>
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
  const badge=isBull?'🟢 ORB LONG':'🔴 ORB SHORT';
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
# FLASK ROUTES
# ═════════════════════════════════════════════════════════

@app.route("/")
def index():
    from flask import Response
    return Response(TEMPLATE, mimetype='text/html; charset=utf-8')


@app.route("/api/data")
def api_data():
    data = run_pipeline()
    if "error" in data:
        return jsonify({
            "error": data["error"], "spot": 0, "vix": 15, "velocity": 0,
            "status": "BLOCKED", "signal": data["error"], "entry": 0, "target": 0, "sl": 0,
            "chk": [False] * 5, "pass_count": 0, "total": 0, "wins": 0, "losses": 0,
            "pnl": 0, "win_rate": 0, "market_status": "CLOSED", "market_msg": data["error"],
            "atm_strike": 0, "option_type": "CE", "direction": "LONG", "data_source": "—",
            "expiry": "—", "candles": [], "current_candle": {}, "strategy_mode": "ERROR",
            "ce_premium": 0, "pe_premium": 0, "lot_size": 65, "session_pnl_rs": 0,
            "orb_high": 0, "orb_low": 0, "orb_width": 0, "orb_set": false,
            "orb_triggered": false, "orb_trades_today": 0,
        })
    return jsonify(data)


@app.route("/api/trades")
def api_trades():
    closed = db_closed_trades()
    return jsonify({
        "open": db_open_trade(),
        "closed": closed,
        "stats": calc_stats(closed)
    })


@app.route("/ping")
def ping():
    return jsonify({
        "status": "alive",
        "time": time.strftime("%H:%M:%S"),
        "service": "GOAT PRO v2 — ORB Strategy"
    })


@app.route("/paper/exit", methods=["POST"])
def paper_exit():
    d = request.get_json()
    tid = d.get("trade_id")
    ep = float(d.get("exit_price", 0))
    dir_ = d.get("direction", "LONG")
    enp = float(d.get("entry_price", 0))
    pnl = round((ep - enp) if dir_ == "LONG" else (enp - ep), 2)
    pnl_rs = round(pnl * LOT_SIZES.get("NIFTY", 65), 0)
    db_close_trade(tid, ep, d.get("exit_reason", ""), d.get("post_note", ""), pnl, pnl_rs=pnl_rs)
    if pnl > 0:
        ENGINE["trades_won"] += 1
    else:
        ENGINE["trades_lost"] += 1
    ENGINE["trades_total"] += 1
    ENGINE["session_pnl"] = round(ENGINE["session_pnl"] + pnl, 1)
    ENGINE["session_pnl_rs"] = round(ENGINE["session_pnl_rs"] + pnl_rs, 0)
    ENGINE["status"] = "BLOCKED"
    return jsonify({"status": "ok", "pnl": pnl, "pnl_rs": pnl_rs})


@app.route("/paper/clear", methods=["POST"])
def paper_clear():
    db_clear_trades()
    return jsonify({"status": "ok"})


# ═════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
