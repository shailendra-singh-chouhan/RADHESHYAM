"""
GOAT PRO — Configuration & Symbols
(Angel One removed — all live data now sourced from Yahoo Finance)
"""

import os
import datetime
import pytz

# ─── Safety Toggle ──────────────────────────────────────────────────
AUTO_TRADE_ENABLED = os.getenv("AUTO_TRADE_ENABLED", "false").lower() == "true"

# ─── Yahoo Finance Symbol Map ────────────────────────────────────────
# internal name -> Yahoo Finance ticker
SYMBOLS = {
    "nifty": "^NSEI",
    "banknifty": "^NSEBANK",
    "finnifty": "NIFTY_FIN_SERVICE.NS",
    "sensex": "^BSESN",
    "crudeoil": "CL=F",
    "gold": "GC=F",
    "silver": "SI=F",
    "usdinr": "INR=X",
    "midcap": "^NSEMDCP50",
    "vix": "^INDIAVIX",
}

# ─── Poller Intervals ───────────────────────────────────────────────
PRICE_POLL_INTERVAL = 15       # yfinance — no need to hammer every 5s
INDICATOR_POLL_INTERVAL = 180

# ─── Risk Limits ────────────────────────────────────────────────────
MAX_DAILY_LOSS = 5000
MAX_TRADE_LOSS = 2000

# ─── Strategy Parameters ───────────────────────────────────────────
RSI_PERIOD = 14
EMA_FAST = 9
EMA_SLOW = 21
SUPERTREND_PERIOD = 10
SUPERTREND_MULTIPLIER = 3
ORB_MINUTES = 15
MIN_SIGNAL_CONFIDENCE = 4

# ─── Database ───────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/goatpro")

# ─── Time & Market Status Utilities ──────────────────────────────────
IST = pytz.timezone("Asia/Kolkata")


def get_ist_now():
    """Current time localized to Indian Standard Time (IST)."""
    return datetime.datetime.now(IST)


def get_market_status():
    """Real market-hours check: NSE trades Mon-Fri, 9:15 AM - 3:30 PM IST.
    (No more hardcoded 'always OPEN' — that was a troubleshooting hack
    left in by a previous edit and has been removed.)"""
    now = get_ist_now()
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return "CLOSED"
    open_time = now.replace(hour=9, minute=15, second=0, microsecond=0)
    close_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now < open_time:
        return "PRE_MARKET"
    if now > close_time:
        return "CLOSED"
    return "OPEN"
