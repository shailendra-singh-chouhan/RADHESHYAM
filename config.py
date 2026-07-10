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
ANGEL_PASSWORD    = os.getenv("ANGEL_PASSWORD", ANGEL_MPIN) # Fallback to MPIN
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
MAX_DAILY_LOSS = -2000
MAX_DAILY_TRADES = 5

# ────────────────────────────────────────────
# Auto-Trade Toggle (SAFETY-DEFAULT: OFF)
# ────────────────────────────────────────────
# When False: signals are still calculated and shown on dashboard, but NO trades
# are auto-opened/closed by the bot. User must press buttons manually.
# When True: the indicator_poller will auto-execute signals from the ORB strategy.
# Set to True ONLY after:
#   1. You've verified the dashboard works end-to-end (live data, indicators, signals)
#   2. You've tested paper trades manually and confirmed PnL is calculated correctly
#   3. You understand the 5-trades/day and -₹2000 risk limits
# You can override via env var on Render: AUTO_TRADE_ENABLED=true
AUTO_TRADE_ENABLED = os.getenv("AUTO_TRADE_ENABLED", "false").lower() == "true"

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
        self._institutional_stats: Dict[str, Any] = {
            "fii_long": 0, "fii_short": 0, "fii_net": 0,
            "dii_long": 0, "dii_short": 0, "dii_net": 0,
            "win_rate": 0.0, "total_trades": 0, "status": "Initializing..."
        }
        self._active_trade_context: Optional[Dict[str, Any]] = None # To store context of active trade for persistence
        self._last_state_save_time: Optional[datetime] = None
        self._last_data_update_time: Optional[datetime] = None # New: Timestamp for last successful data update

    def get_state(self, key: str) -> Any:
        with self._lock:
            return getattr(self, f"_{key}", None)

    def set_state(self, key: str, value: Any, allow_none_overwrite: bool = True):
        """Sets a state variable. If allow_none_overwrite is False, None values will not overwrite existing non-None values."""
        with self._lock:
            if not allow_none_overwrite and value is None and getattr(self, f"_{key}", None) is not None:
                return
            setattr(self, f"_{key}", value)

    def update_state(self, key: str, updates: Dict[str, Any], allow_none_overwrite: bool = True):
        """Updates a state dictionary. If allow_none_overwrite is False, None values in updates will not overwrite existing non-None values."""
        with self._lock:
            current_state = getattr(self, f"_{key}", {})
            for k, v in updates.items():
                if not allow_none_overwrite and v is None and current_state.get(k) is not None:
                    continue
                current_state[k] = v
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
    def institutional_stats(self) -> Dict[str, Any]:
        with self._lock:
            return self._institutional_stats.copy()

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

    @property
    def last_data_update_time(self) -> Optional[datetime]:
        with self._lock:
            return self._last_data_update_time

    @last_data_update_time.setter
    def last_data_update_time(self, value: Optional[datetime]):
        with self._lock:
            self._last_data_update_time = value

# Instantiate the State Manager
state_manager = StateManager()

# ────────────────────────────────────────────
# Time helpers
# ────────────────────────────────────────────
IST = pytz.timezone("Asia/Kolkata")

def get_ist_now() -> datetime:
    return datetime.now(IST)

def get_market_status() -> str:
    """
    Returns market status based on IST time.

    NSE Equity: 9:15 AM - 3:30 PM (Mon-Fri)
    NSE F&O:   9:15 AM - 3:30 PM (Mon-Fri)
    MCX:       9:00 AM - 11:30 PM (Mon-Fri) — extended for commodities

    Pre-open:  9:00 AM - 9:15 AM
    Closed:    All other times + Weekends + NSE holidays
    """
    now = get_ist_now()
    t = now.time()
    weekday = now.weekday()
    date_str = now.strftime("%Y-%m-%d")

    # Saturday = 5, Sunday = 6
    if weekday >= 5:
        return "CLOSED"

    # NSE Trading Holidays 2026 (major ones)
    # Add more as needed — ideally fetch from NSE API
    trading_holidays_2026 = [
        "2026-01-26",  # Republic Day
        "2026-03-17",  # Holi
        "2026-04-03",  # Good Friday
        "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
        "2026-05-01",  # Maharashtra Day
        "2026-08-15",  # Independence Day
        "2026-08-28",  # Ganesh Chaturthi
        "2026-10-02",  # Gandhi Jayanti
        "2026-10-20",  # Diwali (Laxmi Pujan)
        "2026-10-21",  # Diwali Balipratipada
        "2026-11-16",  # Gurunanak Jayanti
        "2026-12-25",  # Christmas
    ]

    if date_str in trading_holidays_2026:
        return "CLOSED_HOLIDAY"

    # Pre-open: 9:00 - 9:15
    if time(9, 0) <= t < time(9, 15):
        return "PRE_OPEN"

    # Market hours: 9:15 - 15:30 (NSE Equity + F&O)
    if time(9, 15) <= t <= time(15, 30):
        return "OPEN"

    # Post-market: 15:40 - 16:00 (for reference, still CLOSED for trading)
    if time(15, 40) <= t <= time(16, 0):
        return "POST_CLOSE"

    return "CLOSED"


