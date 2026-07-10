"""
live_data_fetcher.py — Fetches ALL real market data

FIXED:
- Strike extraction uses scrip master 'strike' column directly
- VIX & Global markets cached (5-min TTL) to avoid Yahoo rate limits
- Global markets return None instead of 0.0 on failure
- Better error handling and logging
"""

import logging
import time
import re
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

import pandas as pd
import config
from angel_client import get_angel_client

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# CACHE — Simple TTL cache to avoid rate limiting
# ─────────────────────────────────────────────────────────────────

class _TTLCache:
    def __init__(self, ttl_seconds: int = 300):
        self._ttl = ttl_seconds
        self._store: Dict[str, tuple] = {}

    def get(self, key: str) -> Optional[Any]:
        if key not in self._store:
            return None
        value, expiry = self._store[key]
        if datetime.now() > expiry:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any):
        self._store[key] = (value, datetime.now() + timedelta(seconds=self._ttl))

_vix_cache = _TTLCache(ttl_seconds=300)      # 5 min
_global_cache = _TTLCache(ttl_seconds=300)   # 5 min

# ─────────────────────────────────────────────────────────────────
# 1. SPOT PRICES (Angel One — works on cloud)
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
    return {"value": None, "source": "UNAVAILABLE", "time": datetime.now().isoformat()}


def fetch_banknifty_spot() -> Dict[str, Any]:
    """Fetch BANKNIFTY spot from Angel One."""
    try:
        client = get_angel_client()
        ltp = client.get_ltp("NSE", "BANKNIFTY")
        if ltp and ltp > 0:
            return {"value": ltp, "source": "ANGEL_ONE", "time": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"BANKNIFTY spot error: {e}")
    return {"value": None, "source": "UNAVAILABLE", "time": datetime.now().isoformat()}


def fetch_sensex_spot() -> Dict[str, Any]:
    """Fetch SENSEX spot from Angel One."""
    try:
        client = get_angel_client()
        ltp = client.get_ltp("BSE", "SENSEX")
        if ltp and ltp > 0:
            return {"value": ltp, "source": "ANGEL_ONE", "time": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"SENSEX spot error: {e}")
    return {"value": None, "source": "UNAVAILABLE", "time": datetime.now().isoformat()}


# ─────────────────────────────────────────────────────────────────
# 2. OPTION CHAIN / OI (Angel One Scrip Master — FIXED)
# ─────────────────────────────────────────────────────────────────

def _extract_strike_from_symbol(symbol: str, index: str = "NIFTY") -> Optional[int]:
    """
    Robust strike extraction from symbol string.
    Returns None if extraction fails.
    """
    symbol = symbol.upper().strip()
    
    # Pattern 1: NIFTY24JUL24200CE -> 24200
    m = re.search(r'(\d{4,5})(?:CE|PE)$', symbol)
    if m:
        strike = int(m.group(1))
        step = 50 if index == "NIFTY" else 100
        if strike % step == 0:
            return strike
    
    # Pattern 2: NIFTY-24200-CE-24JUL -> 24200
    m = re.search(r'(?:^|[^\d])(\d{4,5})(?:[^\d]|$)', symbol)
    if m:
        strike = int(m.group(1))
        step = 50 if index == "NIFTY" else 100
        if strike % step == 0:
            return strike
    
    return None


