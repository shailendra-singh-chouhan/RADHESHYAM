"""
Stocks module: Real live price data for HDFC, SBI, PNB, YES, INFY
Integrates with Angel One API for NSE stock quotes.
Plus: NIFTY / BANKNIFTY spot price polling for dashboard & trading.
"""

import time
import threading
from typing import Optional, Dict
from logzero import logger

import config
import angel_client
import strategy  # to update shared_state["spot"]


# Stock token mappings (NSE symbols)
STOCK_SYMBOLS = {
    "HDFC": "HDFCBANK-EQ",
    "SBI": "SBIN-EQ",
    "PNB": "PNB-EQ",
    "YES": "YESBANK-EQ",
    "INFY": "INFY-EQ",
}

# Index symbols for spot price
INDEX_SYMBOLS = {
    "NIFTY": ("NSE", "NIFTY 50"),
    "BANKNIFTY": ("NSE", "BANKNIFTY"),
}

# Global stock prices dictionary
stock_prices = {
    "HDFC": {"ltp": None, "open": None, "high": None, "low": None, "last_update": None},
    "SBI": {"ltp": None, "open": None, "high": None, "low": None, "last_update": None},
    "PNB": {"ltp": None, "open": None, "high": None, "low": None, "last_update": None},
    "YES": {"ltp": None, "open": None, "high": None, "low": None, "last_update": None},
    "INFY": {"ltp": None, "open": None, "high": None, "low": None, "last_update": None},
}


def fetch_stock_ohlc(stock_name: str) -> Optional[Dict]:
    """Fetch live price for a stock (OHLC currently fallback to LTP)."""
    if stock_name not in STOCK_SYMBOLS:
        logger.warning(f"Unknown stock: {stock_name}")
        return None

    try:
        symbol = STOCK_SYMBOLS[stock_name]
        client = angel_client.get_angel_client()
        ltp = client.get_ltp("NSE", symbol)
        if ltp:
            return {
                "ltp": ltp,
                "open": ltp,
                "high": ltp,
                "low": ltp,
            }
        return None
    except Exception as e:
        logger.error(f"Failed to fetch price for {stock_name}: {e}")
        return None


def fetch_index_spot(index_name: str) -> Optional[float]:
    """Fetch spot price for NIFTY / BANKNIFTY via Angel One."""
    if index_name not in INDEX_SYMBOLS:
        return None
    exchange, symbol = INDEX_SYMBOLS[index_name]
    try:
        client = angel_client.get_angel_client()
        ltp = client.get_ltp(exchange, symbol)
        if ltp and ltp > 0:
            logger.info(f"{index_name} spot: {ltp}")
            return ltp
        return None
    except Exception as e:
        logger.error(f"Failed to fetch {index_name} spot: {e}")
        return None


def stock_price_poller() -> None:
    """Background thread: refresh stock prices + index spot every 15 seconds."""
    while True:
        try:
            market_status = config.get_market_status() if hasattr(config, "get_market_status") else "OPEN"
            if market_status in ("OPEN", "PRE_OPEN"):
                # 1. Fetch individual stocks
                for stock_name in STOCK_SYMBOLS.keys():
                    ohlc = fetch_stock_ohlc(stock_name)
                    if ohlc:
                        stock_prices[stock_name]["ltp"] = ohlc.get("ltp")
                        stock_prices[stock_name]["open"] = ohlc.get("open")
                        stock_prices[stock_name]["high"] = ohlc.get("high")
                        stock_prices[stock_name]["low"] = ohlc.get("low")
                        stock_prices[stock_name]["last_update"] = config.get_ist_now().isoformat() if hasattr(config, "get_ist_now") else None

                # 2. Fetch NIFTY spot → update shared_state + state_manager
                nifty_spot = fetch_index_spot("NIFTY")
                if nifty_spot:
                    strategy.shared_state["spot"] = nifty_spot
                    strategy.shared_state["last_updated"] = config.get_ist_now().isoformat() if hasattr(config, "get_ist_now") else None
                    config.state_manager.set_state("latest_prices", {"nifty": nifty_spot})
                    logger.info(f"shared_state['spot'] updated: {nifty_spot}")

                # 3. Fetch BANKNIFTY spot → update state_manager
                banknifty_spot = fetch_index_spot("BANKNIFTY")
                if banknifty_spot:
                    config.state_manager.set_state("latest_prices", {"banknifty": banknifty_spot})
        except Exception as e:
            logger.error(f"stock_price_poller error: {e}")

        time.sleep(15)


_stock_poller_started = False

def start_stock_price_poller() -> None:
    """Start background thread for stock price + index spot updates."""
    global _stock_poller_started
    if _stock_poller_started:
        return
    _stock_poller_started = True
    threading.Thread(target=stock_price_poller, daemon=True).start()
    logger.info("Stock + Index spot price poller started.")


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
    return {name: stock_prices[name] for name in STOCK_SYMBOLS.keys()}
