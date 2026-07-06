import os
import datetime

# --- Environment Credentials ---
ANGEL_API_KEY = os.environ.get("ANGEL_API_KEY")
ANGEL_CLIENT_ID = os.environ.get("ANGEL_CLIENT_ID")
ANGEL_MPIN = os.environ.get("ANGEL_MPIN")
ANGEL_TOTP_SECRET = os.environ.get("ANGEL_TOTP_SECRET")

# --- NSE Tokens & Symbols ---
NIFTY_TOKEN = "99926000"
NIFTY_SYMBOL = "Nifty 50"
BANKNIFTY_TOKEN = "99926009"
BANKNIFTY_SYMBOL = "Nifty Bank"
VIX_TOKEN = "99926017"
VIX_SYMBOL = "India VIX"

# --- Timezone Helpers ---
IST_OFFSET = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def get_ist_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).astimezone(IST_OFFSET)

def get_market_status() -> str:
    """Returns 'OPEN', 'PRE_OPEN', or 'CLOSED' based on real IST time."""
    now = get_ist_now()
    if now.weekday() >= 5:  # Sat=5, Sun=6
        return "CLOSED"
    t = now.time()
    if datetime.time(9, 0) <= t < datetime.time(9, 15):
        return "PRE_OPEN"
    if datetime.time(9, 15) <= t <= datetime.time(15, 30):
        return "OPEN"
    return "CLOSED"

# --- Shared Global In-Memory Cache/State ---
latest_prices = {
    "nifty": None, 
    "vix": None,
    "day_open": None, 
    "day_open_date": None,
    "last_update": None,
}

indicator_data = {
    "rsi": None, 
    "ema9": None, 
    "ema21": None, 
    "vwap_approx": None
}

signal_data = {
    "signal": "WAIT", 
    "confidence": 0, 
    "checklist": {},
    "orb_high": None, 
    "orb_low": None, 
    "note": "Waiting for data"
}

candle_store = []