def fetch_nse_option_chain(index: str = "NIFTY") -> Dict[str, Any]:
    """
    Fetch option chain data using Angel One scrip master.
    FIXED: Uses 'strike' column from scrip master directly.
           Falls back to robust symbol parsing with validation.
    """
    try:
        client = get_angel_client()

        # Get spot for ATM calculation
        spot = client.get_ltp("NSE", "NIFTY 50" if index == "NIFTY" else index)
        if not spot or spot <= 0:
            logger.warning(f"Invalid spot for {index}: {spot}")
            return _fallback_oi_data()

        # Get scrip master DataFrame
        df = client._scrip_master_df
        if df is None or df.empty:
            logger.warning("Scrip master empty")
            return _fallback_oi_data()

        # Filter for index options in NFO segment
        step = 50 if index == "NIFTY" else 100
        atm_strike = round(spot / step) * step
        
        # Look for index name in symbol/name columns
        index_name = "NIFTY" if index == "NIFTY" else index
        mask = (
            (df["exch_seg"] == "NFO") &
            (
                df.get("name", "").str.contains(index_name, case=False, na=False) |
                df.get("symbol", "").str.contains(index_name, case=False, na=False)
            )
        )
        opts = df[mask].copy()

        if opts.empty:
            logger.warning(f"No options found for {index}")
            return _fallback_oi_data()

        # Parse expiry and filter for nearest
        expiry_col = "expiry" if "expiry" in opts.columns else "expiry_date"
        if expiry_col in opts.columns:
            opts["_expiry_dt"] = pd.to_datetime(opts[expiry_col], errors="coerce", dayfirst=True)
        else:
            opts["_expiry_dt"] = pd.NaT

        opts = opts.dropna(subset=["_expiry_dt"])
        today = pd.Timestamp.today().normalize()
        opts = opts[opts["_expiry_dt"] >= today]

        if opts.empty:
            logger.warning("No valid expiry options found")
            return _fallback_oi_data()

        nearest_expiry = opts["_expiry_dt"].min().strftime("%d-%b-%Y")

        # Get nearest expiry options only
        opts = opts[opts["_expiry_dt"] == opts["_expiry_dt"].min()].copy()

        # Sort by strike distance from ATM
        strike_col = "strike" if "strike" in opts.columns else "strike_price"
        if strike_col in opts.columns:
            opts["_strike"] = pd.to_numeric(opts[strike_col], errors="coerce")
        else:
            # Fallback: extract from symbol
            opts["_strike"] = opts["symbol"].apply(
                lambda s: _extract_strike_from_symbol(str(s), index)
            )

        opts = opts.dropna(subset=["_strike"])
        opts["_dist"] = abs(opts["_strike"] - atm_strike)
        opts = opts.sort_values("_dist").head(14)  # ±7 strikes around ATM

        # Fetch LTP for each option
        strikes_data = []
        ce_oi = 0
        pe_oi = 0

        for _, row in opts.iterrows():
            try:
                token = str(row.get("token", ""))
                symbol = str(row.get("symbol", ""))
                strike = int(row["_strike"]) if pd.notna(row["_strike"]) else 0
                
                if strike <= 0:
                    continue

                ltp = client.get_ltp_by_token("NFO", symbol, token)
                
                if not ltp or ltp <= 0:
                    continue

                is_ce = "CE" in symbol.upper()
                
                # Proxy OI = LTP * 1000 (Angel One doesn't provide raw OI)
                proxy_oi = int(ltp * 1000)

                if is_ce:
                    ce_oi += proxy_oi
                else:
                    pe_oi += proxy_oi

                strikes_data.append({
                    "strike": strike,
                    "oi": proxy_oi,
                    "ce_oi": proxy_oi if is_ce else 0,
                    "pe_oi": proxy_oi if not is_ce else 0,
                    "ltp": round(ltp, 2),
                })

            except Exception as e:
                logger.debug(f"Option LTP fetch error for row: {e}")
                continue

        strikes_data.sort(key=lambda x: x["oi"], reverse=True)
        pcr = round(pe_oi / ce_oi, 2) if ce_oi > 0 else 0.0

        # Max pain: strike closest to spot
        max_pain = atm_strike
        if strikes_data:
            max_pain = min(strikes_data, key=lambda x: abs(x["strike"] - spot))["strike"]

        return {
            "call_oi": ce_oi,
            "put_oi": pe_oi,
            "pcr": pcr,
            "max_pain": max_pain,
            "expiry": nearest_expiry,
            "top_strikes": strikes_data[:7],
            "strike_count": len(strikes_data),
            "underlying": round(spot, 2),
            "source": "ANGEL_ONE_PROXIED",
            "note": "OI proxied from LTP (Angel One does not provide raw OI)",
            "time": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.error(f"Option chain error: {e}", exc_info=True)
        return _fallback_oi_data()


def _fallback_oi_data() -> Dict[str, Any]:
    """Return empty OI data when sources fail."""
    return {
        "call_oi": 0,
        "put_oi": 0,
        "pcr": 0.0,
        "max_pain": None,
        "expiry": None,
        "top_strikes": [],
        "strike_count": 0,
        "underlying": None,
        "source": "UNAVAILABLE",
        "note": "Data unavailable — market may be closed or source blocked",
        "time": datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────
# 3. INDIA VIX (Yahoo Finance — CACHED)
# ─────────────────────────────────────────────────────────────────

def fetch_india_vix() -> Dict[str, Any]:
    """Fetch India VIX from Yahoo Finance with 5-min cache."""
    cache_key = "india_vix"
    cached = _vix_cache.get(cache_key)
    if cached:
        return cached

    try:
        import yfinance as yf
        ticker = yf.Ticker("^INDIAVIX")
        hist = ticker.history(period="5d")
        if not hist.empty:
            close = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else close
            change = close - prev
            change_pct = (change / prev * 100) if prev > 0 else 0
            
            result = {
                "value": round(close, 2),
                "change": round(change, 2),
                "change_percent": round(change_pct, 2),
                "source": "YAHOO_FINANCE",
                "time": datetime.now().isoformat(),
            }
            _vix_cache.set(cache_key, result)
            return result
    except Exception as e:
        logger.warning(f"Yahoo VIX error: {e}")

    return {
        "value": None,
        "change": None,
        "change_percent": None,
        "source": "UNAVAILABLE",
        "time": datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────
# 4. GLOBAL MARKETS (Yahoo Finance — CACHED + DELAY)
# ─────────────────────────────────────────────────────────────────

def fetch_global_markets() -> Dict[str, Any]:
    """Fetch NASDAQ, DOW, KOSPI from Yahoo Finance with 5-min cache."""
    cache_key = "global_markets"
    cached = _global_cache.get(cache_key)
    if cached:
        return cached

    result = {
        "nasdaq": {"value": None, "change": None, "change_percent": None},
        "dow": {"value": None, "change": None, "change_percent": None},
        "kospi": {"value": None, "change": None, "change_percent": None},
        "sgx_nifty": {"value": None, "change": None, "change_percent": None},
        "source": "UNAVAILABLE",
        "time": datetime.now().isoformat(),
    }

    tickers = {
        "nasdaq": "^IXIC",
        "dow": "^DJI",
        "kospi": "^KS11",
        "sgx_nifty": "IN.NS",
    }

    try:
        import yfinance as yf

        for key, ticker in tickers.items():
            try:
                t = yf.Ticker(ticker)
                hist = t.history(period="2d")
                
                if not hist.empty:
                    close = float(hist["Close"].iloc[-1])
                    if len(hist) >= 2:
                        prev = float(hist["Close"].iloc[-2])
                    else:
                        prev = close
                    
                    change = close - prev
                    change_pct = (change / prev * 100) if prev > 0 else 0

                    result[key] = {
                        "value": round(close, 2),
                        "change": round(change, 2),
                        "change_percent": round(change_pct, 2),
                    }
                
                # Sleep 0.5s between requests to avoid rate limiting
                time.sleep(0.5)
                
            except Exception as e:
                logger.debug(f"yfinance {ticker} error: {e}")

        result["source"] = "YAHOO_FINANCE"
        _global_cache.set(cache_key, result)

    except ImportError:
        logger.warning("yfinance not installed")
    except Exception as e:
        logger.error(f"Global markets error: {e}")

    return result


# ─────────────────────────────────────────────────────────────────
# 5. FII / DII (NSE blocks cloud — return note)
# ─────────────────────────────────────────────────────────────────

def fetch_fii_dii() -> Dict[str, Any]:
    """
    FII/DII data from NSE India.
    NOTE: NSE API is blocked on cloud servers (Render/AWS).
    """
    return {
        "fii": {"buy": 0, "sell": 0, "net": 0},
        "dii": {"buy": 0, "sell": 0, "net": 0},
        "source": "NSE_BLOCKED_ON_CLOUD",
        "note": "NSE API blocks cloud IPs. Use local deployment or VPN for real FII/DII data.",
        "time": datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────
# 6. STOCK PRICES (Angel One — works on cloud)
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

    return {
        "ltp": None,
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "source": "UNAVAILABLE",
        "time": datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────
# 7. CANDLE DATA (Angel One — works on cloud)
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
