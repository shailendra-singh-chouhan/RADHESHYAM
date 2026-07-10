"""
live_data_fetcher.py — Fetches ALL real market data from live sources

Data Sources:
- Spot: Angel One SmartAPI (NIFTY, BANKNIFTY, SENSEX)
- OI/Option Chain: NSE India API (with session cookies)
- VIX: NSE India / Yahoo Finance (^INDIAVIX)
- Global: Yahoo Finance (NASDAQ, DOW, KOSPI, SGX Nifty)
- FII/DII: NSE India (fii-dii endpoint)
- Stocks: Angel One (HDFC, SBI, PNB, YES, INFY)
- Candles: Angel One getCandleData

All functions return real data with "source" field indicating live/fallback.
"""

import logging
import time
import requests
from typing import Optional, Dict, Any, List
from datetime import datetime

import config
from angel_client import get_angel_client

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# NSE SESSION (shared for all NSE API calls)
# ─────────────────────────────────────────────────────────────────

_nse_session: Optional[requests.Session] = None
_nse_session_time: float = 0


def _get_nse_session() -> requests.Session:
    """Get or create NSE session with proper headers and cookies."""
    global _nse_session, _nse_session_time

    if _nse_session is not None and (time.time() - _nse_session_time < 300):
        return _nse_session

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    })

    try:
        # Visit homepage to get cookies
        resp = session.get("https://www.nseindia.com", timeout=10)
        logger.info(f"NSE session initialized: {resp.status_code}")
    except Exception as e:
        logger.warning(f"NSE session init warning: {e}")

    _nse_session = session
    _nse_session_time = time.time()
    return session


# ─────────────────────────────────────────────────────────────────
# 1. SPOT PRICES (Angel One — already working)
# ─────────────────────────────────────────────────────────────────

def fetch_nifty_spot() -> Dict[str, Any]:
    """Fetch NIFTY 50 spot from Angel One."""
    try:
        client = get_angel_client()
        ltp = client.get_ltp("NSE", "NIFTY 50")
        if ltp and ltp > 0:
            return {"value": ltp, "source": "ANGEL_ONE", "time": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"NIFTY spot error: {e}")
    return {"value": 0, "source": "UNAVAILABLE", "time": datetime.now().isoformat()}


def fetch_banknifty_spot() -> Dict[str, Any]:
    """Fetch BANKNIFTY spot from Angel One."""
    try:
        client = get_angel_client()
        ltp = client.get_ltp("NSE", "BANKNIFTY")
        if ltp and ltp > 0:
            return {"value": ltp, "source": "ANGEL_ONE", "time": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"BANKNIFTY spot error: {e}")
    return {"value": 0, "source": "UNAVAILABLE", "time": datetime.now().isoformat()}


def fetch_sensex_spot() -> Dict[str, Any]:
    """Fetch SENSEX spot from Angel One."""
    try:
        client = get_angel_client()
        ltp = client.get_ltp("BSE", "SENSEX")
        if ltp and ltp > 0:
            return {"value": ltp, "source": "ANGEL_ONE", "time": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"SENSEX spot error: {e}")
    return {"value": 0, "source": "UNAVAILABLE", "time": datetime.now().isoformat()}


# ─────────────────────────────────────────────────────────────────
# 2. OPTION CHAIN / OI DATA (NSE India)
# ─────────────────────────────────────────────────────────────────

