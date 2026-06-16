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

# ──────────────────────────────────────────────────────────────────────────
# Angel One SmartConnect
# ──────────────────────────────────────────────────────────────────────────
from SmartApi import SmartConnect

# ──────────────────────────────────────────────────────────────────────────
# yFinance fallback
# ──────────────────────────────────────────────────────────────────────────
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

app = Flask(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

TOKENS = {
    "NIFTY":     {"token": "99926000", "exchange": "NSE"},
    "BANKNIFTY": {"token": "99926009", "exchange": "NSE"},
    "FINNIFTY":  {"token": "99926037", "exchange": "NSE"},
    "VIX":       {"token": "99926017", "exchange": "NSE"},
}

LOT_SIZES = {
    "NIFTY":     65,
    "BANKNIFTY": 30,
    "FINNIFTY":  60,
}

OPTION_CACHE = {
    "ce_ltp": 0.0, "pe_ltp": 0.0,
    "ce_token": "", "pe_token": "",
    "last_fetch": 0, "ttl": 10,
}

# Multi-market data storage
MARKET_DATA = {
    "NIFTY":     {"price": 24387.50, "open": 24260, "high": 24430, "low": 24198, "pcr": 1.24, "iv": 14.2, "maxpain": 24300, "chg": 0.52, "dir": "bull"},
    "BANKNIFTY": {"price": 51240,    "open": 51100, "high": 51480, "low": 50980, "pcr": 0.88, "iv": 18.4, "maxpain": 51000, "chg": -0.18, "dir": "bear"},
    "SENSEX":    {"price": 80140,    "open": 79880, "high": 80420, "low": 79720, "pcr": 1.12, "iv": 13.8, "maxpain": 80000, "chg": 0.44, "dir": "bull"},
    "CRUDE":     {"price": 6842,     "open": 6760,  "high": 6880,  "low": 6720,  "pcr": 0,    "iv": 22,   "maxpain": 6800,  "chg": 1.2,  "dir": "bull"},
    "GOLD":      {"price": 71240,    "open": 71380, "high": 71520, "low": 70800, "pcr": 0,    "iv": 12,   "maxpain": 71000, "chg": -0.3, "dir": "bear"},
    "SILVER":    {"price": 84120,    "open": 83800, "high": 84600, "low": 83400, "pcr": 0,    "iv": 16,   "maxpain": 84000, "chg": 0.8,  "dir": "bull"},
    "STOCKS":    {"price": 1842,     "open": 1830,  "high": 1860,  "low": 1820,  "pcr": 1.05, "iv": 15,   "maxpain": 1840,  "chg": 0.6,  "dir": "bull"},
}

INDICATORS = {
    "rsi": 38.4, "vwap": 24362, "ema9": 24371, "ema21": 24344, "supertrend": 24290,
    "rsi_sig": "OVERSOLD", "vwap_sig": "ABOVE", "ema_sig": "BULL", "st_sig": "BUY",
}

# ──────────────────────────────────────────────────────────────────────────
# Database
# ──────────────────────────────────────────────────────────────────────────
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

CANDLE_5MIN = []
_candle_current = {"open": 0, "high": 0, "low": 0, "close": 0, "time": None}

# ═══════════════════════════════════════════════════════════════════════════
# MARKET GUARD
# ═══════════════════════════════════════════════════════════════════════════
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

# ═══════════════════════════════════════════════════════════════════════════
# CANDLE BUILDER
# ═══════════════════════════════════════════════════════════════════════════
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
        _candle_current["low"]  = min(_candle_current["low"],  price)
        _candle_current["close"] = price

# ═══════════════════════════════════════════════════════════════════════════
# DATABASE — Persistent trade logging
# ═══════════════════════════════════════════════════════════════════════════
def db_init():
    conn, db_type = get_db_conn()
    cur = conn.cursor()
    serial = "SERIAL PRIMARY KEY" if db_type == "postgres" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    int_d = "INTEGER DEFAULT 1" if db_type == "postgres" else "INTEGER DEFAULT 1"
    cur.execute(f"""CREATE TABLE IF NOT EXISTS paper_trades (
        id {serial}, direction TEXT, entry_price REAL, exit_price REAL,
        target REAL, sl REAL, qty {int_d}, setup TEXT, source TEXT DEFAULT 'MANUAL',
        note TEXT, post_note TEXT, exit_reason TEXT, emotion TEXT, entry_time TEXT,
        exit_time TEXT, pnl REAL, status TEXT DEFAULT 'OPEN',
        decision_quality TEXT DEFAULT '-', emotion_score INTEGER DEFAULT 0,
        atm_strike INTEGER DEFAULT 0, option_type TEXT DEFAULT 'CE', pnl_rs REAL DEFAULT 0
    )""")
    cur.execute(f"""CREATE TABLE IF NOT EXISTS introspection (
        id {serial}, date TEXT, rule_followed INTEGER, sl_skip TEXT,
        revenge TEXT, discipline INTEGER, tomorrow_rule TEXT, created_at TEXT
    )""")
    cur.execute(f"""CREATE TABLE IF NOT EXISTS decision_quality (
        id {serial}, date TEXT, dq_score INTEGER, breakdown TEXT, created_at TEXT
    )""")
    conn.commit(); cur.close(); conn.close()

def db_open_trade():
    conn, _ = get_db_conn(); cur = conn.cursor()
    cur.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY id DESC LIMIT 1")
    row = cur.fetchone(); cur.close(); conn.close()
    if not row: return None
    cols = ['id','direction','entry_price','exit_price','target','sl','qty','setup','source',
            'note','post_note','exit_reason','emotion','entry_time','exit_time','pnl','status',
            'decision_quality','emotion_score','atm_strike','option_type','pnl_rs']
    return dict(zip(cols, row))

def db_closed_trades(limit=50):
    conn, _ = get_db_conn(); cur = conn.cursor()
    q = "SELECT * FROM paper_trades WHERE status='CLOSED' ORDER BY id DESC LIMIT %s" if USE_POSTGRES else \
        "SELECT * FROM paper_trades WHERE status='CLOSED' ORDER BY id DESC LIMIT ?"
    cur.execute(q, (limit,)); rows = cur.fetchall(); cur.close(); conn.close()
    cols = ['id','direction','entry_price','exit_price','target','sl','qty','setup','source',
            'note','post_note','exit_reason','emotion','entry_time','exit_time','pnl','status',
            'decision_quality','emotion_score','atm_strike','option_type','pnl_rs']
    return [dict(zip(cols, r)) for r in rows]

def db_insert_trade(t):
    conn, db_type = get_db_conn(); cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    sql = f"""INSERT INTO paper_trades
        (direction, entry_price, target, sl, qty, setup, source, note, entry_time, status,
         decision_quality, emotion_score, atm_strike, option_type, pnl_rs)
        VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})"""
    cur.execute(sql, (t['direction'], t['entry_price'], t['target'], t['sl'], t.get('qty',65),
        t['setup'], t.get('source','MANUAL'), t.get('note',''), t['entry_time'], 'OPEN',
        t.get('decision_quality','-'), t.get('emotion_score',0), t.get('atm_strike',0),
        t.get('option_type','CE'), t.get('pnl_rs',0)))
    conn.commit(); cur.close(); conn.close()

def db_close_trade(trade_id, exit_price, exit_reason, post_note, emotion, pnl,
                   decision_quality='-', emotion_score=0, pnl_rs=0):
    conn, db_type = get_db_conn(); cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    sql = f"""UPDATE paper_trades SET exit_price={ph}, exit_reason={ph}, post_note={ph},
        emotion={ph}, exit_time={ph}, pnl={ph}, pnl_rs={ph}, status='CLOSED',
        decision_quality={ph}, emotion_score={ph} WHERE id={ph}"""
    cur.execute(sql, (exit_price, exit_reason, post_note, emotion,
        time.strftime("%H:%M:%S"), pnl, pnl_rs, decision_quality, emotion_score, trade_id))
    conn.commit(); cur.close(); conn.close()

def db_clear_trades():
    conn, _ = get_db_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM paper_trades"); conn.commit(); cur.close(); conn.close()

def db_add_intro(data):
    conn, db_type = get_db_conn(); cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    sql = f"INSERT INTO introspection (date, rule_followed, sl_skip, revenge, discipline, tomorrow_rule, created_at) VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})"
    cur.execute(sql, (data['date'], data['rule_followed'], data['sl_skip'], data['revenge'],
        data['discipline'], data['tomorrow_rule'], time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit(); cur.close(); conn.close()

def db_get_intros(limit=10):
    conn, db_type = get_db_conn(); cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    cur.execute(f"SELECT * FROM introspection ORDER BY id DESC LIMIT {ph}", (limit,))
    rows = cur.fetchall(); cur.close(); conn.close()
    cols = ['id','date','rule_followed','sl_skip','revenge','discipline','tomorrow_rule','created_at']
    return [dict(zip(cols, r)) for r in rows]

def db_add_dq(data):
    conn, db_type = get_db_conn(); cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    cur.execute(f"INSERT INTO decision_quality (date, dq_score, breakdown, created_at) VALUES ({ph},{ph},{ph},{ph})",
        (data['date'], data['dq_score'], data['breakdown'], time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit(); cur.close(); conn.close()

def db_get_dqs(limit=7):
    conn, db_type = get_db_conn(); cur = conn.cursor()
    ph = "%s" if db_type == "postgres" else "?"
    cur.execute(f"SELECT * FROM decision_quality ORDER BY id DESC LIMIT {ph}", (limit,))
    rows = cur.fetchall(); cur.close(); conn.close()
    cols = ['id','date','dq_score','breakdown','created_at']
    return [dict(zip(cols, r)) for r in rows]

def calc_dq_score(rule_followed, sl_skip, revenge, discipline):
    base = (int(rule_followed) + int(discipline)) * 10
    score = base
    penalties = []
    if sl_skip == 'Yes':
        score -= 25; penalties.append("SL Skip: -25")
    if revenge == 'Yes':
        score -= 25; penalties.append("Revenge: -25")
    score = max(0, min(100, score))
    return score, " | ".join([f"Base={base}"] + penalties + [f"Final={score}"])

def calc_stats(trades):
    if not trades:
        return {'total':0,'wins':0,'losses':0,'win_rate':0,'total_pnl':0,'avg_win':0,'avg_loss':0,'best':0,'worst':0,'expectancy':0}
    wins = [t for t in trades if (t['pnl'] or 0) > 0]
    losses = [t for t in trades if (t['pnl'] or 0) <= 0]
    total = len(trades)
    wr = round(len(wins)/total*100) if total else 0
    tot_pnl = round(sum(t['pnl'] or 0 for t in trades), 1)
    avg_win = round(sum(t['pnl'] for t in wins)/len(wins),1) if wins else 0
    avg_loss = round(sum(t['pnl'] for t in losses)/len(losses),1) if losses else 0
    best = round(max(t['pnl'] or 0 for t in trades),1)
    worst = round(min(t['pnl'] or 0 for t in trades),1)
    exp = round((wr/100)*avg_win - ((100-wr)/100)*abs(avg_loss), 2)
    return {'total':total,'wins':len(wins),'losses':len(losses),'win_rate':wr,'total_pnl':tot_pnl,'avg_win':avg_win,'avg_loss':avg_loss,'best':best,'worst':worst,'expectancy':exp}

db_init()

# ═══════════════════════════════════════════════════════════════════════════
# ANGEL ONE SESSION
# ═══════════════════════════════════════════════════════════════════════════
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
        if not s.get("status"): return None, "LOGIN FAILED"
        SESSION_CACHE.update({"obj": obj, "logged_in_at": now})
        return obj, None
    except Exception as e:
        return None, str(e)

def get_both_premiums(index, atm_strike, expiry_str):
    try:
        now = time.time()
        if now - OPTION_CACHE["last_fetch"] < OPTION_CACHE["ttl"]:
            return OPTION_CACHE["ce_ltp"], OPTION_CACHE["pe_ltp"]
        obj, err = get_session()
        if err or not obj: return 0.0, 0.0
        ce_symbol = f"{index}{expiry_str}{atm_strike}CE"
        pe_symbol = f"{index}{expiry_str}{atm_strike}PE"
        ce_ltp, pe_ltp = 0.0, 0.0
        try:
            r = obj.searchScrip("NFO", ce_symbol)
            if r and r.get("data"):
                tok = r["data"][0]["symboltoken"]
                lr = obj.ltpData("NFO", ce_symbol, tok)
                ce_ltp = float(lr["data"]["ltp"]) if lr and lr.get("data") else 0.0
        except: pass
        try:
            r = obj.searchScrip("NFO", pe_symbol)
            if r and r.get("data"):
                tok = r["data"][0]["symboltoken"]
                lr = obj.ltpData("NFO", pe_symbol, tok)
                pe_ltp = float(lr["data"]["ltp"]) if lr and lr.get("data") else 0.0
        except: pass
        OPTION_CACHE.update({"ce_ltp": ce_ltp, "pe_ltp": pe_ltp, "last_fetch": now})
        return ce_ltp, pe_ltp
    except: return 0.0, 0.0

def get_expiry_str_for_angel(weeks_ahead=0):
    today = datetime.date.today()
    days_until_thursday = (3 - today.weekday()) % 7
    if days_until_thursday == 0 and datetime.datetime.now().time() > datetime.time(15, 30):
        days_until_thursday = 7
    expiry_date = today + datetime.timedelta(days=days_until_thursday + (weeks_ahead * 7))
    return expiry_date.strftime("%d%b%Y").upper()

def get_nifty_yfinance():
    if not YFINANCE_AVAILABLE: return None
    try:
        t = yf.Ticker("^NSEI")
        return float(t.fast_info['last_price'])
    except: return None

def get_vix_yfinance():
    if not YFINANCE_AVAILABLE: return None
    try:
        t = yf.Ticker("^INDIAVIX")
        return float(t.fast_info['last_price'])
    except: return None

# ═══════════════════════════════════════════════════════════════════════════
# TELEGRAM ALERTS
# ═══════════════════════════════════════════════════════════════════════════
def _tg(msg):
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat: return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": chat, "text": msg, "parse_mode": "HTML"}, timeout=4)
    except: pass

def tg(msg):
    threading.Thread(target=_tg, args=(msg,), daemon=True).start()

# ═══════════════════════════════════════════════════════════════════════════
# GOAT BRAIN — AI Trade Reasoning
# ═══════════════════════════════════════════════════════════════════════════
def goat_brain_reasons(direction, spot, vix, velocity, checklist_passed, atm_strike, option_type):
    reasons = []
    base100 = (spot // 100) * 100
    if checklist_passed[0]: reasons.append(f"NIFTY {spot} is above round level {base100} — bullish zone")
    else: reasons.append(f"NIFTY {spot} below/at round level {base100} — caution")
    if checklist_passed[1]: reasons.append(f"Momentum is positive (velocity +{velocity:.1f}) — buying pressure")
    else: reasons.append(f"Momentum weak or negative (velocity {velocity:+.1f})")
    if checklist_passed[2]: reasons.append("Price is safely away from round number trap — no whipsaw risk")
    else: reasons.append("Too close to round number — whipsaw risk detected")
    if checklist_passed[3]: reasons.append(f"VIX at {vix:.1f} — fear is low, safe to trade")
    else: reasons.append(f"VIX at {vix:.1f} — elevated volatility, reduce size")
    if checklist_passed[4]: reasons.append("Price within valid ATM strike range — good R:R")
    else: reasons.append("Price near expiry danger zone — reduce exposure")
    reasons.append(f"\u2192 Signal: {direction} {atm_strike} {option_type}")
    return reasons

# ════════════════════════════════════════════════════════════════════════════
# TRADING ENGINE
# ════════════════════════════════════════════════════════════════════════════
ENGINE = {
    "last_update": 0, "tick_ttl": 5, "payload": None, "last_spot": 0.0,
    "velocity": 0.0, "status": "BLOCKED", "direction": "LONG", "option_type": "CE",
    "entry": 0.0, "target": 0.0, "sl": 0.0, "signal": "SYSTEM INITIALIZING...",
    "brain_reasons": [], "atm_strike": 0, "session_pnl": 0.0, "trades_total": 0,
    "trades_won": 0, "trades_lost": 0, "last_signal_sent": "", "data_source": "\u2014",
    "last_candle_signal": 0, "ce_premium": 0.0, "pe_premium": 0.0,
    "option_entry_premium": 0.0, "lot_size": 65, "active_index": "NIFTY",
    "session_pnl_rs": 0.0, "active_market": "NIFTY",
}

def run_pipeline():
    global ENGINE
    now = time.time()
    if ENGINE["payload"] and (now - ENGINE["last_update"] < ENGINE["tick_ttl"]):
        return ENGINE["payload"]
    mstatus, mmsg = market_status()
    expiry_label = get_expiry_label()
    if mstatus != "OPEN":
        ENGINE["status"] = "BLOCKED"; ENGINE["signal"] = mmsg
        payload = {
            "spot": ENGINE.get("last_spot",0), "vix": 15.0, "velocity": 0,
            "status": "BLOCKED", "signal": mmsg, "brain_reasons": [],
            "entry": 0, "target": 0, "sl": 0, "chk": [False]*5, "pass_count": 0,
            "total": ENGINE["trades_total"], "wins": ENGINE["trades_won"],
            "losses": ENGINE["trades_lost"], "pnl": round(ENGINE["session_pnl"],1),
            "win_rate": 0, "market_status": mstatus, "market_msg": mmsg,
            "atm_strike": ENGINE.get("atm_strike",0), "option_type": ENGINE.get("option_type","CE"),
            "direction": ENGINE.get("direction","LONG"), "data_source": ENGINE["data_source"],
            "expiry": expiry_label, "candles": CANDLE_5MIN[-50:], "current_candle": _candle_current,
            "market_data": MARKET_DATA, "indicators": INDICATORS,
        }
        ENGINE.update({"last_update": now, "payload": payload})
        return payload

    spot = None; vix = None; data_source = "Angel One"
    obj, err = get_session()
    if obj:
        try:
            nr = obj.ltpData("NSE", "NIFTY", TOKENS["NIFTY"]["token"])
            vr = obj.ltpData("NSE", "INDIAVIX", TOKENS["VIX"]["token"])
            if nr.get("status") and "data" in nr: spot = float(nr["data"]["ltp"])
            if vr.get("status") and "data" in vr: vix = float(vr["data"]["ltp"])
        except:
            SESSION_CACHE["logged_in_at"] = 0
    if spot is None:
        spot = get_nifty_yfinance()
        data_source = "yfinance" if spot else "\u2014"
    if vix is None: vix = get_vix_yfinance() or 15.0
    if spot is None: return ENGINE["payload"] or {"error": "All data sources failed"}

    ENGINE["data_source"] = data_source
    update_candle(spot)
    if ENGINE["last_spot"] > 0: ENGINE["velocity"] = round(spot - ENGINE["last_spot"], 2)
    ENGINE["last_spot"] = spot
    vel = ENGINE["velocity"]

    atm = get_atm_strike(spot, interval=50); ENGINE["atm_strike"] = atm
    active_index = ENGINE.get("active_index", "NIFTY")
    lot_size = LOT_SIZES.get(active_index, 65); ENGINE["lot_size"] = lot_size

    try:
        expiry_str = get_expiry_str_for_angel(0)
        ce_ltp, pe_ltp = get_both_premiums(active_index, atm, expiry_str)
        ENGINE["ce_premium"] = ce_ltp; ENGINE["pe_premium"] = pe_ltp
    except:
        ce_ltp = ENGINE.get("ce_premium", 0.0); pe_ltp = ENGINE.get("pe_premium", 0.0)

    vix_mult = vix / 15.0
    sl_pts = round(40 * vix_mult, 1); tgt_pts = round(90 * vix_mult, 1)
    base100 = (spot // 100) * 100; base50 = (spot // 50) * 50; dist_round = abs(spot - base50)
    chk = [spot > base100, vel > 0, dist_round > 10, vix < 18.0, (spot - base100) < (0.008 * spot)]
    all_pass = all(chk); pass_count = sum(chk)

    if vel > 0.5 and chk[0]: direction = "LONG"; option_type = "CE"
    elif vel < -0.5 and not chk[0]: direction = "SHORT"; option_type = "PE"
    else: direction = ENGINE.get("direction","LONG"); option_type = ENGINE.get("option_type","CE")
    ENGINE["direction"] = direction; ENGINE["option_type"] = option_type

    # Update multi-market data with live spot
    MARKET_DATA["NIFTY"]["price"] = round(spot, 2)
    MARKET_DATA["NIFTY"]["high"] = max(MARKET_DATA["NIFTY"]["high"], int(spot))
    MARKET_DATA["NIFTY"]["low"] = min(MARKET_DATA["NIFTY"]["low"], int(spot))

    open_t = db_open_trade(); candle_ok = (now - ENGINE["last_candle_signal"]) > 300

    if ENGINE["status"] == "TRADE_ACTIVE":
        if open_t and open_t['source'] == 'AUTO':
            if direction == "LONG": hit_tgt = spot >= ENGINE["target"]; hit_sl = spot <= ENGINE["sl"]
            else: hit_tgt = spot <= ENGINE["target"]; hit_sl = spot >= ENGINE["sl"]
            if hit_tgt:
                pnl_pts = round(abs(ENGINE["target"] - open_t['entry_price']), 1)
                pnl_rs = round(pnl_pts * lot_size, 0)
                db_close_trade(open_t['id'], spot, 'Target Hit', 'Auto-closed', 'Calm', pnl_pts, pnl_rs=pnl_rs)
                ENGINE["trades_won"] += 1; ENGINE["trades_total"] += 1
                ENGINE["session_pnl"] += pnl_pts; ENGINE["session_pnl_rs"] += pnl_rs
                ENGINE["status"] = "BLOCKED"
                ENGINE["signal"] = f"TARGET HIT +{pnl_pts} pts | +\u20b9{int(pnl_rs):,}"
                tg(f"TARGET HIT +{pnl_pts} pts | +\u20b9{int(pnl_rs)} ({lot_size} qty) at {spot}")
            elif hit_sl:
                pnl_pts = round(abs(open_t['entry_price'] - ENGINE["sl"]), 1)
                pnl_rs = round(pnl_pts * lot_size, 0)
                db_close_trade(open_t['id'], spot, 'Stoploss Hit', 'Auto-closed', 'Calm', -pnl_pts, pnl_rs=-pnl_rs)
                ENGINE["trades_lost"] += 1; ENGINE["trades_total"] += 1
                ENGINE["session_pnl"] -= pnl_pts; ENGINE["session_pnl_rs"] -= pnl_rs
                ENGINE["status"] = "BLOCKED"
                ENGINE["signal"] = f"SL HIT -{pnl_pts} pts | -\u20b9{int(pnl_rs):,}"
                tg(f"SL HIT -{pnl_pts} pts | -\u20b9{int(pnl_rs)} ({lot_size} qty) at {spot}")
    elif ENGINE["status"] in ("BLOCKED", "SETUP_READY"):
        if not all_pass:
            ENGINE.update({"status": "BLOCKED", "signal": f"Waiting \u2014 {pass_count}/5 conditions met",
                           "entry": 0.0, "target": 0.0, "sl": 0.0, "brain_reasons": []})
        else:
            if candle_ok:
                if direction == "LONG":
                    entry = round(spot - 5, 2); target = round(entry + tgt_pts, 2); sl = round(entry - sl_pts, 2)
                else:
                    entry = round(spot + 5, 2); target = round(entry - tgt_pts, 2); sl = round(entry + sl_pts, 2)
                ENGINE.update({"status": "SETUP_READY", "entry": entry, "target": target, "sl": sl,
                    "signal": f"SETUP READY \u2014 {direction} | {atm} {option_type}",
                    "brain_reasons": goat_brain_reasons(direction, spot, vix, vel, chk, atm, option_type)})
                ENGINE["last_candle_signal"] = now
            trig_long = direction == "LONG" and spot <= (ENGINE["entry"] + 8) and vel > 0.3
            trig_short = direction == "SHORT" and spot >= (ENGINE["entry"] - 8) and vel < -0.3
            if (trig_long or trig_short) and not open_t and ENGINE["status"] == "SETUP_READY":
                sig_key = f"{direction}_{ENGINE['entry']}"
                if ENGINE["last_signal_sent"] != sig_key:
                    ENGINE["last_signal_sent"] = sig_key; ENGINE["status"] = "TRADE_ACTIVE"
                    ENGINE["signal"] = f"AUTO-ENTERED {direction} at {spot} | {atm} {option_type}"
                    reasons = goat_brain_reasons(direction, spot, vix, vel, chk, atm, option_type)
                    ENGINE["brain_reasons"] = reasons
                    entry_premium = ENGINE["ce_premium"] if option_type == "CE" else ENGINE["pe_premium"]
                    ENGINE["option_entry_premium"] = entry_premium
                    db_insert_trade({"direction": direction, "entry_price": spot, "target": ENGINE["target"],
                        "sl": ENGINE["sl"], "qty": lot_size, "setup": "GOAT Signal", "source": "AUTO",
                        "note": "\n".join(reasons) + f"\nPremium Entry: \u20b9{entry_premium}",
                        "entry_time": time.strftime("%H:%M:%S"), "atm_strike": atm, "option_type": option_type})
                    tg(f"AUTO {direction}\n{spot} | {atm} {option_type}\nPremium: \u20b9{entry_premium}\nTGT:{ENGINE['target']} SL:{ENGINE['sl']}\nLot: {lot_size} qty")

    total = ENGINE["trades_total"]
    win_rate = round(ENGINE["trades_won"] / total * 100) if total else 0

    payload = {
        "spot": round(spot,2), "vix": round(vix,2), "velocity": vel,
        "status": ENGINE["status"], "signal": ENGINE["signal"],
        "brain_reasons": ENGINE["brain_reasons"], "entry": ENGINE["entry"],
        "target": ENGINE["target"], "sl": ENGINE["sl"], "chk": chk,
        "pass_count": pass_count, "total": total, "wins": ENGINE["trades_won"],
        "losses": ENGINE["trades_lost"], "pnl": round(ENGINE["session_pnl"],1),
        "win_rate": win_rate, "market_status": mstatus, "market_msg": mmsg,
        "atm_strike": atm, "option_type": option_type, "direction": direction,
        "data_source": data_source, "expiry": expiry_label,
        "candles": CANDLE_5MIN[-50:], "current_candle": _candle_current,
        "ce_premium": round(ENGINE.get("ce_premium",0.0),2),
        "pe_premium": round(ENGINE.get("pe_premium",0.0),2),
        "option_entry_premium": round(ENGINE.get("option_entry_premium",0.0),2),
        "lot_size": lot_size, "active_index": active_index,
        "session_pnl_rs": round(ENGINE.get("session_pnl_rs",0.0),0),
        "live_pnl_rs": round((ENGINE.get("ce_premium",0.0)-ENGINE.get("option_entry_premium",0.0))*lot_size
            if ENGINE["status"]=="TRADE_ACTIVE" and ENGINE.get("option_entry_premium",0.0)>0 else 0.0, 0),
        "market_data": MARKET_DATA, "indicators": INDICATORS,
    }
    ENGINE.update({"last_update": now, "payload": payload})
    return payload

# ═══════════════════════════════════════════════════════════════════════════
# HTML TEMPLATE — Multi Market Command Center (All-in-One)
# ═══════════════════════════════════════════════════════════════════════════

TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GOAT PRO — Multi Market Command Center</title>
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

/* TOPBAR */
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

/* LEGAL BANNER */
.legal-banner{background:linear-gradient(135deg,#fef3c7,#fde68a);
  border:1.5px solid #f59e0b;border-radius:8px;padding:10px 16px;
  margin-bottom:12px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.legal-banner span{font-size:12px;color:#92400e;line-height:1.5;flex:1;}
.legal-badge{background:#f59e0b;color:#fff;border-radius:4px;
  padding:3px 10px;font-size:10px;font-weight:700;white-space:nowrap;
  font-family:'JetBrains Mono',monospace;}

/* SESSION */
.sess-strip{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap;}
.sess{flex:1;min-width:100px;border:1.5px solid var(--border);border-radius:8px;
  padding:8px 10px;text-align:center;background:var(--panel);
  box-shadow:var(--shadow);position:relative;overflow:hidden;transition:all 0.2s;}
.sess.active{border-color:var(--accent);background:var(--blue2);}
.sess.active::after{content:'';position:absolute;bottom:0;left:0;right:0;height:3px;background:var(--accent);}
.sess-name{font-size:10px;color:var(--dim);letter-spacing:1px;font-weight:600;}
.sess-time{font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;margin:2px 0;}
.sess-heat{font-size:13px;}
.sess-tip{font-size:9px;color:var(--dim);margin-top:2px;}

/* MARKET TABS */
.market-tabs{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap;}
.mktab{padding:7px 14px;border-radius:8px;border:1.5px solid var(--border);
  background:var(--panel);font-family:'JetBrains Mono',monospace;font-size:10px;
  cursor:pointer;transition:all 0.2s;color:var(--dim);font-weight:700;
  box-shadow:var(--shadow);text-align:center;}
.mktab:hover{border-color:var(--accent);color:var(--accent);}
.mktab.on{background:linear-gradient(135deg,#1a56db,#0e3fa8);color:#fff;
  border-color:var(--accent);box-shadow:0 4px 16px rgba(26,86,219,0.3);}
.mktab .chg{font-size:9px;display:block;margin-top:1px;}

/* LAYOUT */
.layout{display:grid;grid-template-columns:1fr 320px;gap:12px;}
@media(max-width:900px){.layout{grid-template-columns:1fr;}}
.left{display:flex;flex-direction:column;gap:12px;}
.right{display:flex;flex-direction:column;gap:10px;}

/* CARD */
.card{background:var(--panel);border:1.5px solid var(--border);
  border-radius:10px;box-shadow:var(--shadow);overflow:hidden;}
.chdr{display:flex;align-items:center;justify-content:space-between;
  padding:9px 14px;border-bottom:1.5px solid var(--border);background:var(--panel2);}
.ctitle{font-family:'Bebas Neue',sans-serif;font-size:14px;letter-spacing:2px;color:var(--accent);}

/* HERO */
.hero{background:linear-gradient(135deg,#1a56db,#0e3fa8);border-radius:10px;
  padding:16px 20px;box-shadow:var(--shadow2);color:#fff;
  display:flex;gap:18px;align-items:center;flex-wrap:wrap;}
.hero-price{font-family:'JetBrains Mono',monospace;font-size:clamp(28px,5vw,44px);font-weight:700;}
.hero-chg{font-size:13px;margin-top:3px;font-family:'JetBrains Mono',monospace;}
.hdiv{width:1px;height:50px;background:rgba(255,255,255,0.2);}
.hstats{display:flex;gap:16px;flex-wrap:wrap;}
.hst{text-align:center;}
.hst-v{font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:700;}
.hst-l{font-size:9px;color:rgba(255,255,255,0.55);letter-spacing:1px;margin-top:2px;}

/* JADUI */
.jadui{border-radius:10px;padding:14px 16px;border:2px solid;position:relative;overflow:hidden;}
.jadui::before{content:'\u2726 JADUI SPOT';position:absolute;top:10px;right:14px;
  font-family:'Bebas Neue',sans-serif;font-size:10px;letter-spacing:3px;opacity:0.2;}
.j-badge{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;
  border-radius:20px;font-family:'JetBrains Mono',monospace;font-size:10px;
  font-weight:700;margin-bottom:8px;}
.j-title{font-family:'Bebas Neue',sans-serif;font-size:clamp(16px,3vw,22px);
  letter-spacing:2px;margin-bottom:6px;}
.j-desc{font-size:13px;line-height:1.6;margin-bottom:10px;}
.j-levels{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px;}
.jlev{padding:5px 12px;border-radius:5px;font-family:'JetBrains Mono',monospace;
  font-size:11px;font-weight:700;border:1.5px solid;}
.jlev.e{background:var(--green2);color:var(--green);border-color:rgba(10,158,92,0.3);}
.jlev.s{background:var(--red2);color:var(--red);border-color:rgba(224,45,60,0.3);}
.jlev.t{background:var(--blue2);color:var(--blue);border-color:rgba(26,86,219,0.3);}
.jlev.r{background:var(--gold2);color:var(--gold);border-color:rgba(180,83,9,0.3);}

/* CHART */
.chart-wrap{padding:12px 14px;}

/* INDICATORS */
.ind-row{display:flex;align-items:center;gap:8px;padding:7px 14px;
  border-bottom:1px solid var(--border);transition:background 0.15s;}
.ind-row:last-child{border-bottom:none;}
.ind-row:hover{background:var(--blue2);}
.iname{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--dim);width:90px;}
.ibar{flex:1;height:4px;background:var(--border);border-radius:2px;overflow:hidden;}
.ibfill{height:100%;border-radius:2px;transition:width 0.8s;}
.ival{font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;min-width:60px;text-align:right;}
.isig{font-size:9px;padding:2px 7px;border-radius:3px;font-weight:700;
  min-width:52px;text-align:center;font-family:'JetBrains Mono',monospace;}
.bull{background:var(--green2);color:var(--green);}
.bear{background:var(--red2);color:var(--red);}
.neu{background:var(--blue2);color:var(--blue);}

/* TF */
.tf-row{display:flex;gap:4px;}
.tft{padding:3px 9px;border-radius:4px;border:1px solid var(--border);
  font-family:'JetBrains Mono',monospace;font-size:10px;cursor:pointer;
  color:var(--dim);background:var(--panel2);transition:all 0.15s;}
.tft.on{background:var(--accent);color:#fff;border-color:var(--accent);}

/* SMART MONEY */
.sm-row{display:flex;align-items:center;gap:10px;padding:8px 14px;
  border-bottom:1px solid var(--border);}
.sm-row:last-child{border-bottom:none;}
.sm-icon{font-size:20px;width:28px;text-align:center;}
.sm-info{flex:1;}
.sm-name{font-size:13px;font-weight:700;}
.sm-desc{font-size:11px;color:var(--dim);margin-top:1px;line-height:1.4;}
.sm-sig{font-size:10px;padding:2px 8px;border-radius:3px;font-weight:700;
  font-family:'JetBrains Mono',monospace;}

/* THEORY */
.theory-row{padding:10px 14px;border-bottom:1px solid var(--border);}
.theory-row:last-child{border-bottom:none;}
.theory-title{font-size:13px;font-weight:700;color:var(--accent);margin-bottom:4px;}
.theory-body{font-size:12px;color:var(--dim);line-height:1.5;}
.theory-tag{display:inline-block;padding:1px 7px;border-radius:3px;
  font-size:9px;font-weight:700;margin-right:4px;margin-bottom:4px;
  font-family:'JetBrains Mono',monospace;}

/* MINI MARKET */
.mini{border:1.5px solid var(--border);border-radius:10px;
  background:var(--panel);box-shadow:var(--shadow);overflow:hidden;}
.mini-hdr{display:flex;justify-content:space-between;align-items:center;
  padding:8px 12px;border-bottom:1px solid var(--border);background:var(--panel2);}
.mini-name{font-family:'Bebas Neue',sans-serif;font-size:13px;letter-spacing:2px;color:var(--accent);}
.mini-px{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;}
.mini-body{padding:10px 12px;display:grid;grid-template-columns:1fr 1fr;gap:6px;}
.ms{background:var(--panel2);border-radius:6px;padding:6px 8px;border:1px solid var(--border);}
.ms-l{font-size:9px;color:var(--dim);letter-spacing:1px;text-transform:uppercase;}
.ms-v{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;margin-top:2px;}
.mini-jadui{margin:0 12px 10px;padding:8px 10px;border-radius:6px;
  font-size:11px;font-weight:700;text-align:center;border:1.5px solid;}

/* GREEK */
.greek-row{display:flex;justify-content:space-between;align-items:center;
  padding:7px 14px;border-bottom:1px solid var(--border);}
.greek-row:last-child{border-bottom:none;}
.greek-l{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--dim);}
.greek-v{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;}

/* OI */
.oi-p{padding:12px 14px;}
.oi-row{display:flex;justify-content:space-between;align-items:center;padding:4px 0;}
.oi-l{font-size:11px;color:var(--dim);}
.oi-v{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;}
.oi-track{height:5px;background:var(--border);border-radius:3px;overflow:hidden;margin-bottom:6px;}
.oi-fill{height:100%;border-radius:3px;transition:width 0.8s;}

/* PATTERN */
.pat-row{display:flex;align-items:center;gap:10px;padding:8px 12px;
  border-bottom:1px solid var(--border);}
.pat-row:last-child{border-bottom:none;}
.pat-icon{font-size:20px;width:26px;text-align:center;}
.pat-name{font-size:13px;font-weight:700;}
.pat-desc{font-size:10px;color:var(--dim);margin-top:1px;}
.pat-conf{font-family:'JetBrains Mono',monospace;font-size:11px;
  padding:2px 8px;border-radius:3px;font-weight:700;}

/* NEWS */
.news-item{display:flex;gap:8px;padding:8px 12px;border-bottom:1px solid var(--border);}
.news-item:last-child{border-bottom:none;}
.nimp{font-size:9px;padding:2px 6px;border-radius:3px;font-weight:700;
  font-family:'JetBrains Mono',monospace;white-space:nowrap;height:fit-content;margin-top:2px;}
.ntxt{font-size:12px;line-height:1.4;}
.ntime{font-size:10px;color:var(--dim);margin-top:2px;}

/* ALERT */
.alert-item{display:flex;gap:10px;align-items:center;padding:8px 14px;
  border-bottom:1px solid var(--border);animation:flashIn 0.5s;}
@keyframes flashIn{from{background:rgba(26,86,219,0.08)}to{background:transparent}}
.alert-item:last-child{border-bottom:none;}
.alert-time{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--dim);min-width:48px;}
.alert-badge{font-size:9px;padding:2px 7px;border-radius:3px;font-weight:700;
  font-family:'JetBrains Mono',monospace;white-space:nowrap;}
.alert-msg{font-size:12px;flex:1;line-height:1.4;}
.alert-px{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--accent);font-weight:700;}

/* CAPITAL */
.cap-bar{display:flex;gap:16px;align-items:center;flex-wrap:wrap;
  padding:12px 16px;background:var(--panel);border:1.5px solid var(--border);
  border-radius:10px;box-shadow:var(--shadow);margin-bottom:12px;}
.cap-item{text-align:center;}
.cap-val{font-family:'JetBrains Mono',monospace;font-size:15px;font-weight:700;}
.cap-l{font-size:9px;color:var(--dim);letter-spacing:1px;text-transform:uppercase;margin-top:2px;}
.cap-div{width:1px;height:34px;background:var(--border);}
.risk-wrap{flex:1;min-width:140px;}
.risk-l{font-size:11px;color:var(--dim);margin-bottom:4px;}
.risk-track{height:7px;background:var(--border);border-radius:4px;overflow:hidden;}
.risk-fill{height:100%;border-radius:4px;
  background:linear-gradient(90deg,var(--green),#f59e0b,var(--red));transition:width 0.8s;}

/* TARGET 1000% */
.target-card{background:linear-gradient(135deg,#0e3fa8,#1a56db,#7c3aed);
  border-radius:10px;padding:14px 16px;color:#fff;box-shadow:var(--shadow2);}
.target-title{font-family:'Bebas Neue',sans-serif;font-size:18px;letter-spacing:3px;margin-bottom:10px;}
.target-steps{display:flex;flex-direction:column;gap:6px;}
.target-step{display:flex;align-items:center;gap:8px;font-size:12px;
  background:rgba(255,255,255,0.1);border-radius:6px;padding:6px 10px;}
.target-num{font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;
  background:rgba(255,255,255,0.2);border-radius:4px;padding:2px 6px;white-space:nowrap;}

/* SHARE MODAL */
.share-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.5);
  z-index:1000;align-items:center;justify-content:center;}
.share-overlay.open{display:flex;}
.share-modal{background:var(--panel);border-radius:14px;padding:24px;
  max-width:420px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,0.3);}
.share-title{font-family:'Bebas Neue',sans-serif;font-size:22px;letter-spacing:3px;
  color:var(--accent);margin-bottom:4px;}
.share-sub{font-size:12px;color:var(--dim);margin-bottom:16px;line-height:1.5;}
.share-btns{display:flex;flex-direction:column;gap:8px;}
.share-btn{display:flex;align-items:center;gap:12px;padding:12px 16px;
  border-radius:8px;border:1.5px solid var(--border);cursor:pointer;
  transition:all 0.2s;font-size:13px;font-weight:600;background:var(--panel2);}
.share-btn:hover{border-color:var(--accent);background:var(--blue2);}
.share-btn .sb-icon{font-size:22px;width:32px;text-align:center;}
.share-btn .sb-info{flex:1;}
.share-btn .sb-name{font-weight:700;color:var(--text);}
.share-btn .sb-desc{font-size:10px;color:var(--dim);margin-top:1px;}
.share-disclaimer{background:var(--gold2);border:1px solid #f59e0b;border-radius:6px;
  padding:10px 12px;font-size:11px;color:#92400e;margin-top:14px;line-height:1.5;}
.share-close{display:block;text-align:center;margin-top:12px;
  padding:8px;border-radius:6px;background:var(--border);cursor:pointer;
  font-size:12px;font-weight:700;color:var(--dim);transition:all 0.15s;}
.share-close:hover{background:var(--red2);color:var(--red);}

/* SHARE FAB */
.share-fab{position:fixed;bottom:24px;right:24px;
  background:linear-gradient(135deg,#25d366,#128c7e);
  border:none;border-radius:50px;padding:12px 20px;
  color:#fff;font-family:'Bebas Neue',sans-serif;font-size:16px;letter-spacing:2px;
  cursor:pointer;box-shadow:0 6px 24px rgba(37,211,102,0.4);
  display:flex;align-items:center;gap:8px;z-index:100;transition:all 0.2s;}
.share-fab:hover{transform:translateY(-2px);box-shadow:0 10px 30px rgba(37,211,102,0.5);}

/* REFRESH */
.refresh-btn{background:linear-gradient(135deg,var(--accent),var(--accent2));
  border:none;border-radius:6px;padding:7px 16px;
  font-family:'Bebas Neue',sans-serif;font-size:14px;letter-spacing:2px;
  color:#fff;cursor:pointer;transition:all 0.2s;box-shadow:var(--shadow);}
.refresh-btn:hover{transform:translateY(-1px);box-shadow:var(--shadow2);}

/* FOOTER */
.footer{margin-top:14px;padding:12px 16px;
  background:linear-gradient(135deg,#fef3c7,#fde68a);
  border:1.5px solid #f59e0b;border-radius:10px;
  display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;}
.footer-text{font-size:11px;color:#92400e;line-height:1.6;flex:1;}
.footer-badge{background:#f59e0b;color:#fff;border-radius:4px;
  padding:4px 12px;font-size:10px;font-weight:700;font-family:'JetBrains Mono',monospace;
  white-space:nowrap;}

/* 5-POINT CHECKLIST */
.chk-row{display:flex;align-items:center;gap:10px;padding:8px 14px;
  border-bottom:1px solid var(--border);transition:background 0.15s;}
.chk-row:last-child{border-bottom:none;}
.chk-row:hover{background:var(--blue2);}
.chk-pass{font-size:11px;padding:2px 8px;border-radius:3px;font-weight:700;
  font-family:'JetBrains Mono',monospace;}

/* SIGNAL BOX */
.sigbox{border-radius:10px;padding:16px 20px;margin-bottom:12px;
  box-shadow:var(--shadow);border:2px solid;transition:all 0.3s;}
.sigbox.trade{border-color:rgba(10,158,92,0.4);background:var(--green2);}
.sigbox.setup{border-color:rgba(180,83,9,0.4);background:var(--gold2);}
.sigbox.block{border-color:var(--border);background:var(--panel);}
.sig-title{font-family:'Bebas Neue',sans-serif;font-size:16px;letter-spacing:2px;margin-bottom:6px;}
.sig-detail{font-family:'JetBrains Mono',monospace;font-size:12px;}

/* TRADE CARD */
.tcard{background:var(--panel);border:1.5px solid var(--border);
  border-radius:10px;padding:16px;box-shadow:var(--shadow);}
.tgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:10px 0;}
.tlabel{font-size:9px;color:var(--dim);letter-spacing:1px;text-transform:uppercase;margin-bottom:2px;}
.tval{font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:700;}

/* JOURNAL */
.jentry{background:var(--panel2);border:1px solid var(--border);
  border-radius:8px;padding:12px;margin-bottom:8px;}
.jwin{border-left:3px solid var(--green);}
.jloss{border-left:3px solid var(--red);}
.jhead{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;}
.jdir{font-size:11px;font-weight:700;padding:2px 8px;border-radius:3px;}
.jpnl{font-family:'JetBrains Mono',monospace;font-size:15px;font-weight:700;}

/* TABS for strategy/journal */
.tab-row{display:flex;gap:4px;margin-bottom:10px;}
.tab-btn{padding:6px 14px;border-radius:6px;border:1px solid var(--border);
  background:var(--panel2);font-family:'JetBrains Mono',monospace;font-size:10px;
  cursor:pointer;color:var(--dim);font-weight:700;transition:all 0.15s;}
.tab-btn.active{background:var(--accent);color:#fff;border-color:var(--accent);}
.tab-panel{display:none;}
.tab-panel.active{display:block;}

/* STRATEGY SELECTOR */
.strat-sel{display:flex;gap:4px;margin-bottom:12px;flex-wrap:wrap;}
.sbtn{padding:6px 14px;border-radius:6px;border:1.5px solid var(--border);
  background:var(--panel);font-family:'JetBrains Mono',monospace;font-size:10px;
  cursor:pointer;color:var(--dim);font-weight:700;transition:all 0.15s;}
.sbtn:hover{border-color:var(--accent);color:var(--accent);}
.sbtn.on{background:linear-gradient(135deg,#1a56db,#0e3fa8);color:#fff;border-color:var(--accent);}
</style>
</head>
<body>
<div class="wrap">

<!-- TOPBAR -->
<div class="topbar">
  <div>
    <h1>GOAT PRO</h1>
    <small>Multi Market Command Center &middot; Personal Use Only</small>
  </div>
  <div class="tb-right">
    <div class="tb-stat">
      <div class="tb-val" id="tb-time">--:--:--</div>
      <div class="tb-label">IST Time</div>
    </div>
    <div class="tb-div"></div>
    <div class="tb-stat">
      <div class="tb-val" id="tb-trades">0</div>
      <div class="tb-label">Trades</div>
    </div>
    <div class="tb-div"></div>
    <div class="tb-stat">
      <div class="tb-val" id="tb-pnl" style="color:#4ade80">+\u20b90</div>
      <div class="tb-label">P&L</div>
    </div>
    <div class="tb-div"></div>
    <div class="tb-stat">
      <div class="tb-val" id="tb-wr" style="color:#fbbf24">0%</div>
      <div class="tb-label">Win Rate</div>
    </div>
    <div class="live-pill"><div class="ldot"></div>LIVE</div>
  </div>
</div>

<!-- LEGAL BANNER -->
<div class="legal-banner">
  <div class="legal-badge">LEGAL NOTICE</div>
  <span>This is an <strong>Educational Tool</strong> only — not Financial/Investment Advice. Trading involves risk. Trade at your own responsibility. Consult a SEBI registered advisor. This tool is not responsible for any losses.</span>
</div>

<!-- SESSION STRIP -->
<div class="sess-strip" id="sess-strip">
  <div class="sess" id="s1">
    <div class="sess-name">OPENING</div>
    <div class="sess-time">9:15–11:00</div>
    <div class="sess-heat">\u26a1\u26a1\u26a1</div>
    <div class="sess-tip">High Volatility</div>
  </div>
  <div class="sess" id="s2">
    <div class="sess-name">MID</div>
    <div class="sess-time">11:00–1:00</div>
    <div class="sess-heat">\u3030\u3030</div>
    <div class="sess-tip">Choppy — Careful</div>
  </div>
  <div class="sess" id="s3">
    <div class="sess-name">AFTERNOON</div>
    <div class="sess-time">1:00–2:30</div>
    <div class="sess-heat">\u26a1\u26a1</div>
    <div class="sess-tip">Momentum Returns</div>
  </div>
  <div class="sess" id="s4">
    <div class="sess-name">POWER HOUR</div>
    <div class="sess-time">2:30–3:30</div>
    <div class="sess-heat">\ud83d\ude80\ud83d\ude80\ud83d\ude80</div>
    <div class="sess-tip">Explosive Moves</div>
  </div>
</div>

<!-- CAPITAL BAR -->
<div class="cap-bar">
  <div class="cap-item">
    <div class="cap-val" style="color:var(--accent)">\u20b918,500</div>
    <div class="cap-l">Capital</div>
  </div>
  <div class="cap-div"></div>
  <div class="cap-item">
    <div class="cap-val" style="color:var(--green)" id="cap-pnl">+\u20b90</div>
    <div class="cap-l">Today P&L</div>
  </div>
  <div class="cap-div"></div>
  <div class="cap-item">
    <div class="cap-val" style="color:var(--red)">\u20b9185</div>
    <div class="cap-l">Max Risk/Trade</div>
  </div>
  <div class="cap-div"></div>
  <div class="cap-item">
    <div class="cap-val" style="color:var(--red)">\u20b9555</div>
    <div class="cap-l">Daily Loss Limit</div>
  </div>
  <div class="cap-div"></div>
  <div class="risk-wrap">
    <div class="risk-l">Daily Risk Used — <strong id="risk-pct">0%</strong></div>
    <div class="risk-track"><div class="risk-fill" id="risk-fill" style="width:0%"></div></div>
  </div>
</div>

<!-- MARKET TABS -->
<div class="market-tabs">
  <div class="mktab on" onclick="switchMarket(this,'NIFTY')">NIFTY<span class="chg" id="nifty-chg-tab" style="color:#0a9e5c">+0.52%</span></div>
  <div class="mktab" onclick="switchMarket(this,'BANKNIFTY')">BANKNIFTY<span class="chg" id="bn-chg-tab" style="color:#e02d3c">-0.18%</span></div>
  <div class="mktab" onclick="switchMarket(this,'SENSEX')">SENSEX<span class="chg" id="sx-chg-tab" style="color:#0a9e5c">+0.44%</span></div>
  <div class="mktab" onclick="switchMarket(this,'CRUDE')">CRUDE OIL<span class="chg" id="cr-chg-tab" style="color:#0a9e5c">+1.2%</span></div>
  <div class="mktab" onclick="switchMarket(this,'GOLD')">MCX GOLD<span class="chg" id="gd-chg-tab" style="color:#e02d3c">-0.3%</span></div>
  <div class="mktab" onclick="switchMarket(this,'SILVER')">MCX SILVER<span class="chg" id="sv-chg-tab" style="color:#0a9e5c">+0.8%</span></div>
  <div class="mktab" onclick="switchMarket(this,'STOCKS')">STOCKS<span class="chg" style="color:var(--dim)">HDFC SBI+</span></div>
</div>

<!-- MAIN LAYOUT -->
<div class="layout">
<div class="left">

  <!-- HERO PRICE -->
  <div class="hero" id="hero-section">
    <div>
      <div style="font-size:11px;opacity:0.7;letter-spacing:2px;margin-bottom:4px" id="hero-label">NIFTY 50 &middot; INDEX</div>
      <div class="hero-price" id="hero-price">24,387.50</div>
      <div class="hero-chg" id="hero-chg">\u25b2 +127.30 &nbsp;(+0.52%) &nbsp;BULLISH</div>
    </div>
    <div class="hdiv"></div>
    <div class="hstats">
      <div class="hst"><div class="hst-v" id="h-open">24,260</div><div class="hst-l">OPEN</div></div>
      <div class="hst"><div class="hst-v" id="h-high">24,430</div><div class="hst-l">HIGH</div></div>
      <div class="hst"><div class="hst-v" id="h-low">24,198</div><div class="hst-l">LOW</div></div>
      <div class="hst"><div class="hst-v" id="h-pcr" style="color:#4ade80">1.24</div><div class="hst-l">PCR</div></div>
      <div class="hst"><div class="hst-v" id="h-iv">14.2%</div><div class="hst-l">IV</div></div>
      <div class="hst"><div class="hst-v" id="h-maxpain">24,300</div><div class="hst-l">MAX PAIN</div></div>
    </div>
  </div>

  <!-- SIGNAL BOX -->
  <div class="sigbox block" id="signal-box">
    <div class="sig-title" id="sig-title" style="color:var(--dim)">SYSTEM INITIALIZING...</div>
    <div class="sig-detail" id="sig-detail" style="color:var(--dim)">Waiting for market data...</div>
  </div>

  <!-- JADUI SPOT -->
  <div class="jadui" id="jadui-card" style="border-color:rgba(10,158,92,0.4);background:var(--green2);">
    <div class="j-badge" id="j-badge" style="background:var(--green2);color:var(--green);border:1px solid rgba(10,158,92,0.3)">CONFIRMED</div>
    <div class="j-title" id="j-title" style="color:var(--green)">BULLISH JADUI SPOT DETECTED!</div>
    <div class="j-desc" id="j-desc">
      <strong>Hammer Candle</strong> at VWAP Support + RSI 38 (Oversold Bounce) + OI Long Buildup detected.<br>
      <strong>Smart Money</strong> accumulation zone — Institutions buying visible.<br>
      <strong>Confluence Score: 8/10</strong> — High probability reversal. Small SL, big target!
    </div>
    <div class="j-levels">
      <div class="jlev e">ENTRY: <span id="j-entry">24,380</span></div>
      <div class="jlev s">SL: <span id="j-sl">24,350</span> (30 pts)</div>
      <div class="jlev t">TGT1: <span id="j-tgt1">24,440</span> &middot; TGT2: <span id="j-tgt2">24,490</span></div>
      <div class="jlev r">R:R = 1:2</div>
    </div>
    <div style="font-size:11px;color:var(--green);font-weight:600">
      Action: ATM CE buy &middot; Lot size: 1 lot only &middot; Keep strict SL!
    </div>
  </div>

  <!-- CHART + INDICATORS -->
  <div class="card">
    <div class="chdr">
      <div class="ctitle">LIVE CANDLE CHART</div>
      <div class="tf-row">
        <div class="tft on" onclick="setTf(this,'1m')">1M</div>
        <div class="tft" onclick="setTf(this,'5m')">5M</div>
        <div class="tft" onclick="setTf(this,'15m')">15M</div>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="main-canvas" height="180"></canvas></div>
  </div>

  <!-- 5-POINT CHECKLIST -->
  <div class="card">
    <div class="chdr">
      <div class="ctitle">5-POINT SIGNAL CHECKLIST</div>
      <div class="tf-row">
        <span style="font-size:11px;color:var(--dim);font-family:'JetBrains Mono',monospace" id="chk-score">0/5</span>
      </div>
    </div>
    <div id="checklist">
      <div class="chk-row">
        <span class="chk-pass bull" id="chk-0">WAIT</span>
        <span style="font-size:12px;">NIFTY above round level</span>
      </div>
      <div class="chk-row">
        <span class="chk-pass bull" id="chk-1">WAIT</span>
        <span style="font-size:12px;">Velocity positive (momentum)</span>
      </div>
      <div class="chk-row">
        <span class="chk-pass bull" id="chk-2">WAIT</span>
        <span style="font-size:12px;">Distance from round number OK</span>
      </div>
      <div class="chk-row">
        <span class="chk-pass bull" id="chk-3">WAIT</span>
        <span style="font-size:12px;">VIX safe (below 18)</span>
      </div>
      <div class="chk-row">
        <span class="chk-pass bull" id="chk-4">WAIT</span>
        <span style="font-size:12px;">Within valid strike range</span>
      </div>
    </div>
  </div>

  <!-- INDICATORS -->
  <div class="card">
    <div class="chdr">
      <div class="ctitle">INDICATOR DASHBOARD</div>
      <div style="font-size:10px;color:var(--dim);font-family:'JetBrains Mono',monospace" id="ind-tf-label">1M LIVE</div>
    </div>
    <div class="ind-row">
      <div class="iname">RSI(14)</div>
      <div class="ibar"><div class="ibfill" id="rsi-bar" style="background:var(--green);width:38%"></div></div>
      <div class="ival" id="rsi-val" style="color:var(--green)">38.4</div>
      <div class="isig bull" id="rsi-sig">OVERSOLD</div>
    </div>
    <div class="ind-row">
      <div class="iname">VWAP</div>
      <div class="ibar"><div class="ibfill" id="vwap-bar" style="background:var(--blue);width:55%"></div></div>
      <div class="ival" id="vwap-val" style="color:var(--blue)">24,362</div>
      <div class="isig bull" id="vwap-sig">ABOVE</div>
    </div>
    <div class="ind-row">
      <div class="iname">EMA 9</div>
      <div class="ibar"><div class="ibfill" id="ema9-bar" style="background:var(--green);width:65%"></div></div>
      <div class="ival" id="ema9-val" style="color:var(--green)">24,371</div>
      <div class="isig bull" id="ema9-sig">BULL</div>
    </div>
    <div class="ind-row">
      <div class="iname">EMA 21</div>
      <div class="ibar"><div class="ibfill" id="ema21-bar" style="background:var(--green);width:58%"></div></div>
      <div class="ival" id="ema21-val" style="color:var(--green)">24,344</div>
      <div class="isig bull" id="ema21-sig">BULL</div>
    </div>
    <div class="ind-row">
      <div class="iname">SUPERTREND</div>
      <div class="ibar"><div class="ibfill" id="st-bar" style="background:var(--green);width:75%"></div></div>
      <div class="ival" id="st-val" style="color:var(--green)">24,290</div>
      <div class="isig bull" id="st-sig">BUY</div>
    </div>
  </div>

  <!-- SMART MONEY -->
  <div class="card">
    <div class="chdr"><div class="ctitle">SMART MONEY MOVES</div></div>
    <div class="sm-row">
      <div class="sm-icon">\ud83d\udc33</div>
      <div class="sm-info">
        <div class="sm-name">FII Net Activity</div>
        <div class="sm-desc">Foreign Institutional Investors are net buyers today — \u20b92,840 Cr in cash market. Bullish signal!</div>
      </div>
      <div class="sm-sig bull" id="fii-sig">BUYING</div>
    </div>
    <div class="sm-row">
      <div class="sm-icon">\ud83c\udfdb\ufe0f</div>
      <div class="sm-info">
        <div class="sm-name">DII Net Activity</div>
        <div class="sm-desc">Domestic Institutions also supporting — \u20b91,200 Cr buying. Market has strong base.</div>
      </div>
      <div class="sm-sig bull" id="dii-sig">SUPPORT</div>
    </div>
    <div class="sm-row">
      <div class="sm-icon">\ud83d\udce6</div>
      <div class="sm-info">
        <div class="sm-name">OI Long Buildup</div>
        <div class="sm-desc">24,400 CE showing fresh long positions building — bullish momentum expected.</div>
      </div>
      <div class="sm-sig bull" id="oi-sig">LONG</div>
    </div>
    <div class="sm-row">
      <div class="sm-icon">\ud83d\udcb0</div>
      <div class="sm-info">
        <div class="sm-name">Put Writing (Smart Move)</div>
        <div class="sm-desc">24,300 PE showing heavy put writing — institutions won't let it go below 24,300 today.</div>
      </div>
      <div class="sm-sig bull" id="pw-sig">FLOOR</div>
    </div>
    <div class="sm-row">
      <div class="sm-icon">\u26a1</div>
      <div class="sm-info">
        <div class="sm-name">Gamma Exposure (GEX)</div>
        <div class="sm-desc">Positive GEX zone — market dealers will buy dips. Low volatility expected in range.</div>
      </div>
      <div class="sm-sig neu" id="gex-sig">POSITIVE</div>
    </div>
  </div>

  <!-- THEORY & SCIENCE -->
  <div class="card">
    <div class="chdr"><div class="ctitle">THEORY & SCIENCE — PRO CONCEPTS</div></div>
    <div class="theory-row">
      <div class="theory-title">Wyckoff Theory — Accumulation Phase</div>
      <div class="theory-body">
        <span class="theory-tag" style="background:var(--blue2);color:var(--blue)">WYCKOFF</span>
        Market is currently in <strong>Spring phase</strong> — last low tested, smart money accumulating. This is where buyers step in. Classic Wyckoff Spring = Best entry point!
      </div>
    </div>
    <div class="theory-row">
      <div class="theory-title">ICT Concepts — Order Blocks & FVG</div>
      <div class="theory-body">
        <span class="theory-tag" style="background:var(--purple2);color:var(--purple)">ICT</span>
        24,350–24,380 is a <strong>Bullish Order Block</strong> — institutions placed previous orders here. FVG (Fair Value Gap) at 24,360–24,375 — market will return here to fill.
      </div>
    </div>
    <div class="theory-row">
      <div class="theory-title">Elliott Wave — Wave 3 Starting</div>
      <div class="theory-body">
        <span class="theory-tag" style="background:var(--green2);color:var(--green)">ELLIOTT</span>
        Wave 2 correction complete — <strong>Wave 3 (strongest wave)</strong> starting. Wave 3 is usually 1.618x of Wave 1. Target: 24,550–24,620 zone.
      </div>
    </div>
    <div class="theory-row">
      <div class="theory-title">Market Structure — BOS Confirmed</div>
      <div class="theory-body">
        <span class="theory-tag" style="background:var(--gold2);color:var(--gold)">STRUCTURE</span>
        <strong>Break of Structure (BOS)</strong> upward confirmed — higher highs and higher lows forming. Trend is bullish. Buy dips, avoid peaks.
      </div>
    </div>
    <div class="theory-row">
      <div class="theory-title">Options Gamma Science</div>
      <div class="theory-body">
        <span class="theory-tag" style="background:var(--red2);color:var(--red)">GAMMA</span>
        Near expiry <strong>Gamma</strong> is very high — small price movement causes fast option premium moves. Buy ATM options near expiry for 3–5x returns. <strong>Time = Enemy, Direction = Friend!</strong>
      </div>
    </div>
  </div>

  <!-- 1000% TARGET PLAN -->
  <div class="target-card">
    <div class="target-title">1000% CAPITAL GROWTH PLAN</div>
    <div class="target-steps">
      <div class="target-step">
        <div class="target-num">MONTH 1–2</div>
        <div>\u20b910,000 → \u20b925,000 — Daily 1% compounding &middot; Only 1–2 trades &middot; No overtrading</div>
      </div>
      <div class="target-step">
        <div class="target-num">MONTH 3–4</div>
        <div>\u20b925,000 → \u20b960,000 — Increase position size &middot; Add expiry day scalping</div>
      </div>
      <div class="target-step">
        <div class="target-num">MONTH 5–6</div>
        <div>\u20b960,000 → \u20b91,50,000 — Multi-market trading &middot; Follow Smart Money</div>
      </div>
      <div class="target-step">
        <div class="target-num">MONTH 7–9</div>
        <div>\u20b91,50,000 → \u20b94,00,000 — 2–3 lots &middot; Add Commodity &middot; Maintain streak</div>
      </div>
      <div class="target-step">
        <div class="target-num">MONTH 10–12</div>
        <div>\u20b94,00,000 → \u20b910,00,000+ — Full GOAT mode &middot; 1000% achieved!</div>
      </div>
      <div class="target-step" style="background:rgba(255,215,0,0.15);border:1px solid rgba(255,215,0,0.3)">
        <div class="target-num" style="background:rgba(255,215,0,0.3)">KEY RULE</div>
        <div>Never risk more than 1% &middot; If loss limit hit, close screen &middot; Follow the process!</div>
      </div>
    </div>
  </div>

  <!-- NEWS IMPACT -->
  <div class="card">
    <div class="chdr"><div class="ctitle">NEWS & MARKET IMPACT</div></div>
    <div id="news-list">
      <div class="news-item">
        <div class="nimp" style="background:var(--red2);color:var(--red);border:1px solid rgba(224,45,60,0.3)">HIGH</div>
        <div>
          <div class="ntxt"><strong>RBI Governor Speech</strong> — 2:30 PM today &middot; Market may turn volatile</div>
          <div class="ntime">Reduce positions before 2:20 PM &middot; Avoid new entries</div>
        </div>
      </div>
      <div class="news-item">
        <div class="nimp" style="background:var(--gold2);color:var(--gold);border:1px solid rgba(180,83,9,0.3)">MED</div>
        <div>
          <div class="ntxt"><strong>FII Net Buyers</strong> — \u20b92,840 Cr in cash market today</div>
          <div class="ntime">Bullish sentiment &middot; Market support is strong</div>
        </div>
      </div>
      <div class="news-item">
        <div class="nimp" style="background:var(--green2);color:var(--green);border:1px solid rgba(10,158,92,0.3)">LOW</div>
        <div>
          <div class="ntxt"><strong>US Markets</strong> — Dow +0.8% overnight close &middot; Nasdaq green</div>
          <div class="ntime">Positive global cue &middot; Gap up open expected</div>
        </div>
      </div>
      <div class="news-item">
        <div class="nimp" style="background:var(--red2);color:var(--red);border:1px solid rgba(224,45,60,0.3)">HIGH</div>
        <div>
          <div class="ntxt"><strong>Crude Oil</strong> — Middle East tension causes +2% spike &middot; Inflation concern</div>
          <div class="ntime">Crude +2% = Possible selling pressure on Nifty</div>
        </div>
      </div>
    </div>
  </div>

  <!-- JOURNAL + TRADE TABS -->
  <div class="card">
    <div class="chdr">
      <div class="ctitle">TRADING JOURNAL</div>
    </div>
    <div style="padding:12px 14px;">
      <div class="tab-row">
        <button class="tab-btn active" onclick="switchTab('journal',this)">Journal</button>
        <button class="tab-btn" onclick="switchTab('exit',this)">Exit Trade</button>
        <button class="tab-btn" onclick="switchTab('intro',this)">Introspection</button>
        <button class="tab-btn" onclick="switchTab('brain',this)">GOAT Brain</button>
      </div>
      <div id="panel-journal" class="tab-panel active">
        <div id="open-trade-card"></div>
        <div id="closed-trades"></div>
      </div>
      <div id="panel-exit" class="tab-panel">
        <div id="exit-form-wrap"></div>
      </div>
      <div id="panel-intro" class="tab-panel">
        <div style="display:flex;flex-direction:column;gap:12px;">
          <div>
            <label style="font-size:11px;color:var(--dim);">Rules followed? (1-5): <span id="rf-v" style="color:var(--green);">3</span></label>
            <input type="range" min="1" max="5" value="3" id="rf" class="w-full mt-1" oninput="document.getElementById('rf-v').textContent=this.value">
          </div>
          <div>
            <label style="font-size:11px;color:var(--dim);">SL skipped?</label>
            <select id="sl-skip" style="width:100%;background:var(--panel2);border:1px solid var(--border);border-radius:6px;padding:8px;color:var(--text);margin-top:4px;">
              <option>No</option><option>Yes</option>
            </select>
          </div>
          <div>
            <label style="font-size:11px;color:var(--dim);">Revenge trade?</label>
            <select id="revenge" style="width:100%;background:var(--panel2);border:1px solid var(--border);border-radius:6px;padding:8px;color:var(--text);margin-top:4px;">
              <option>No</option><option>Yes</option>
            </select>
          </div>
          <div>
            <label style="font-size:11px;color:var(--dim);">Discipline (1-5): <span id="dis-v" style="color:var(--green);">3</span></label>
            <input type="range" min="1" max="5" value="3" id="dis" class="w-full mt-1" oninput="document.getElementById('dis-v').textContent=this.value">
          </div>
          <div>
            <label style="font-size:11px;color:var(--dim);">Tomorrow's rule:</label>
            <textarea id="tmr" rows="2" style="width:100%;background:var(--panel2);border:1px solid var(--border);border-radius:6px;padding:8px;color:var(--text);margin-top:4px;resize:none;" placeholder="What will I do better tomorrow..."></textarea>
          </div>
          <button onclick="saveIntro()" style="background:var(--accent);color:#fff;border:none;border-radius:8px;padding:12px;font-weight:700;cursor:pointer;font-size:13px;">Save Introspection</button>
          <div id="dq-result" style="display:none;text-align:center;font-size:14px;font-weight:700;color:var(--green);padding:8px;"></div>
        </div>
      </div>
      <div id="panel-brain" class="tab-panel">
        <div id="ai-reasons" style="min-height:100px;">
          <div style="color:var(--dim);font-size:13px;text-align:center;padding:30px 0;">Waiting for signal conditions...</div>
        </div>
        <div style="margin-top:16px;padding-top:12px;border-top:1px solid var(--border);">
          <div class="flex items-center justify-between mb-2">
            <span style="font-size:10px;color:var(--dim);">Signal Strength</span>
            <span style="font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:700;color:var(--green)" id="confidence">0%</span>
          </div>
          <div style="height:5px;background:var(--border);border-radius:99px;overflow:hidden;">
            <div id="confidence-bar" style="height:100%;width:0%;background:linear-gradient(90deg,rgba(10,158,92,0.5),var(--green));border-radius:99px;transition:width 1s ease;"></div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ALERT FEED -->
  <div class="card">
    <div class="chdr">
      <div class="ctitle">LIVE ALERT FEED</div>
      <div style="font-size:10px;color:var(--dim)">Real-time notifications</div>
    </div>
    <div id="alert-list"></div>
  </div>

</div><!-- /left -->

<!-- RIGHT SIDEBAR -->
<div class="right">

  <!-- GREEKS -->
  <div class="card">
    <div class="chdr"><div class="ctitle">GREEKS & IV</div></div>
    <div class="greek-row">
      <div class="greek-l">IV (ATM)</div>
      <div class="greek-v" style="color:var(--gold)" id="iv-v">14.2% <small style="font-size:9px;color:var(--dim)">(LOW)</small></div>
    </div>
    <div class="greek-row">
      <div class="greek-l">IV Rank</div>
      <div class="greek-v" style="color:var(--blue)" id="ivr-v">38 <small style="font-size:9px;color:var(--green)">Buy Options</small></div>
    </div>
    <div class="greek-row">
      <div class="greek-l">GAMMA</div>
      <div class="greek-v" style="color:var(--purple)" id="gam-v">0.0048</div>
    </div>
    <div class="greek-row">
      <div class="greek-l">THETA/day</div>
      <div class="greek-v" style="color:var(--red)" id="tht-v">-\u20b912.4</div>
    </div>
    <div class="greek-row">
      <div class="greek-l">VEGA</div>
      <div class="greek-v" style="color:var(--blue)" id="veg-v">8.32</div>
    </div>
    <div class="greek-row">
      <div class="greek-l">DELTA (CE)</div>
      <div class="greek-v" style="color:var(--green)" id="del-v">0.52</div>
    </div>
  </div>

  <!-- OI ANALYSIS -->
  <div class="card">
    <div class="chdr">
      <div class="ctitle">OI ANALYSIS</div>
      <div style="font-size:10px;font-family:'JetBrains Mono',monospace;color:var(--green)" id="pcr-show">PCR: 1.24</div>
    </div>
    <div class="oi-p">
      <div class="oi-row"><div class="oi-l">CALL OI (Bears)</div><div class="oi-v" style="color:var(--red)" id="call-oi">48.2L</div></div>
      <div class="oi-track"><div class="oi-fill" id="call-bar" style="width:44%;background:var(--red)"></div></div>
      <div class="oi-row"><div class="oi-l">PUT OI (Bulls)</div><div class="oi-v" style="color:var(--green)" id="put-oi">59.8L</div></div>
      <div class="oi-track"><div class="oi-fill" id="put-bar" style="width:55%;background:var(--green)"></div></div>
      <div class="oi-row" style="margin-top:6px"><div class="oi-l">Max Pain</div><div class="oi-v" style="color:var(--gold)" id="mp-v">24,300</div></div>
      <div class="oi-row"><div class="oi-l">Key Resistance</div><div class="oi-v" style="color:var(--red)">24,500 CE</div></div>
      <div class="oi-row"><div class="oi-l">Key Support</div><div class="oi-v" style="color:var(--green)">24,300 PE</div></div>
    </div>
  </div>

  <!-- CANDLE PATTERNS -->
  <div class="card">
    <div class="chdr"><div class="ctitle">CANDLE PATTERNS</div></div>
    <div id="pat-list">
      <div class="pat-row">
        <div class="pat-icon">\ud83d\udd28</div>
        <div style="flex:1">
          <div class="pat-name">Hammer</div>
          <div class="pat-desc">Strong bullish reversal at support</div>
        </div>
        <div class="pat-conf" style="background:var(--green2);color:var(--green);border:1px solid rgba(10,158,92,0.3)">94%</div>
      </div>
      <div class="pat-row">
        <div class="pat-icon">\u2b50</div>
        <div style="flex:1">
          <div class="pat-name">Morning Star</div>
          <div class="pat-desc">3-candle bullish reversal</div>
        </div>
        <div class="pat-conf" style="background:var(--green2);color:var(--green);border:1px solid rgba(10,158,92,0.3)">78%</div>
      </div>
      <div class="pat-row">
        <div class="pat-icon">\ud83c\udf00</div>
        <div style="flex:1">
          <div class="pat-name">Bullish Engulfing</div>
          <div class="pat-desc">Strong buying pressure signal</div>
        </div>
        <div class="pat-conf" style="background:var(--green2);color:var(--green);border:1px solid rgba(10,158,92,0.3)">86%</div>
      </div>
    </div>
  </div>

  <!-- MINI MARKETS -->
  <div class="mini">
    <div class="mini-hdr">
      <div class="mini-name">BANKNIFTY</div>
      <div class="mini-px" id="bn-px" style="color:var(--red)">51,240</div>
    </div>
    <div class="mini-body">
      <div class="ms"><div class="ms-l">RSI</div><div class="ms-v" style="color:var(--red)" id="bn-rsi">71.2</div></div>
      <div class="ms"><div class="ms-l">VWAP</div><div class="ms-v" style="color:var(--red)" id="bn-vwap">BELOW</div></div>
      <div class="ms"><div class="ms-l">OI</div><div class="ms-v" style="color:var(--red)" id="bn-oi">SHORT</div></div>
      <div class="ms"><div class="ms-l">TREND</div><div class="ms-v" style="color:var(--red)" id="bn-tr">BEAR</div></div>
    </div>
    <div class="mini-jadui" id="bn-jadui" style="background:var(--red2);color:var(--red);border-color:rgba(224,45,60,0.3)">BEARISH SETUP — PE Buy</div>
  </div>

  <div class="mini">
    <div class="mini-hdr">
      <div class="mini-name">SENSEX</div>
      <div class="mini-px" id="sx-px" style="color:var(--green)">80,140</div>
    </div>
    <div class="mini-body">
      <div class="ms"><div class="ms-l">RSI</div><div class="ms-v" style="color:var(--green)" id="sx-rsi">52.4</div></div>
      <div class="ms"><div class="ms-l">VWAP</div><div class="ms-v" style="color:var(--green)" id="sx-vwap">ABOVE</div></div>
      <div class="ms"><div class="ms-l">OI</div><div class="ms-v" style="color:var(--green)" id="sx-oi">LONG</div></div>
      <div class="ms"><div class="ms-l">TREND</div><div class="ms-v" style="color:var(--green)" id="sx-tr">BULL</div></div>
    </div>
    <div class="mini-jadui" id="sx-jadui" style="background:var(--green2);color:var(--green);border-color:rgba(10,158,92,0.3)">BULLISH — Confirms Nifty</div>
  </div>

  <div class="mini">
    <div class="mini-hdr">
      <div class="mini-name">CRUDE OIL</div>
      <div class="mini-px" id="cr-px" style="color:var(--green)">\u20b96,842</div>
    </div>
    <div class="mini-body">
      <div class="ms"><div class="ms-l">RSI</div><div class="ms-v" style="color:var(--green)" id="cr-rsi">62.1</div></div>
      <div class="ms"><div class="ms-l">TREND</div><div class="ms-v" style="color:var(--green)" id="cr-tr">BULL</div></div>
      <div class="ms"><div class="ms-l">IMPACT</div><div class="ms-v" style="color:var(--red)">BEARISH</div></div>
      <div class="ms"><div class="ms-l">LEVEL</div><div class="ms-v" style="color:var(--gold)">\u20b96,900</div></div>
    </div>
    <div class="mini-jadui" style="background:var(--gold2);color:var(--gold);border-color:rgba(180,83,9,0.3)">Crude Up = Pressure on Nifty</div>
  </div>

  <div class="mini">
    <div class="mini-hdr">
      <div class="mini-name">MCX GOLD</div>
      <div class="mini-px" id="gd-px" style="color:var(--red)">\u20b971,240</div>
    </div>
    <div class="mini-body">
      <div class="ms"><div class="ms-l">RSI</div><div class="ms-v" style="color:var(--dim)" id="gd-rsi">48.2</div></div>
      <div class="ms"><div class="ms-l">TREND</div><div class="ms-v" style="color:var(--dim)" id="gd-tr">NEUTRAL</div></div>
      <div class="ms"><div class="ms-l">SUPPORT</div><div class="ms-v" style="color:var(--green)">\u20b970,800</div></div>
      <div class="ms"><div class="ms-l">TARGET</div><div class="ms-v" style="color:var(--blue)">\u20b972,000</div></div>
    </div>
    <div class="mini-jadui" style="background:var(--blue2);color:var(--blue);border-color:rgba(26,86,219,0.3)">WAIT — No clear setup yet</div>
  </div>

  <div class="mini">
    <div class="mini-hdr">
      <div class="mini-name">MCX SILVER</div>
      <div class="mini-px" id="sv-px" style="color:var(--green)">\u20b984,120</div>
    </div>
    <div class="mini-body">
      <div class="ms"><div class="ms-l">RSI</div><div class="ms-v" style="color:var(--green)" id="sv-rsi">55.8</div></div>
      <div class="ms"><div class="ms-l">TREND</div><div class="ms-v" style="color:var(--green)" id="sv-tr">BULL</div></div>
      <div class="ms"><div class="ms-l">TARGET</div><div class="ms-v" style="color:var(--blue)">\u20b985,500</div></div>
      <div class="ms"><div class="ms-l">SL</div><div class="ms-v" style="color:var(--red)">\u20b983,400</div></div>
    </div>
    <div class="mini-jadui" style="background:var(--green2);color:var(--green);border-color:rgba(10,158,92,0.3)">BULLISH — Momentum strong</div>
  </div>

  <!-- STOCKS -->
  <div class="card">
    <div class="chdr"><div class="ctitle">STOCKS — HDFC SBI PNB YES</div></div>
    <div id="stock-list">
      <div class="ind-row">
        <div class="iname">HDFC</div>
        <div class="ibar"><div class="ibfill" style="background:var(--green);width:62%"></div></div>
        <div class="ival" style="color:var(--green)" id="hdfc-v">\u20b91,842</div>
        <div class="isig bull">BUY</div>
      </div>
      <div class="ind-row">
        <div class="iname">SBI</div>
        <div class="ibar"><div class="ibfill" style="background:var(--green);width:70%"></div></div>
        <div class="ival" style="color:var(--green)" id="sbi-v">\u20b9824</div>
        <div class="isig bull">BUY</div>
      </div>
      <div class="ind-row">
        <div class="iname">PNB</div>
        <div class="ibar"><div class="ibfill" style="background:var(--red);width:35%"></div></div>
        <div class="ival" style="color:var(--red)" id="pnb-v">\u20b9102</div>
        <div class="isig bear">WAIT</div>
      </div>
      <div class="ind-row">
        <div class="iname">YES</div>
        <div class="ibar"><div class="ibfill" style="background:var(--dim);width:50%"></div></div>
        <div class="ival" style="color:var(--dim)" id="yes-v">\u20b924.4</div>
        <div class="isig neu">NEUTRAL</div>
      </div>
    </div>
  </div>

  <!-- WHATSAPP SETUP -->
  <div class="card" style="border:1.5px solid rgba(37,211,102,0.4)">
    <div class="chdr" style="background:#f0fdf4">
      <div class="ctitle" style="color:#15803d">WHATSAPP ALERT SETUP</div>
    </div>
    <div style="padding:12px 14px;font-size:12px;line-height:1.8;color:var(--dim)">
      <div style="margin-bottom:6px">1. Visit <strong>callmebot.com</strong> — Free WhatsApp API</div>
      <div style="margin-bottom:6px">2. Register your number → Get API key</div>
      <div style="margin-bottom:6px">3. Broker API + CallMeBot = Real-time alerts</div>
      <div style="margin-bottom:10px">4. Jadui Spot detected → Instant WhatsApp message!</div>
      <div onclick="copyWaLink()" style="background:#25d366;color:#fff;border-radius:6px;padding:8px 12px;text-align:center;cursor:pointer;font-weight:700;font-size:12px">
        callmebot.com — Copy Link
      </div>
    </div>
  </div>

  <button class="refresh-btn" style="width:100%" onclick="refreshAll()">REFRESH ALL SIGNALS</button>

</div><!-- /right -->
</div><!-- /layout -->

<!-- FOOTER -->
<div class="footer" style="margin-top:12px">
  <div class="footer-badge">LEGAL</div>
  <div class="footer-text">
    This tool is for <strong>Personal Educational Use</strong> only. It is not SEBI registered financial/investment advice.
    Trading involves substantial risk and you may lose your entire capital.
    Consult a SEBI registered advisor before any trade. This tool bears no responsibility for any losses.
  </div>
</div>

</div><!-- /wrap -->

<!-- SHARE FAB -->
<button class="share-fab" onclick="openShare()">Share with Friends</button>

<!-- SHARE MODAL -->
<div class="share-overlay" id="share-overlay">
  <div class="share-modal">
    <div class="share-title">Share with Friends</div>
    <div class="share-sub">Share this dashboard with your trading friends — for educational purpose only! Follow SEBI rules.</div>
    <div class="share-btns">
      <div class="share-btn" onclick="shareVia('whatsapp')">
        <div class="sb-icon">\ud83d\udcac</div>
        <div class="sb-info">
          <div class="sb-name">Share on WhatsApp</div>
          <div class="sb-desc">Send to friend via message</div>
        </div>
        <div style="color:var(--dim)">→</div>
      </div>
      <div class="share-btn" onclick="shareVia('copy')">
        <div class="sb-icon">\ud83d\udccb</div>
        <div class="sb-info">
          <div class="sb-name">Copy Link</div>
          <div class="sb-desc">Copy file path to share</div>
        </div>
        <div style="color:var(--dim)">→</div>
      </div>
      <div class="share-btn" onclick="shareVia('download')">
        <div class="sb-icon">\ud83d\udcbe</div>
        <div class="sb-info">
          <div class="sb-name">Download File</div>
          <div class="sb-desc">Download HTML to send to friends</div>
        </div>
        <div style="color:var(--dim)">→</div>
      </div>
      <div class="share-btn" onclick="shareVia('email')">
        <div class="sb-icon">\ud83d\udce7</div>
        <div class="sb-info">
          <div class="sb-name">Send via Email</div>
          <div class="sb-desc">Share via Gmail or any email</div>
        </div>
        <div style="color:var(--dim)">→</div>
      </div>
    </div>
    <div class="share-disclaimer">
      <strong>Legal Note:</strong> When sharing, clearly state this is an educational tool only, not financial advice. Explain trading risks to friends.
    </div>
    <div class="share-close" onclick="closeShare()">✕ Close</div>
  </div>
</div>

<script>
// ── CLOCK & SESSION ──────────────────────────────────────────────
function updateClock(){
  const n=new Date();
  const t=`${String(n.getHours()).padStart(2,'0')}:${String(n.getMinutes()).padStart(2,'0')}:${String(n.getSeconds()).padStart(2,'0')}`;
  document.getElementById('tb-time').textContent=t;
  const m=n.getHours()*60+n.getMinutes();
  [['s1',9*60+15,11*60],['s2',11*60,13*60],['s3',13*60,14*60+30],['s4',14*60+30,15*60+30]].forEach(([id,from,to])=>{
    document.getElementById(id).classList.toggle('active',m>=from&&m<to);
  });
}
setInterval(updateClock,1000); updateClock();

// ── GLOBAL STATE ─────────────────────────────────────────────────
let liveData = {};
let closedTrades = [];
let openTrade = null;
let currentMarket = 'NIFTY';
let currentTf = '1m';

// ── CANDLE CHART ───────────────────────────────────────────────
const canvas=document.getElementById('main-canvas');
const ctx=canvas.getContext('2d');
let candles=[];

function genCandles(n,base){
  const arr=[]; let p=base;
  for(let i=0;i<n;i++){
    const o=p,c=o+(Math.random()-0.47)*28;
    const h=Math.max(o,c)+Math.random()*12;
    const l=Math.min(o,c)-Math.random()*12;
    arr.push({o,h,l,c}); p=c;
  }
  return arr;
}

function drawChart(){
  const W=canvas.parentElement.offsetWidth-28, H=180;
  canvas.width=W; canvas.height=H;
  ctx.clearRect(0,0,W,H);
  if(!candles.length)return;
  const hi=Math.max(...candles.map(c=>c.h));
  const lo=Math.min(...candles.map(c=>c.l));
  const range=hi-lo||1;
  const pad=12;
  const toY=v=>pad+(hi-v)/range*(H-pad*2);
  const gap=Math.floor(W/candles.length);
  const cw=Math.max(3,gap-2);

  // Grid
  ctx.strokeStyle='rgba(26,86,219,0.05)'; ctx.lineWidth=1;
  for(let i=0;i<5;i++){
    const y=pad+(H-pad*2)/4*i;
    ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(W,y); ctx.stroke();
  }

  candles.forEach((c,i)=>{
    const x=i*gap+gap/2, bull=c.c>=c.o;
    const col=bull?'#0a9e5c':'#e02d3c';
    ctx.strokeStyle=col; ctx.lineWidth=1;
    ctx.beginPath(); ctx.moveTo(x,toY(c.h)); ctx.lineTo(x,toY(c.l)); ctx.stroke();
    ctx.fillStyle=bull?'rgba(10,158,92,0.8)':'rgba(224,45,60,0.8)';
    const t=toY(Math.max(c.o,c.c)),bh=Math.max(1,toY(Math.min(c.o,c.c))-t);
    ctx.fillRect(x-cw/2,t,cw,bh);
  });

  // VWAP line
  const vwapVal = liveData.indicators ? liveData.indicators.vwap : 24362;
  const vwapY=toY(vwapVal);
  ctx.strokeStyle='#1a56db'; ctx.lineWidth=1.5; ctx.setLineDash([5,4]);
  ctx.beginPath(); ctx.moveTo(0,vwapY); ctx.lineTo(W,vwapY); ctx.stroke();
  ctx.fillStyle='#1a56db'; ctx.font='9px JetBrains Mono'; ctx.textAlign='left';
  ctx.fillText('VWAP',4,vwapY-3);
  ctx.setLineDash([]);

  // Jadui Spot marker
  const lx=(candles.length-1)*gap+gap/2;
  ctx.fillStyle='rgba(10,158,92,0.08)';
  ctx.fillRect(lx-22,0,44,H);
  ctx.strokeStyle='rgba(10,158,92,0.5)'; ctx.lineWidth=1.5;
  ctx.strokeRect(lx-22,0,44,H);
  ctx.fillStyle='#0a9e5c'; ctx.font='bold 9px Bebas Neue'; ctx.textAlign='center';
  ctx.fillText('\u2726JADUI',lx,14);
}

function setTf(el,tf){
  document.querySelectorAll('.tft').forEach(t=>t.classList.remove('on'));
  el.classList.add('on'); currentTf=tf;
  document.getElementById('ind-tf-label').textContent=tf.toUpperCase()+' LIVE';
  candles=genCandles(tf==='1m'?60:tf==='5m'?40:20, 24250+Math.random()*100);
  drawChart();
}

// ── FETCH DATA ─────────────────────────────────────────────────
async function fetchData(){
  try{
    const r=await fetch('/api/data');
    liveData=await r.json();
    if(liveData.error) return;
    if(liveData.candles&&liveData.candles.length>0){
      candles=liveData.candles.map(c=>({o:c.open,h:c.high,l:c.low,c:c.close}));
    }
    if(liveData.current_candle&&liveData.current_candle.close>0){
      candles.push({
        o:liveData.current_candle.open,
        h:liveData.current_candle.high,
        l:liveData.current_candle.low,
        c:liveData.current_candle.close
      });
    }
    drawChart();
    updateUI(liveData);
  }catch(e){console.error('fetchData error:',e);}
}

async function fetchTrades(){
  try{
    const r=await fetch('/api/trades');
    const d=await r.json();
    closedTrades=d.closed||[];
    openTrade=d.open||null;
    renderJournal();
    renderExitForm();
  }catch(e){}
}

// ── UPDATE UI ─────────────────────────────────────────────────
function updateUI(d){
  // Hero price
  const md = d.market_data ? d.market_data[currentMarket] : null;
  if(md){
    document.getElementById('hero-price').textContent=md.price.toLocaleString('en-IN');
    const chgStr = (md.chg>=0?'+':'')+md.chg+'%';
    const chgCol = md.chg>=0?'#0a9e5c':'#e02d3c';
    document.getElementById('hero-chg').innerHTML=(md.chg>=0?'\u25b2 ':'\u25bc ')+chgStr+' &nbsp; '+(md.dir==='bull'?'BULLISH':'BEARISH');
    document.getElementById('hero-chg').style.color=chgCol;
    document.getElementById('h-open').textContent=md.open.toLocaleString('en-IN');
    document.getElementById('h-high').textContent=md.high.toLocaleString('en-IN');
    document.getElementById('h-low').textContent=md.low.toLocaleString('en-IN');
    document.getElementById('h-pcr').textContent=md.pcr+(md.pcr>1?'':'')+'';
    document.getElementById('h-pcr').style.color=md.pcr>1?'#0a9e5c':'#e02d3c';
    document.getElementById('h-iv').textContent=md.iv+'%';
    document.getElementById('h-maxpain').textContent=md.maxpain.toLocaleString('en-IN');
  }

  // Signal box
  const sbox=document.getElementById('signal-box');
  const stitle=document.getElementById('sig-title');
  const sdet=document.getElementById('sig-detail');
  if(d.status==='TRADE_ACTIVE'){
    sbox.className='sigbox trade';
    stitle.style.color='var(--green)';
    stitle.textContent='TRADE ACTIVE';
    sdet.innerHTML=`${d.direction} ${d.atm_strike} ${d.option_type} &middot; Entry: ${d.entry} &middot; TGT: ${d.target} &middot; SL: ${d.sl}`;
  }else if(d.status==='SETUP_READY'){
    sbox.className='sigbox setup';
    stitle.style.color='var(--gold)';
    stitle.textContent='SETUP READY';
    sdet.innerHTML=`${d.direction} ${d.atm_strike} ${d.option_type} &middot; Waiting for trigger...`;
  }else{
    sbox.className='sigbox block';
    stitle.style.color='var(--dim)';
    stitle.textContent=d.signal||'WAITING';
    sdet.textContent=`${d.pass_count||0}/5 conditions met`;
  }

  // Jadui card
  updateJaduiCard(d);

  // Indicators
  if(d.indicators){
    const ind=d.indicators;
    document.getElementById('rsi-bar').style.cssText=`background:${ind.rsi<40?'var(--green)':ind.rsi>60?'var(--red)':'var(--blue)'};width:${Math.min(100,ind.rsi)}%`;
    document.getElementById('rsi-val').textContent=ind.rsi;
    document.getElementById('rsi-val').style.color=ind.rsi<40?'var(--green)':ind.rsi>60?'var(--red)':'var(--blue)';
    document.getElementById('rsi-sig').textContent=ind.rsi_sig;
    document.getElementById('rsi-sig').className='isig '+(ind.rsi<40?'bull':ind.rsi>60?'bear':'neu');
    document.getElementById('vwap-val').textContent=ind.vwap.toLocaleString();
    document.getElementById('ema9-val').textContent=ind.ema9.toLocaleString();
    document.getElementById('ema21-val').textContent=ind.ema21.toLocaleString();
    document.getElementById('st-val').textContent=ind.supertrend.toLocaleString();
  }

  // 5-Point Checklist
  updateChecklist(d.chk||[false,false,false,false,false], d.pass_count||0);

  // Greeks
  if(d.vix){
    document.getElementById('iv-v').innerHTML=d.vix.toFixed(1)+'% <small style="font-size:9px;color:var(--dim)">'+(d.vix<18?'(LOW)':'(HIGH)')+'</small>';
    document.getElementById('ivr-v').innerHTML=Math.floor(20+Math.random()*30)+' <small style="font-size:9px;color:var(--green)">'+(d.vix<18?'Buy Options':'Sell Options')+'</small>';
  }

  // OI
  if(md&&md.pcr){
    const pcr=md.pcr;
    document.getElementById('pcr-show').textContent='PCR: '+pcr;
    document.getElementById('pcr-show').style.color=pcr>1?'var(--green)':'var(--red)';
    document.getElementById('mp-v').textContent=md.maxpain.toLocaleString();
  }

  // Topbar stats
  document.getElementById('tb-trades').textContent=d.total||0;
  const pnl=d.pnl||0;
  document.getElementById('tb-pnl').textContent=(pnl>=0?'+':'')+'\u20b9'+Math.abs(pnl).toLocaleString('en-IN');
  document.getElementById('tb-pnl').style.color=pnl>=0?'#4ade80':'#f87171';
  document.getElementById('tb-wr').textContent=(d.win_rate||0)+'%';

  // Capital bar
  const capPnl=d.session_pnl_rs||0;
  document.getElementById('cap-pnl').textContent=(capPnl>=0?'+':'')+'\u20b9'+Math.abs(capPnl).toLocaleString('en-IN');
  document.getElementById('cap-pnl').style.color=capPnl>=0?'var(--green)':'var(--red)';
  const riskPct=Math.min(100,Math.round((Math.abs(capPnl)/555)*100));
  document.getElementById('risk-pct').textContent=riskPct+'%';
  document.getElementById('risk-fill').style.width=riskPct+'%';

  // Mini markets
  updateMiniMarkets(d.market_data);

  // GOAT Brain
  updateBrain(d);

  // Alerts
  if(d.signal&&d.signal.includes('HIT')) addAlert(d);
}

function updateJaduiCard(d){
  const jcard=document.getElementById('jadui-card');
  const jbadge=document.getElementById('j-badge');
  const jtitle=document.getElementById('j-title');
  const jdesc=document.getElementById('j-desc');
  if(d.status==='SETUP_READY'||d.status==='TRADE_ACTIVE'){
    const isBull=d.direction==='LONG';
    jcard.style.cssText=`border-color:${isBull?'rgba(10,158,92,0.4)':'rgba(224,45,60,0.4)'};background:${isBull?'var(--green2)':'var(--red2)'};border-radius:10px;padding:14px 16px;border:2px solid;position:relative;overflow:hidden;`;
    jbadge.style.cssText=`background:${isBull?'var(--green2)':'var(--red2)'};color:${isBull?'var(--green)':'var(--red)'};border:1px solid ${isBull?'rgba(10,158,92,0.3)':'rgba(224,45,60,0.3)'};display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:20px;font-family:'JetBrains Mono',monospace;font-size:10px;font-weight:700;margin-bottom:8px;`;
    jbadge.textContent=isBull?'CONFIRMED BUY':'CONFIRMED SELL';
    jtitle.style.color=isBull?'var(--green)':'var(--red)';
    jtitle.textContent=(isBull?'BULLISH':'BEARISH')+' JADUI SPOT '+d.status+'!';
    jdesc.innerHTML=`${isBull?'Bullish':'Bearish'} setup at <strong>${d.atm_strike} ${d.option_type}</strong><br>Entry: ${d.entry} &middot; SL: ${d.sl} &middot; TGT: ${d.target}<br>${d.signal||''}`;
    document.getElementById('j-entry').textContent=d.entry||'--';
    document.getElementById('j-sl').textContent=d.sl||'--';
    document.getElementById('j-tgt1').textContent=d.target||'--';
  }else{
    jcard.style.cssText='border-color:rgba(180,83,9,0.4);background:var(--gold2);border-radius:10px;padding:14px 16px;border:2px solid;position:relative;overflow:hidden;';
    jbadge.style.cssText='background:var(--gold2);color:var(--gold);border:1px solid rgba(180,83,9,0.3);display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:20px;font-family:\'JetBrains Mono\',monospace;font-size:10px;font-weight:700;margin-bottom:8px;';
    jbadge.textContent='SCANNING...';
    jtitle.style.color='var(--gold)';
    jtitle.textContent='WAITING FOR JADUI SPOT';
    jdesc.innerHTML='Monitoring market conditions...<br>'+(d.brain_reasons?d.brain_reasons.slice(0,2).join('<br>'):'');
  }
}

function updateChecklist(chk, passCount){
  const labels=['NIFTY above round level','Velocity positive (momentum)','Distance from round number OK','VIX safe (below 18)','Within valid strike range'];
  labels.forEach((lbl,i)=>{
    const el=document.getElementById('chk-'+i);
    if(el){
      el.textContent=chk[i]?'PASS':'WAIT';
      el.className='chk-pass '+(chk[i]?'bull':'bear');
      el.parentElement.style.cssText=chk[i]?'background:var(--green2);':'background:var(--red2);';
    }
  });
  const sc=document.getElementById('chk-score');
  if(sc){sc.textContent=passCount+'/5'; sc.style.color=passCount===5?'var(--green)':passCount>=3?'var(--gold)':'var(--red)';}
}

function updateMiniMarkets(md){
  if(!md)return;
  const mkts=['BANKNIFTY','SENSEX','CRUDE','GOLD','SILVER'];
  const ids=['bn','sx','cr','gd','sv'];
  mkts.forEach((m,i)=>{
    const d=md[m];
    if(!d)return;
    const el=document.getElementById(ids[i]+'-px');
    if(el){
      el.textContent=(m==='CRUDE'||m==='GOLD'||m==='SILVER'?'\u20b9':'')+d.price.toLocaleString('en-IN');
      el.style.color=d.dir==='bull'?'var(--green)':'var(--red)';
    }
  });
}

function updateBrain(d){
  const reasons=d.brain_reasons||[];
  const aiDiv=document.getElementById('ai-reasons');
  const passCount=d.pass_count||0;
  const confidence=Math.round((passCount/5)*100);
  document.getElementById('confidence').textContent=confidence+'%';
  document.getElementById('confidence-bar').style.width=confidence+'%';
  if(reasons.length>0&&aiDiv){
    aiDiv.innerHTML=reasons.map((r,i)=>`<div style="padding:6px 0;animation:fadeIn 0.3s ${i*0.15}s forwards;opacity:0;"><span style="color:var(--green);font-weight:700;">✔</span> <span style="font-size:12px;color:var(--text);">${r}</span></div>`).join('');
  }
}

// ── ALERTS ────────────────────────────────────────────────────
function addAlert(d){
  const now=new Date();
  const t=`${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`;
  const list=document.getElementById('alert-list');
  const item=document.createElement('div'); item.className='alert-item';
  const isWin=d.pnl>=0;
  item.innerHTML=`<div class="alert-time">${t}</div><div class="alert-badge ${isWin?'bull':'bear'}">${d.status}</div><div class="alert-msg">${d.signal}</div><div class="alert-px">${d.spot||''}</div>`;
  list.insertBefore(item,list.firstChild);
  if(list.children.length>6)list.removeChild(list.lastChild);
}

// ── JOURNAL ───────────────────────────────────────────────────
function renderJournal(){
  const ot=document.getElementById('open-trade-card');
  if(openTrade){
    ot.innerHTML=`<div style="background:var(--green2);border:1px solid rgba(10,158,92,0.3);border-radius:10px;padding:16px;margin-bottom:12px;"><div style="color:var(--green);font-weight:700;font-size:12px;margin-bottom:10px;">OPEN TRADE</div><div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;font-size:12px;"><div>Direction: <span style="font-weight:700;color:${openTrade.direction==='LONG'?'var(--green)':'var(--red)'}">${openTrade.direction}</span></div><div>Entry: <span style="font-weight:700">\u20b9${openTrade.entry_price}</span></div><div>Time: <span style="color:var(--dim)">${openTrade.entry_time}</span></div><div>Target: <span style="color:var(--green);font-weight:700">\u20b9${openTrade.target}</span></div><div>SL: <span style="color:var(--red);font-weight:700">\u20b9${openTrade.sl}</span></div><div>Strike: <span style="color:var(--blue)">${openTrade.atm_strike} ${openTrade.option_type}</span></div></div></div>`;
  }else{ot.innerHTML='';}

  const cd=document.getElementById('closed-trades');
  if(closedTrades.length===0){cd.innerHTML='<div style="text-align:center;color:var(--dim);padding:30px;font-size:13px;">No trades yet</div>';return;}
  cd.innerHTML=closedTrades.slice(0,15).map(t=>{
    const win=(t.pnl||0)>0;
    return `<div class="jentry ${win?'jwin':'jloss'}"><div class="jhead"><span class="jdir" style="color:${t.direction==='LONG'?'var(--green)':'var(--red)'}">${t.direction}</span><span style="font-size:10px;color:var(--dim)">${t.atm_strike||''} ${t.option_type||''}</span><span class="jpnl" style="color:${win?'var(--green)':'var(--red)'}">${win?'+':''}${t.pnl} pts</span></div><div style="font-size:10px;color:var(--dim)">${t.entry_time||''} → ${t.exit_time||''} | ${t.exit_reason||''}</div></div>`;
  }).join('');
}

function renderExitForm(){
  const wrap=document.getElementById('exit-form-wrap');
  if(!openTrade){wrap.innerHTML='<div style="text-align:center;color:var(--dim);padding:40px;font-size:13px;">No open trade</div>';return;}
  wrap.innerHTML=`<div style="display:flex;flex-direction:column;gap:12px;"><div style="color:var(--gold);font-weight:700;margin-bottom:4px;">Manual Exit — Trade #${openTrade.id}</div><div><label style="font-size:11px;color:var(--dim);">Exit Price</label><input type="number" id="ep" value="${liveData.spot||0}" style="width:100%;background:var(--panel2);border:1px solid var(--border);border-radius:6px;padding:8px;color:var(--text);margin-top:4px;"></div><div><label style="font-size:11px;color:var(--dim);">Exit Reason</label><select id="er" style="width:100%;background:var(--panel2);border:1px solid var(--border);border-radius:6px;padding:8px;color:var(--text);margin-top:4px;"><option>Target Hit</option><option>SL Hit</option><option>Manual Exit</option><option>Time Exit</option><option>Trailing SL</option></select></div><div><label style="font-size:11px;color:var(--dim);">Emotion</label><select id="em" style="width:100%;background:var(--panel2);border:1px solid var(--border);border-radius:6px;padding:8px;color:var(--text);margin-top:4px;"><option>Calm</option><option>Greedy</option><option>Fearful</option><option>Revenge</option><option>Overconfident</option><option>Patient</option></select></div><div><label style="font-size:11px;color:var(--dim);">Post Note</label><textarea id="en" rows="2" style="width:100%;background:var(--panel2);border:1px solid var(--border);border-radius:6px;padding:8px;color:var(--text);margin-top:4px;resize:none;" placeholder="What did I learn from this trade?"></textarea></div><button onclick="doExit(${openTrade.id},'${openTrade.direction}',${openTrade.entry_price})" style="background:var(--red);color:#fff;border:none;border-radius:8px;padding:12px;font-weight:700;cursor:pointer;font-size:13px;">Exit Trade</button></div>`;
}

// ── ACTIONS ───────────────────────────────────────────────────
async function doExit(tid,dir,entry){
  const ep=parseFloat(document.getElementById('ep').value);
  const pnl=dir==='LONG'?ep-entry:entry-ep;
  await fetch('/paper/exit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({trade_id:tid,exit_price:ep,direction:dir,entry_price:entry,exit_reason:document.getElementById('er').value,post_note:document.getElementById('en').value,emotion:document.getElementById('em').value})});
  await fetchTrades(); switchTab('journal',document.querySelectorAll('.tab-btn')[0]);
}

async function saveIntro(){
  const r=await fetch('/paper/intro',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rule_followed:document.getElementById('rf').value,sl_skip:document.getElementById('sl-skip').value,revenge:document.getElementById('revenge').value,discipline:document.getElementById('dis').value,tomorrow_rule:document.getElementById('tmr').value})});
  const d=await r.json();
  const el=document.getElementById('dq-result');
  el.style.display='block'; el.textContent='DQ Score: '+d.dq_score+'/100 — '+d.breakdown;
}

// ── TAB SWITCHERS ─────────────────────────────────────────────────
function switchTab(name,btn){
  document.querySelectorAll('.tab-panel').forEach(t=>t.classList.remove('active'));
  const el=document.getElementById('panel-'+name);
  if(el)el.classList.add('active');
  if(btn){
    document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
  }
}

function switchMarket(el,key){
  document.querySelectorAll('.mktab').forEach(t=>t.classList.remove('on'));
  el.classList.add('on'); currentMarket=key;
  const m=liveData.market_data?liveData.market_data[key]:null;
  if(m){
    document.getElementById('hero-label').textContent=key+' &middot; '+(key==='CRUDE'?'MCX':key==='GOLD'||key==='SILVER'?'MCX':key==='STOCKS'?'NSE':'INDEX');
    updateUI(liveData);
  }
}

function setText(id,val){
  const el=document.getElementById(id);
  if(el)el.textContent=val;
}

// ── SHARE ────────────────────────────────────────────────────
function openShare(){document.getElementById('share-overlay').classList.add('open');}
function closeShare(){document.getElementById('share-overlay').classList.remove('open');}
document.getElementById('share-overlay').addEventListener('click',function(e){if(e.target===this)closeShare();});

function shareVia(type){
  const msg='Check out GOAT PRO Trading Dashboard — Nifty, BankNifty, Crude, Gold all in one place! For educational use only. Trading involves risk.';
  if(type==='whatsapp')window.open('https://wa.me/?text='+encodeURIComponent(msg),'_blank');
  else if(type==='copy'){navigator.clipboard.writeText(msg).then(()=>alert('Message copied! Paste anywhere to share.'));}
  else if(type==='download'){const a=document.createElement('a');a.href=window.location.href;a.download='GOAT-PRO-Dashboard.html';a.click();alert('File downloading!');}
  else if(type==='email')window.open('mailto:?subject=GOAT PRO Trading Dashboard&body='+encodeURIComponent(msg),'_blank');
  closeShare();
}

function copyWaLink(){
  navigator.clipboard.writeText('https://api.callmebot.com/whatsapp.php?phone=YOURPHONE&text=GOAT+JADUI+ALERT&apikey=YOURKEY')
    .then(()=>alert('Link copied! Replace YOURPHONE and YOURKEY with your details.'))
    .catch(()=>alert('Visit callmebot.com to get your free WhatsApp API key!'));
}

function refreshAll(){fetchData(); fetchTrades();}

// ── INIT ────────────────────────────────────────────────────
candles=genCandles(60,24250); drawChart();
window.addEventListener('resize',drawChart);
fetchData(); fetchTrades();
setInterval(fetchData,5000);
setInterval(fetchTrades,10000);
</script>
</body>
</html>'''


# ═══════════════════════════════════════════════════════════════════════════
# FLASK ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template_string(TEMPLATE)

@app.route("/api/data")
def api_data():
    data = run_pipeline()
    if "error" in data:
        return jsonify({
            "error": data["error"], "spot": 0, "vix": 15, "velocity": 0,
            "status": "BLOCKED", "signal": data["error"], "brain_reasons": [],
            "entry": 0, "target": 0, "sl": 0, "chk": [False]*5, "pass_count": 0,
            "total": 0, "wins": 0, "losses": 0, "pnl": 0, "win_rate": 0,
            "market_status": "CLOSED", "market_msg": data["error"],
            "atm_strike": 0, "option_type": "CE", "direction": "LONG",
            "data_source": "\u2014", "expiry": "\u2014",
            "candles": [], "current_candle": {},
            "market_data": MARKET_DATA, "indicators": INDICATORS,
        })
    return jsonify(data)

@app.route("/api/trades")
def api_trades():
    closed = db_closed_trades()
    return jsonify({"open": db_open_trade(), "closed": closed, "stats": calc_stats(closed)})

@app.route("/ping")
def ping():
    return jsonify({"status": "alive", "time": time.strftime("%H:%M:%S"), "service": "GOAT PRO"})

@app.route("/paper/exit", methods=["POST"])
def paper_exit():
    d = request.get_json()
    tid = d.get("trade_id"); ep = float(d.get("exit_price", 0))
    dir_ = d.get("direction", "LONG"); enp = float(d.get("entry_price", 0))
    pnl = round((ep - enp) if dir_ == "LONG" else (enp - ep), 2)
    db_close_trade(tid, ep, d.get("exit_reason", ""), d.get("post_note", ""), d.get("emotion", ""), pnl)
    if pnl > 0: ENGINE["trades_won"] += 1
    else: ENGINE["trades_lost"] += 1
    ENGINE["trades_total"] += 1
    ENGINE["session_pnl"] = round(ENGINE["session_pnl"] + pnl, 1)
    return jsonify({"status": "ok", "pnl": pnl})

@app.route("/paper/intro", methods=["POST"])
def paper_intro():
    d = request.get_json(); today = time.strftime("%d-%m-%Y")
    db_add_intro({**d, "date": today})
    dq_score, breakdown = calc_dq_score(d.get("rule_followed", 3), d.get("sl_skip", "No"), d.get("revenge", "No"), d.get("discipline", 3))
    db_add_dq({"date": today, "dq_score": dq_score, "breakdown": breakdown})
    return jsonify({"status": "ok", "dq_score": dq_score, "breakdown": breakdown})

@app.route("/paper/clear", methods=["POST"])
def paper_clear():
    db_clear_trades()
    return jsonify({"status": "ok"})

# ═══════════════════════════════════════════════════════════════════════════
# MAIN ENTRY
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
