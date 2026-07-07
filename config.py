import os
import pytz
from datetime import datetime, time
from typing import Optional

# ────────────────────────────────────────────
# Angel One API Credentials (from env)
# ────────────────────────────────────────────
ANGEL_API_KEY     = os.getenv("ANGEL_API_KEY", "")
ANGEL_CLIENT_ID   = os.getenv("ANGEL_CLIENT_ID", "")
ANGEL_MPIN        = os.getenv("ANGEL_MPIN", "")
ANGEL_TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "")

# ────────────────────────────────────────────
# Symbols (names only — tokens resolved dynamically)
# ────────────────────────────────────────────
NIFTY_SYMBOL     = "NIFTY"
BANKNIFTY_SYMBOL = "BANKNIFTY"
VIX_SYMBOL       = "INDIA VIX"      # try NSE first, fallback NFO
FINNIFTY_SYMBOL  = "FINNIFTY"
SENSEX_SYMBOL    = "SENSEX"
CRUDEOIL_SYMBOL  = "CRUDEOIL"
GOLD_SYMBOL      = "GOLD"
SILVER_SYMBOL    = "SILVER"
# Global Indices
KOSPI_SYMBOL     = "^KS11"
DJI_SYMBOL       = "^DJI"
NASDAQ_SYMBOL    = "^IXIC"
FTSE_SYMBOL      = "^FTSE"

# ────────────────────────────────────────────
# Shared state (populated at runtime)
# ────────────────────────────────────────────
latest_prices: dict = {
    "nifty": None,
    "banknifty": None,
    "vix": None,
    "finnifty": None,
    "sensex": None,
    "crudeoil": None,
    "gold": None,
    "silver": None,
    "kospi": None,
    "dji": None,
    "nasdaq": None,
    "ftse": None,
    "day_open": None,
    "day_open_date": "",
    "last_update": "",
}

oi_data: dict = {
    "call_oi": 0,
    "put_oi": 0,
    "pcr": 0.0,
    "max_pain": None,
}

greeks_data: dict = {
    "iv": None,
    "delta": None,
    "theta": None,
    "gamma": None,
    "vega": None,
}

news_feed: list = []
market_alerts: list = []

candle_store: list = []

indicator_data: dict = {
    "rsi": None,
    "ema9": None,
    "ema21": None,
    "vwap_approx": None,
}

signal_data: dict = {
    "signal": "WAIT",
    "confidence": 0,
    "checklist": {},
    "orb_high": None,
    "orb_low": None,
    "note": "Initializing...",
}

# ────────────────────────────────────────────
# Time helpers
# ────────────────────────────────────────────
IST = pytz.timezone("Asia/Kolkata")

def get_ist_now() -> datetime:
    return datetime.now(IST)

def get_market_status() -> str:
    """Returns OPEN / PRE_OPEN / CLOSED based on IST time."""
    now = get_ist_now()
    t = now.time()
    weekday = now.weekday()
    
    # Saturday = 5, Sunday = 6
    if weekday >= 5:
        return "CLOSED"
    
    # Pre-open: 9:00 - 9:15
    if time(9, 0) <= t < time(9, 15):
        return "PRE_OPEN"
    
    # Market hours: 9:15 - 15:30
    if time(9, 15) <= t <= time(15, 30):
        return "OPEN"
    
    return "CLOSED"