def fetch_nse_option_chain(index: str = "NIFTY") -> Dict[str, Any]:
    """
    Fetch real option chain from NSE India.
    Returns: call_oi, put_oi, pcr, max_pain, top_strikes, expiry, underlying
    """
    try:
        session = _get_nse_session()
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={index}"
        resp = session.get(url, timeout=15)

        if resp.status_code != 200:
            logger.warning(f"NSE option chain HTTP {resp.status_code}")
            return _fallback_oi_data()

        data = resp.json()
        records = data.get("records", {})
        underlying = records.get("underlyingValue", 0)
        expiry_dates = records.get("expiryDates", [])
        nearest_expiry = expiry_dates[0] if expiry_dates else None

        ce_oi = 0
        pe_oi = 0
        strikes_data = []

        for item in records.get("data", []):
            ce = item.get("CE", {})
            pe = item.get("PE", {})

            ce_oi += ce.get("openInterest", 0)
            pe_oi += pe.get("openInterest", 0)

            strike = item.get("strikePrice", 0)
            total_oi = ce.get("openInterest", 0) + pe.get("openInterest", 0)

            if total_oi > 0:
                strikes_data.append({
                    "strike": strike,
                    "oi": total_oi,
                    "ce_oi": ce.get("openInterest", 0),
                    "pe_oi": pe.get("openInterest", 0),
                    "ce_ltp": ce.get("lastPrice", 0),
                    "pe_ltp": pe.get("lastPrice", 0),
                    "ce_iv": ce.get("impliedVolatility", 0),
                    "pe_iv": pe.get("impliedVolatility", 0),
                    "ce_change_oi": ce.get("changeinOpenInterest", 0),
                    "pe_change_oi": pe.get("changeinOpenInterest", 0),
                })

        strikes_data.sort(key=lambda x: x["oi"], reverse=True)
        top_strikes = strikes_data[:10]

        pcr = round(pe_oi / ce_oi, 2) if ce_oi > 0 else 0.0

        # Max pain = strike with minimum total loss (simplified)
        max_pain = None
        if strikes_data and underlying > 0:
            max_pain = min(strikes_data, key=lambda x: abs(x["strike"] - underlying))["strike"]

        return {
            "call_oi": ce_oi,
            "put_oi": pe_oi,
            "pcr": pcr,
            "max_pain": max_pain,
            "expiry": nearest_expiry,
            "top_strikes": top_strikes,
            "strike_count": len(strikes_data),
            "underlying": underlying,
            "source": "NSE_LIVE",
            "time": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.error(f"NSE option chain error: {e}")
        return _fallback_oi_data()


def _fallback_oi_data() -> Dict[str, Any]:
    """Return fallback OI data when NSE is unreachable."""
    return {
        "call_oi": 0,
        "put_oi": 0,
        "pcr": 0,
        "max_pain": None,
        "expiry": None,
        "top_strikes": [],
        "strike_count": 0,
        "underlying": 0,
        "source": "FALLBACK",
        "time": datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────
# 3. INDIA VIX (NSE India / Yahoo Finance)
# ─────────────────────────────────────────────────────────────────

def fetch_india_vix() -> Dict[str, Any]:
    """Fetch India VIX from NSE or Yahoo Finance."""
    # Try NSE first
    try:
        session = _get_nse_session()
        url = "https://www.nseindia.com/api/allIndices"
        resp = session.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("data", []):
                if item.get("index") == "INDIA VIX":
                    return {
                        "value": float(item.get("last", 0)),
                        "change": float(item.get("variation", 0)),
                        "change_percent": float(item.get("percentChange", 0)),
                        "source": "NSE_LIVE",
                        "time": datetime.now().isoformat(),
                    }
    except Exception as e:
        logger.warning(f"NSE VIX error: {e}")

    # Fallback to Yahoo Finance
    try:
        import yfinance as yf
        ticker = yf.Ticker("^INDIAVIX")
        hist = ticker.history(period="1d")
        if not hist.empty:
            return {
                "value": round(float(hist["Close"].iloc[-1]), 2),
                "change": 0,
                "change_percent": 0,
                "source": "YAHOO_FINANCE",
                "time": datetime.now().isoformat(),
            }
    except Exception as e:
        logger.warning(f"Yahoo VIX error: {e}")

    return {"value": 0, "change": 0, "change_percent": 0, "source": "UNAVAILABLE", "time": datetime.now().isoformat()}


# ─────────────────────────────────────────────────────────────────
# 4. GLOBAL MARKETS (Yahoo Finance)
# ─────────────────────────────────────────────────────────────────

def fetch_global_markets() -> Dict[str, Any]:
    """Fetch NASDAQ, DOW, KOSPI, SGX Nifty from Yahoo Finance."""
    result = {
        "nasdaq": {"value": 0, "change": 0, "change_percent": 0},
        "dow": {"value": 0, "change": 0, "change_percent": 0},
        "kospi": {"value": 0, "change": 0, "change_percent": 0},
        "sgx_nifty": {"value": 0, "change": 0, "change_percent": 0},
        "source": "UNAVAILABLE",
        "time": datetime.now().isoformat(),
    }

    tickers = {
        "nasdaq": "^IXIC",
        "dow": "^DJI",
        "kospi": "^KS11",
        "sgx_nifty": "IN.NS",  # SGX Nifty proxy
    }

    try:
        import yfinance as yf

        for key, ticker in tickers.items():
            try:
                t = yf.Ticker(ticker)
                hist = t.history(period="2d")
                if not hist.empty and len(hist) >= 1:
                    close = float(hist["Close"].iloc[-1])
                    prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else close
                    change = close - prev
                    change_pct = (change / prev * 100) if prev > 0 else 0

                    result[key] = {
                        "value": round(close, 2),
                        "change": round(change, 2),
                        "change_percent": round(change_pct, 2),
                    }
            except Exception as e:
                logger.debug(f"yfinance {ticker} error: {e}")

        result["source"] = "YAHOO_FINANCE"

    except ImportError:
        logger.warning("yfinance not installed")
    except Exception as e:
        logger.error(f"Global markets error: {e}")

    return result


# ─────────────────────────────────────────────────────────────────
# 5. FII / DII DATA (NSE India)
# ─────────────────────────────────────────────────────────────────

def fetch_fii_dii() -> Dict[str, Any]:
    """Fetch FII/DII trading activity from NSE India."""
    result = {
        "fii": {"buy": 0, "sell": 0, "net": 0},
        "dii": {"buy": 0, "sell": 0, "net": 0},
        "source": "UNAVAILABLE",
        "time": datetime.now().isoformat(),
    }

    try:
        session = _get_nse_session()
        url = "https://www.nseindia.com/api/fii-dii"
        resp = session.get(url, timeout=10)

        if resp.status_code == 200:
            data = resp.json()

            # Parse FII data
            fii_data = data.get("FII", []) or data.get("fii", [])
            if fii_data and isinstance(fii_data, list) and len(fii_data) > 0:
                latest = fii_data[0]
                result["fii"] = {
                    "buy": float(latest.get("buyValue", 0) or 0),
                    "sell": float(latest.get("sellValue", 0) or 0),
                    "net": float(latest.get("netValue", 0) or 0),
                }

            # Parse DII data
            dii_data = data.get("DII", []) or data.get("dii", [])
            if dii_data and isinstance(dii_data, list) and len(dii_data) > 0:
                latest = dii_data[0]
                result["dii"] = {
                    "buy": float(latest.get("buyValue", 0) or 0),
                    "sell": float(latest.get("sellValue", 0) or 0),
                    "net": float(latest.get("netValue", 0) or 0),
                }

            result["source"] = "NSE_LIVE"

    except Exception as e:
        logger.error(f"FII/DII error: {e}")

    return result


# ─────────────────────────────────────────────────────────────────
# 6. STOCK PRICES (Angel One)
# ─────────────────────────────────────────────────────────────────

STOCK_SYMBOLS = {
    "HDFC": "HDFCBANK-EQ",
    "SBI": "SBIN-EQ",
    "PNB": "PNB-EQ",
    "YES": "YESBANK-EQ",
    "INFY": "INFY-EQ",
}


def fetch_stock_price(stock_name: str) -> Dict[str, Any]:
    """Fetch live price for a stock via Angel One."""
    if stock_name not in STOCK_SYMBOLS:
        return {"error": f"Unknown stock: {stock_name}"}

    try:
        symbol = STOCK_SYMBOLS[stock_name]
        client = get_angel_client()

        # Try OHLC first
        ohlc = client.get_ohlc("NSE", symbol)
        if ohlc:
            return {
                "ltp": ohlc.get("ltp"),
                "open": ohlc.get("open"),
                "high": ohlc.get("high"),
                "low": ohlc.get("low"),
                "close": ohlc.get("close"),
                "source": "ANGEL_ONE",
                "time": datetime.now().isoformat(),
            }

        # Fallback to LTP
        ltp = client.get_ltp("NSE", symbol)
        if ltp:
            return {
                "ltp": ltp,
                "open": ltp,
                "high": ltp,
                "low": ltp,
                "close": ltp,
                "source": "ANGEL_ONE_LTP",
                "time": datetime.now().isoformat(),
            }
    except Exception as e:
        logger.error(f"Stock {stock_name} error: {e}")

    return {"ltp": 0, "open": 0, "high": 0, "low": 0, "close": 0, "source": "UNAVAILABLE", "time": datetime.now().isoformat()}


# ─────────────────────────────────────────────────────────────────
# 7. CANDLE DATA (Angel One)
# ─────────────────────────────────────────────────────────────────

def fetch_candles(symbol: str = "NIFTY 50", exchange: str = "NSE",
                  interval: str = "FIFTEEN_MINUTE", days: int = 5) -> List[Dict]:
    """Fetch historical candle data from Angel One."""
    try:
        client = get_angel_client()
        candles = client.get_candle_data(exchange, symbol, interval, days)
        if candles and len(candles) > 0:
            return candles
    except Exception as e:
        logger.error(f"Candle fetch error: {e}")
    return []


# ─────────────────────────────────────────────────────────────────
# 8. MASTER FETCH — Get everything at once
# ─────────────────────────────────────────────────────────────────

def fetch_all_live_data() -> Dict[str, Any]:
    """
    Fetch ALL live data in one call.
    Returns complete market snapshot.
    """
    logger.info("Fetching all live data...")
    start = time.time()

    result = {
        "spot": {
            "nifty": fetch_nifty_spot(),
            "banknifty": fetch_banknifty_spot(),
            "sensex": fetch_sensex_spot(),
        },
        "oi_data": fetch_nse_option_chain("NIFTY"),
        "vix": fetch_india_vix(),
        "global": fetch_global_markets(),
        "fii_dii": fetch_fii_dii(),
        "stocks": {name: fetch_stock_price(name) for name in STOCK_SYMBOLS.keys()},
        "time": datetime.now().isoformat(),
        "fetch_time_ms": 0,
    }

    result["fetch_time_ms"] = round((time.time() - start) * 1000, 2)
    logger.info(f"All live data fetched in {result['fetch_time_ms']}ms")

    return result
