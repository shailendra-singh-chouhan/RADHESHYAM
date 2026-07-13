"""
Stock Price Poller — HDFC, SBI, PNB, YES, INFY
"""

import time
import threading
import logging
from datetime import datetime
from angel_client import get_ltp
import config  # Dynamic global state integration

logger = logging.getLogger(__name__)

STOCKS = {
    "HDFC": {"exchange": "NSE", "symbol": "HDFCBANK"},
    "SBI": {"exchange": "NSE", "symbol": "SBIN"},
    "PNB": {"exchange": "NSE", "symbol": "PNB"},
    "YES": {"exchange": "NSE", "symbol": "YESBANK"},
    "INFY": {"exchange": "NSE", "symbol": "INFY"},
}

_running = False


def _poll_stocks(shared_state_ref):
    """Fetch stock prices and update shared state."""
    # Ensure nested dictionary structure exists to prevent KeyError
    if "stocks" not in shared_state_ref:
        shared_state_ref["stocks"] = {}

    for name, cfg in STOCKS.items():
        try:
            val = get_ltp(cfg["exchange"], cfg["symbol"])
            if val and val > 0:
                shared_state_ref["stocks"][name] = {
                    "ltp": val,
                    "open": None,
                    "high": None,
                    "low": None,
                    "last_update": datetime.now().isoformat(),
                }
        except Exception as e:
            logger.error(f"Stock {name} error: {e}")


def stock_poller_loop(shared_state_ref):
    """Background thread for stock polling."""
    while _running:
        try:
            _poll_stocks(shared_state_ref)
        except Exception as e:
            logger.error(f"Stock poller error: {e}")
        time.sleep(30)


def start_stock_poller(shared_state_ref=None):
    """Start stock poller thread."""
    global _running
    if _running:
        return
    _running = True
    
    # FIX: Fallback to global config state engine if no explicit reference is passed
    state = shared_state_ref if shared_state_ref is not None else config.state_manager.get_state()
    
    t = threading.Thread(target=stock_poller_loop, args=(state,), daemon=True, name="stock_poller")
    t.start()
    logger.info("Stock poller started")


def stop_stock_poller():
    """Stop stock poller thread."""
    global _running
    _running = False
    logger.info("Stock poller stopped")
