import os
import pytz
import threading
from datetime import datetime, time
from typing import Optional, Dict, Any, List

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
USDINR_SYMBOL    = "USDINR"
MIDCAP_SYMBOL    = "MIDCPNIFTY"
# Global Indices
KOSPI_SYMBOL     = "^KS11"
DJI_SYMBOL       = "^DJI"
NASDAQ_SYMBOL    = "^IXIC"
FTSE_SYMBOL      = "^FTSE"

# ────────────────────────────────────────────
# Trading Configuration
# ────────────────────────────────────────────
GAP_THRESHOLD_PERCENT = float(os.getenv("GAP_THRESHOLD_PERCENT", "0.5")) # 0.5% gap threshold

# ────────────────────────────────────────────
# Thread-safe State Manager
# ────────────────────────────────────────────
class StateManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._latest_prices: Dict[str, Any] = {
            "nifty": None,
            "banknifty": None,
            "vix": None,
            "finnifty": None,
            "sensex": None,
            "crudeoil": None,
            "gold": None,
            "silver": None,
            "usdinr": None,
            "midcap": None,
            "kospi": None,
            "dji": None,
            "nasdaq": None,
            "ftse": None,
            "day_open": None,
            "day_open_date": "",
            "last_update": "",
        }
        self._oi_data: Dict[str, Any] = {
            "call_oi": 0,
            "put_oi": 0,
            "pcr": 0.0,
            "max_pain": None,
        }
        self._greeks_data: Dict[str, Any] = {
            "iv": None,
            "delta": None,
            "theta": None,
            "gamma": None,
            "vega": None,
        }
        self._news_feed: List[Any] = []
        self._market_alerts: List[Any] = []
        self._candle_store: List[Any] = []
        self._indicator_data: Dict[str, Any] = {
            "rsi": None,
            "ema9": None,
            "ema21": None,
            "vwap_approx": None,
            "macd": None,
            "supertrend": None,
        }
        self._signal_data: Dict[str, Any] = {
            "signal": "WAIT",
            "confidence": 0,
            "checklist": {},
            "orb_high": None,
            "orb_low": None,
            "note": "Initializing...",
        }
        self._active_trade_context: Optional[Dict[str, Any]] = None # To store context of active trade for persistence
        self._last_state_save_time: Optional[datetime] = None

    def get_state(self, key: str) -> Any:
        with self._lock:
            return getattr(self, f"_{key}", None)

    def set_state(self, key: str, value: Any):
        with self._lock:
            setattr(self, f"_{key}", value)

    def update_state(self, key: str, updates: Dict[str, Any]):
        with self._lock:
            current_state = getattr(self, f"_{key}", {})
            current_state.update(updates)
            setattr(self, f"_{key}", current_state)

    # Property accessors for convenience
    @property
    def latest_prices(self) -> Dict[str, Any]:
        with self._lock:
            return self._latest_prices.copy() # Return a copy to prevent external modification

    @property
    def oi_data(self) -> Dict[str, Any]:
        with self._lock:
            return self._oi_data.copy()

    @property
    def greeks_data(self) -> Dict[str, Any]:
        with self._lock:
            return self._greeks_data.copy()

    @property
    def news_feed(self) -> List[Any]:
        with self._lock:
            return self._news_feed.copy()

    @property
    def market_alerts(self) -> List[Any]:
        with self._lock:
            return self._market_alerts.copy()

    @property
    def candle_store(self) -> List[Any]:
        with self._lock:
            return self._candle_store.copy()

    @property
    def indicator_data(self) -> Dict[str, Any]:
        with self._lock:
            return self._indicator_data.copy()

    @property
    def signal_data(self) -> Dict[str, Any]:
        with self._lock:
            return self._signal_data.copy()

    @property
    def active_trade_context(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._active_trade_context.copy() if self._active_trade_context else None

    @active_trade_context.setter
    def active_trade_context(self, value: Optional[Dict[str, Any]]):
        with self._lock:
            self._active_trade_context = value

    @property
    def last_state_save_time(self) -> Optional[datetime]:
        with self._lock:
            return self._last_state_save_time

    @last_state_save_time.setter
    def last_state_save_time(self, value: Optional[datetime]):
        with self._lock:
            self._last_state_save_time = value

# Instantiate the State Manager
state_manager = StateManager()

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
    
    # Market hours (Equity + Commodity): 9:15 - 23:55
    if time(9, 15) <= t <= time(23, 55):
        return "OPEN"
    
    return "CLOSED"
