"""
GOAT PRO — Configuration & Symbols
"""

import os
import datetime
import pytz

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

# Individual symbol exports expected by strategy.py
NIFTY_SYMBOL = SYMBOLS["nifty"]["symbol"]
BANKNIFTY_SYMBOL = SYMBOLS["banknifty"]["symbol"]
FINNIFTY_SYMBOL = SYMBOLS["finnifty"]["symbol"]
SENSEX_SYMBOL = SYMBOLS["sensex"]["symbol"]
CRUDEOIL_SYMBOL = SYMBOLS["crudeoil"]["symbol"]
GOLD_SYMBOL = SYMBOLS["gold"]["symbol"]
SILVER_SYMBOL = SYMBOLS["silver"]["symbol"]
USDINR_SYMBOL = SYMBOLS["usdinr"]["symbol"]
MIDCAP_SYMBOL = SYMBOLS["midcap"]["symbol"]
VIX_SYMBOL = SYMBOLS["vix"]["symbol"]

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

# ─── Time & Market Status Utilities ──────────────────────────────────
def get_ist_now():
    """Returns the current execution time localized to Indian Standard Time (IST)."""
    tz = pytz.timezone("Asia/Kolkata")
    return datetime.datetime.now(tz)

def get_market_status():
    """Forces an 'OPEN' status during this troubleshooting phase so your 
    background threads run 24/7 and actively fetch the Yahoo Finance fallback data."""
    return "OPEN"

# ─── State Management Engine ─────────────────────────────────────────
class StateManager:
    def __init__(self):
        self.state = {
            "latest_prices": {
                "day_open_date": "", 
                "last_update": "",
                "nifty": 0,
                "banknifty": 0,
                "sensex": 0,
                "vix": 0
            },
            "indicator_data": {},
            "signal_data": {},
            "candle_store": [],
            "oi_data": {"source": "FALLBACK_SPOT_ONLY"},
            "greeks": {"source": "BS_APPROX"},
            "greeks_data": {},
            "global": {"source": "YAHOO_FINANCE"},
            "institutional_stats": {"status": "Live"},
            "market_alerts": [],
            "active_trade": None
        }
        self.last_data_update_time = None

    @property
    def latest_prices(self):
        return self.state.get("latest_prices", {})

    @property
    def indicator_data(self):
        return self.state.get("indicator_data", {})

    @property
    def oi_data(self):
        return self.state.get("oi_data", {})

    @property
    def market_alerts(self):
        return self.state.get("market_alerts", [])

    def get_state(self):
        return self.state

    def set_state(self, key, value):
        self.state[key] = value

    def update_state(self, key, updates, allow_none_overwrite=False):
        if key not in self.state or self.state[key] is None:
            self.state[key] = {}
        
        if isinstance(updates, dict):
            for k, v in updates.items():
                if v is not None or allow_none_overwrite:
                    self.state[key][k] = v
        else:
            self.state[key] = updates

# Single centralized instance to manage live engine memory across files
state_manager = StateManager()
