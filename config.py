import os
import datetime

# ============================================
# Angel One API Credentials
# ============================================
ANGEL_API_KEY = os.environ.get("ANGEL_API_KEY")
ANGEL_CLIENT_ID = os.environ.get("ANGEL_CLIENT_ID")
ANGEL_MPIN = os.environ.get("ANGEL_MPIN")
ANGEL_TOTP_SECRET = os.environ.get("ANGEL_TOTP_SECRET")

# ============================================
# NSE Indices Tokens (Official - Angel One API)
# ============================================
NIFTY_TOKEN = "26000"
NIFTY_SYMBOL = "NIFTY 50"

BANKNIFTY_TOKEN = "26009"
BANKNIFTY_SYMBOL = "NIFTY BANK"

VIX_TOKEN = "26017"
VIX_SYMBOL = "INDIA VIX"

# ============================================
# Exchange & API Settings
# ============================================
EXCHANGE_NSE = "NSE"
BASE_URL = "https://apiconnect.angelbroking.com"

# ============================================
# Timezone & Market Hours
# ============================================
IST_OFFSET = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def get_ist_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).astimezone(IST_OFFSET)

def get_market_status() -> str:
    now = get_ist_now()
    if now.weekday() >= 5:  # Sat=5, Sun=6
        return "CLOSED"
    t = now.time()
    if datetime.time(9, 0) <= t < datetime.time(9, 15):
        return "PRE_OPEN"
    if datetime.time(9, 15) <= t <= datetime.time(15, 30):
        return "OPEN"
    return "CLOSED"

# ============================================
# Live Prices Dictionary
# ============================================
latest_prices: dict = {
    "nifty": None,
    "banknifty": None,
    "vix": None,
    "day_open": None,
    "day_open_date": None,
    "last_update": None,
}

# ============================================
# Candle & Indicator Data Stores
# ============================================
candle_store: list = []
indicator_data: dict = {"rsi": None, "ema9": None, "ema21": None, "vwap_approx": None}

signal_data = {
    "signal": "WAIT",
    "confidence": 0,
    "checklist": {},
    "orb_high": None,
    "orb_low": None,
    "note": "Waiting for data"
}
