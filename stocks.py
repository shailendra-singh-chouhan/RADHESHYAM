"""
Stock Price Poller — HDFC, SBI, PNB, YES, INFY
"""

import time
import threading
import logging
from datetime import datetime
from angel_client import get_ltp

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
    import strategy
    state = shared_state_ref or strategy.shared_state
    t = threading.Thread(target=stock_poller_loop, args=(state,), daemon=True, name="stock_poller")
    t.start()
    logger.info("Stock poller started")


def stop_stock_poller():
    """Stop stock poller thread."""
    global _running
    _running = False
    logger.info("Stock poller stopped")