def get_market_info() -> dict:
    """
    Returns detailed market info for dashboard display.
    Includes: status, next_open, time_until_open, time_until_close, session
    """
    now = get_ist_now()
    t = now.time()
    weekday = now.weekday()
    status = get_market_status()

    result = {
        "status": status,
        "current_time": now.strftime("%H:%M:%S"),
        "current_date": now.strftime("%Y-%m-%d"),
        "day": now.strftime("%A"),
        "is_weekend": weekday >= 5,
        "session": "—",
        "next_open": None,
        "time_until_open": None,
        "time_until_close": None,
        "market_message": "",
    }

    if status == "OPEN":
        result["session"] = "Regular"
        market_close = datetime.combine(now.date(), time(15, 30))
        time_left = market_close - now
        hours, remainder = divmod(int(time_left.total_seconds()), 3600)
        minutes = remainder // 60
        result["time_until_close"] = f"{hours}h {minutes}m"
        result["market_message"] = f"✅ Market OPEN — Closes in {hours}h {minutes}m"

    elif status == "PRE_OPEN":
        result["session"] = "Pre-Open"
        market_open = datetime.combine(now.date(), time(9, 15))
        time_left = market_open - now
        minutes = int(time_left.total_seconds() // 60)
        result["time_until_open"] = f"{minutes}m"
        result["market_message"] = f"⏳ Pre-Open — Market opens in {minutes}m"

    elif status == "POST_CLOSE":
        result["session"] = "Post-Close"
        result["market_message"] = "📊 Post-Close Session — No new orders"

    elif status == "CLOSED_HOLIDAY":
        result["market_message"] = "🏖️ Market Closed — Trading Holiday"
        # Find next trading day
        next_day = now + timedelta(days=1)
        while next_day.weekday() >= 5 or next_day.strftime("%Y-%m-%d") in [
            "2026-01-26", "2026-03-17", "2026-04-03", "2026-04-14",
            "2026-05-01", "2026-08-15", "2026-08-28", "2026-10-02",
            "2026-10-20", "2026-10-21", "2026-11-16", "2026-12-25"
        ]:
            next_day += timedelta(days=1)
        result["next_open"] = next_day.strftime("%Y-%m-%d (%A) 09:15 AM")

    elif status == "CLOSED":
        if weekday >= 5:
            # Weekend
            days_until_monday = 7 - weekday
            next_open = now + timedelta(days=days_until_monday)
            result["next_open"] = next_open.strftime("%Y-%m-%d (%A) 09:15 AM")
            result["market_message"] = f"🌴 Weekend — Opens {result['next_open']}"
        else:
            # After hours on weekday
            next_open = now + timedelta(days=1)
            # Skip weekend
            while next_open.weekday() >= 5:
                next_open += timedelta(days=1)
            result["next_open"] = next_open.strftime("%Y-%m-%d (%A) 09:15 AM")

            # Time until next open
            next_open_dt = datetime.combine(next_open.date(), time(9, 15))
            time_left = next_open_dt - now
            hours, remainder = divmod(int(time_left.total_seconds()), 3600)
            minutes = remainder // 60
            result["time_until_open"] = f"{hours}h {minutes}m"
            result["market_message"] = f"🔒 Market Closed — Opens in {hours}h {minutes}m ({result['next_open']})"

    return result
