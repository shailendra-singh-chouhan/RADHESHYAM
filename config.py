"""
GOAT PRO — Configuration & Symbols
"""

import os

# ─── Safety Toggle ──────────────────────────────────────────────────
AUTO_TRADE_ENABLED = os.getenv("AUTO_TRADE_ENABLED", "false").lower() == "true"

# ─── TEMPORARY: Disable Angel One login until credentials are updated ───
ANGEL_LOGIN_DISABLED = True  # Set to False after updating credentials

# ─── Symbol Configuration ───────────────────────────────────────────
SYMBOLS = {
    "nifty": {"exchange": "NFO", "symbol": "NIFTY26JULFUT"},
    "banknifty": {"exchange": "NFO", "symbol": "BANKNIFTY26JULFUT"},
    "finnifty": {"exchange": "NFO", "symbol": "FINNIFTY26JULFUT"},
    "sensex": {"exchange": "BSE", "symbol": "SENSEX"},
    "crudeoil": {"exchange": "MCX", "symbol": "CRUDEOIL"},
    "gold": {"exchange": "MCX", "symbol": "GOLD"},
    "silver": {"exchange": "MCX", "symbol": "SILVER"},
    "usdinr": {"exchange": "CDS", "symbol": "USDINR"},
    "midcap": {"exchange": "NSE", "symbol": "NIFTY MIDCAP 100"},
    "vix": {"exchange": "NSE", "symbol": "INDIA VIX"},
}

# ─── Poller Intervals ───────────────────────────────────────────────
PRICE_POLL_INTERVAL = 5
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
