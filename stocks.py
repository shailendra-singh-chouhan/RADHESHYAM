"""
Stock Price Poller — HDFC, SBI, PNB, YES, INFY
(Angel One removed — now uses Yahoo Finance directly)
"""
import time
import threading
import logging
from datetime import datetime

import yfinance as yf

from strategy import shared_state  # write directly into the SAME dict routes.py reads

logger = logging.getLogger(__name__)

# Yahoo Finance tickers (NSE stocks use the .NS suffix)
STOCKS = {
    "HDFC": "HDFCBANK.NS",
    "SBI": "SBIN.NS",
    "PNB": "PNB.NS",
    "YES": "YESBANK.NS",
    "INFY": "INFY.NS",
}

_running = False


def _get_yf_stock_ltp(ticker: str):
    """Fetch last price for a stock ticker from Yahoo Finance."""
    try:
        t = yf.Ticker(ticker)
        fast = t.fast_info
        val = getattr(fast, "last_price", None)
        if val and val > 0:
            return round(float(val), 2)
        hist = t.history(period="1d", interval="1m")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 2)
    except Exception as e:
        logger.error(f"yfinance stock LTP error for {ticker}: {e}")
    return None


def _poll_stocks():
    """Fetch stock prices and write into strategy.shared_state['stocks']."""
    if "stocks" not in shared_state:
        shared_state["stocks"] = {}

    for name, ticker in STOCKS.items():
        try:
            val = _get_yf_stock_ltp(ticker)
            if val:
                shared_state["stocks"][name] = {
                    "ltp": val,
                    "last_update": datetime.now().isoformat(),
                    "source": "YAHOO_FINANCE",
                }
        except Exception as e:
            logger.error(f"Stock {name} error: {e}")


def stock_poller_loop():
    """Background thread for stock polling."""
    while _running:
        try:
            _poll_stocks()
        except Exception as e:
            logger.error(f"Stock poller error: {e}")
        time.sleep(30)


def start_stock_poller():
    """Start stock poller thread. Writes directly into strategy.shared_state,
    the same dict routes.py reads from — this fixes a previous bug where
    stock data was written to a different, unused state object."""
    global _running
    if _running:
        return
    _running = True

    t = threading.Thread(target=stock_poller_loop, daemon=True, name="stock_poller")
    t.start()
    logger.info("Stock poller started (Yahoo Finance mode)")


def stop_stock_poller():
    """Stop stock poller thread."""
    global _running
    _running = False
    logger.info("Stock poller stopped")
