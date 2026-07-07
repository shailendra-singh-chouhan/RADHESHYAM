"""
Stocks module: Real live price data for HDFC, SBI, PNB, YES, INFY
Integrates with Angel One API for NSE stock quotes.
"""

import time
import threading
from typing import Optional, Dict
from logzero import logger

import config
import angel_client


# Stock token mappings (NSE symbols)
STOCK_TOKENS = {
    "HDFC": "3500",
    "SBI": "4963",
    "PNB": "3456",
    "YES": "1333",
    "INFY": "408065",
}

STOCK_SYMBOLS = {
    "HDFC": "HDFCBANK-EQ",
    "SBI": "SBIN-EQ",
    "PNB": "PNB-EQ",
    "YES": "YESBANK-EQ",
    "INFY": "INFY-EQ",
}

# Global stock prices dictionary
stock_prices = {
    "HDFC": {"ltp": None, "open": None, "high": None, "low": None, "last_update": None},
    "SBI": {"ltp": None, "open": None, "high": None, "low": None, "last_update": None},
    "PNB": {"ltp": None, "open": None, "high": None, "low": None, "last_update": None},
    "YES": {"ltp": None, "open": None, "high": None, "low": None, "last_update": None},
    "INFY": {"ltp": None, "open": None, "high": None, "low": None, "last_update": None},
}


def fetch_stock_ltp(stock_name: str) -> Optional[float]:
    """Fetch live LTP for a stock."""
    if stock_name not in STOCK_TOKENS:
        logger.warning(f"Unknown stock: {stock_name}")
        return None
    
    try:
        token = STOCK_TOKENS[stock_name]
        symbol = STOCK_SYMBOLS[stock_name]
        ltp = angel_client.get_ltp("NSE", symbol, token)
        return ltp
    except Exception as e:
        logger.error(f"Failed to fetch LTP for {stock_name}: {e}")
        return None


def stock_price_poller() -> None:
    """Background thread: refresh stock prices every 15 seconds during market hours."""
    while True:
        try:
            if config.get_market_status() in ("OPEN", "PRE_OPEN"):
                for stock_name in STOCK_TOKENS.keys():
                    ltp = fetch_stock_ltp(stock_name)
                    if ltp is not None:
                        stock_prices[stock_name]["ltp"] = ltp
                        stock_prices[stock_name]["last_update"] = config.get_ist_now().isoformat()
        except Exception as e:
            logger.error(f"stock_price_poller error: {e}")
        
        time.sleep(15)


_stock_poller_started = False

def start_stock_price_poller() -> None:
    """Start background thread for stock price updates."""
    global _stock_poller_started
    if _stock_poller_started:
        return
    _stock_poller_started = True
    threading.Thread(target=stock_price_poller, daemon=True).start()
    logger.info("Stock price poller started (HDFC, SBI, PNB, YES, INFY).")


def get_stock_data(stock_name: str) -> Dict:
    """Get current stock data."""
    if stock_name not in stock_prices:
        return {"error": f"Unknown stock: {stock_name}"}
    
    return {
        "stock": stock_name,
        "ltp": stock_prices[stock_name]["ltp"],
        "open": stock_prices[stock_name]["open"],
        "high": stock_prices[stock_name]["high"],
        "low": stock_prices[stock_name]["low"],
        "last_update": stock_prices[stock_name]["last_update"],
    }


def get_all_stock_data() -> Dict:
    """Get data for all tracked stocks."""
    return {name: stock_prices[name] for name in STOCK_TOKENS.keys()}
