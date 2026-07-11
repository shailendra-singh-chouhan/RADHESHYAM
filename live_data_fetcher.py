"""
live_data_fetcher.py — Fetches ALL real market data

FIXED v3:
- NaN/inf from yfinance → None (JSON safe)
- Strike paisa normalization (÷100)
- yfinance candle fallback
- 10-min cache for VIX/Global
"""

import logging
import time
import re
import math
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

import pandas as pd
import config
from angel_client import get_angel_client

logger = logging.getLogger(__name__)


def _safe_float(val, default=None, ndigits: Optional[int] = None):
    """Convert value to float, replacing NaN/inf with default. JSON-safe."""
    try:
        if val is None:
            return default
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        if ndigits is not None:
            return round(f, ndigits)
        return f
    except (TypeError, ValueError):
        return default


class _TTLCache:
    def __init__(self, ttl_seconds: int = 600):
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

_vix_cache = _TTLCache(ttl_seconds=600)
_global_cache = _TTLCache(ttl_seconds=600)
_candles_cache = _TTLCache(ttl_seconds=60)


def fetch_nifty_spot() -> Dict[str, Any]:
    try:
        client = get_angel_client()
        ltp = client.get_ltp("NSE", "NIFTY 50")
        if ltp and ltp > 0:
            return {"value": _safe_float(ltp, ndigits=2), "source": "ANGEL_ONE", "time": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"NIFTY spot error: {e}")
    return {"value": None, "source": "UNAVAILABLE", "time": datetime.now().isoformat()}


def fetch_banknifty_spot() -> Dict[str, Any]:
    try:
        client = get_angel_client()
        ltp = client.get_ltp("NSE", "BANKNIFTY")
        if ltp and ltp > 0:
            return {"value": _safe_float(ltp, ndigits=2), "source": "ANGEL_ONE", "time": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"BANKNIFTY spot error: {e}")
    return {"value": None, "source": "UNAVAILABLE", "time": datetime.now().isoformat()}


def fetch_sensex_spot() -> Dict[str, Any]:
    try:
        client = get_angel_client()
        ltp = client.get_ltp("BSE", "SENSEX")
        if ltp and ltp > 0:
            return {"value": _safe_float(ltp, ndigits=2), "source": "ANGEL_ONE", "time": datetime.now().isoformat()}
    except Exception as e:
        logger.error(f"SENSEX spot error: {e}")
    return {"value": None, "source": "UNAVAILABLE", "time": datetime.now().isoformat()}


def _extract_strike_from_symbol(symbol: str, index: str = "NIFTY") -> Optional[int]:
    symbol = symbol.upper().strip()
    m = re.search(r'(\d{4,5})(?:CE|PE)$', symbol)
    if m:
        strike = int(m.group(1))
        step = 50 if index == "NIFTY" else 100
        if strike % step == 0:
            return strike
    m = re.search(r'(?:^|[^\d])(\d{4,5})(?:[^\d]|$)', symbol)
    if m:
        strike = int(m.group(1))
        step = 50 if index == "NIFTY" else 100
        if strike % step == 0:
            return strike
    return None


def _detect_ce_pe(row: pd.Series) -> Optional[bool]:
    symbol = str(row.get("symbol", "")).upper()
    for col in ["optiontype", "option_type", "opt_type"]:
        if col in row and pd.notna(row[col]):
            val = str(row[col]).upper().strip()
            if val == "CE":
                return True
            if val == "PE":
                return False
    for col in ["instrumenttype", "instrument_type"]:
        if col in row and pd.notna(row[col]):
            val = str(row[col]).upper().strip()
            if "CE" in val:
                return True
            if "PE" in val:
                return False
    if symbol.endswith("CE"):
        return True
    if symbol.endswith("PE"):
        return False
    if "CE" in symbol and "PE" not in symbol:
        return True
    if "PE" in symbol and "CE" not in symbol:
        return False
    return None


def fetch_nse_option_chain(index: str = "NIFTY") -> Dict[str, Any]:
    try:
        client = get_angel_client()
        spot = client.get_ltp("NSE", "NIFTY 50" if index == "NIFTY" else index)
        if not spot or spot <= 0:
            logger.warning(f"Invalid spot for {index}: {spot}")
            return _fallback_oi_data()

        df = client._scrip_master_df
        if df is None or df.empty:
            logger.warning("Scrip master empty")
            return _fallback_oi_data()

        step = 50 if index == "NIFTY" else 100
        atm_strike = round(spot / step) * step
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
        opts = opts[opts["_expiry_dt"] == opts["_expiry_dt"].min()].copy()

        strike_col = "strike" if "strike" in opts.columns else "strike_price"
        if strike_col in opts.columns:
            opts["_strike"] = pd.to_numeric(opts[strike_col], errors="coerce")
            mask_paisa = opts["_strike"] > 100000
            opts.loc[mask_paisa, "_strike"] = opts.loc[mask_paisa, "_strike"] / 100
        else:
            opts["_strike"] = opts["symbol"].apply(
                lambda s: _extract_strike_from_symbol(str(s), index)
            )

        opts = opts.dropna(subset=["_strike"])
        opts["_strike"] = opts["_strike"].astype(int)
        
        opts = opts[
            (opts["_strike"] > spot * 0.5) & 
            (opts["_strike"] < spot * 1.5)
        ]
        
        if opts.empty:
            logger.warning("No valid strikes near spot %s", spot)
            return _fallback_oi_data()

        opts["_dist"] = abs(opts["_strike"] - atm_strike)
        opts = opts.sort_values("_dist").head(20)

        strikes_data = []
        ce_oi = 0
        pe_oi = 0

        for _, row in opts.iterrows():
            try:
                token = str(row.get("token", ""))
                symbol = str(row.get("symbol", ""))
                strike = int(row["_strike"])
                
                if strike <= 0:
                    continue

                ltp = client.get_ltp_by_token("NFO", symbol, token)
                if not ltp or ltp <= 0:
                    continue

                is_ce = _detect_ce_pe(row)
                if is_ce is None:
                    continue

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
                    "ltp": _safe_float(ltp, ndigits=2),
                })

            except Exception as e:
                logger.debug(f"Option LTP fetch error: {e}")
                continue

        strikes_data.sort(key=lambda x: x["oi"], reverse=True)
        pcr = _safe_float(pe_oi / ce_oi, ndigits=2) if ce_oi > 0 else 0.0

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
            "underlying": _safe_float(spot, ndigits=2),
            "source": "ANGEL_ONE_PROXIED",
            "note": "OI proxied from LTP (Angel One does not provide raw OI)",
            "time": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.error(f"Option chain error: {e}", exc_info=True)
        return _fallback_oi_data()


def _fallback_oi_data() -> Dict[str, Any]:
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


def fetch_india_vix() -> Dict[str, Any]:
    cache_key = "india_vix"
    cached = _vix_cache.get(cache_key)
    if cached:
        return cached

    try:
        import yfinance as yf
        ticker = yf.Ticker("^INDIAVIX")
        hist = ticker.history(period="5d")
        if not hist.empty:
            close_raw = hist["Close"].iloc[-1]
            prev_raw = hist["Close"].iloc[-2] if len(hist) > 1 else close_raw
            
            close = _safe_float(close_raw, ndigits=2)
            prev = _safe_float(prev_raw, ndigits=2)
            
            if close is not None and prev is not None:
                change = _safe_float(close - prev, ndigits=2)
                change_pct = _safe_float((change / prev * 100) if prev > 0 else 0, ndigits=2)
                
                result = {
                    "value": close,
                    "change": change,
                    "change_percent": change_pct,
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


def fetch_global_markets() -> Dict[str, Any]:
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
                    close_raw = hist["Close"].iloc[-1]
                    prev_raw = hist["Close"].iloc[-2] if len(hist) >= 2 else close_raw
                    
                    close = _safe_float(close_raw, ndigits=2)
                    prev = _safe_float(prev_raw, ndigits=2)
                    
                    if close is not None and prev is not None:
                        change = _safe_float(close - prev, ndigits=2)
                        change_pct = _safe_float((change / prev * 100) if prev > 0 else 0, ndigits=2)

                        result[key] = {
                            "value": close,
                            "change": change,
                            "change_percent": change_pct,
                        }
                
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


def fetch_fii_dii() -> Dict[str, Any]:
    return {
        "fii": {"buy": 0, "sell": 0, "net": 0},
        "dii": {"buy": 0, "sell": 0, "net": 0},
        "source": "NSE_BLOCKED_ON_CLOUD",
        "note": "NSE API blocks cloud IPs. Use local deployment or VPN for real FII/DII data.",
        "time": datetime.now().isoformat(),
    }


STOCK_SYMBOLS = {
    "HDFC": "HDFCBANK-EQ",
    "SBI": "SBIN-EQ",
    "PNB": "PNB-EQ",
    "YES": "YESBANK-EQ",
    "INFY": "INFY-EQ",
}


def fetch_stock_price(stock_name: str) -> Dict[str, Any]:
    if stock_name not in STOCK_SYMBOLS:
        return {"error": f"Unknown stock: {stock_name}"}

    try:
        symbol = STOCK_SYMBOLS[stock_name]
        client = get_angel_client()

        ohlc = client.get_ohlc("NSE", symbol)
        if ohlc:
            return {
                "ltp": _safe_float(ohlc.get("ltp"), ndigits=2),
                "open": _safe_float(ohlc.get("open"), ndigits=2),
                "high": _safe_float(ohlc.get("high"), ndigits=2),
                "low": _safe_float(ohlc.get("low"), ndigits=2),
                "close": _safe_float(ohlc.get("close"), ndigits=2),
                "source": "ANGEL_ONE",
                "time": datetime.now().isoformat(),
            }

        ltp = client.get_ltp("NSE", symbol)
        if ltp:
            return {
                "ltp": _safe_float(ltp, ndigits=2),
                "open": _safe_float(ltp, ndigits=2),
                "high": _safe_float(ltp, ndigits=2),
                "low": _safe_float(ltp, ndigits=2),
                "close": _safe_float(ltp, ndigits=2),
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


def _fetch_candles_yfinance(symbol: str, interval: str, days: int) -> List[Dict]:
    try:
        import yfinance as yf
        
        ticker_map = {
            "NIFTY 50": "^NSEI",
            "NIFTY": "^NSEI",
            "BANKNIFTY": "^NSEBANK",
            "SENSEX": "^BSESN",
        }
        ticker = ticker_map.get(symbol, symbol)
        
        interval_map = {
            "ONE_MINUTE": "1m",
            "FIVE_MINUTE": "5m",
            "FIFTEEN_MINUTE": "15m",
            "THIRTY_MINUTE": "30m",
            "ONE_HOUR": "60m",
            "ONE_DAY": "1d",
        }
        yf_interval = interval_map.get(interval, "15m")
        
        t = yf.Ticker(ticker)
        hist = t.history(period=f"{days}d", interval=yf_interval)
        
        if hist.empty:
            logger.warning(f"yfinance returned empty for {ticker}")
            return []
        
        candles = []
        for timestamp, row in hist.iterrows():
            open_val = _safe_float(row.get("Open"), ndigits=2)
            if open_val is None:
                continue  # Skip NaN rows
            candles.append({
                "time": int(timestamp.timestamp() * 1000),
                "open": open_val,
                "high": _safe_float(row.get("High"), ndigits=2),
                "low": _safe_float(row.get("Low"), ndigits=2),
                "close": _safe_float(row.get("Close"), ndigits=2),
                "volume": int(row.get("Volume", 0)) if pd.notna(row.get("Volume")) else 0,
            })
        
        logger.info(f"yfinance fallback: fetched {len(candles)} candles for {ticker}")
        return candles
        
    except Exception as e:
        logger.error(f"yfinance candle fallback error: {e}")
        return []


def fetch_candles(symbol: str = "NIFTY 50", exchange: str = "NSE",
                  interval: str = "FIFTEEN_MINUTE", days: int = 5) -> List[Dict]:
    cache_key = f"candles_{symbol}_{interval}_{days}"
    cached = _candles_cache.get(cache_key)
    if cached:
        return cached

    try:
        client = get_angel_client()
        candles = client.get_candle_data(exchange, symbol, interval, days)
        if candles and len(candles) > 0:
            _candles_cache.set(cache_key, candles)
            return candles
    except Exception as e:
        logger.error(f"Angel One candle fetch failed: {e}")

    logger.info("Falling back to yfinance for candles")
    candles = _fetch_candles_yfinance(symbol, interval, days)
    if candles:
        _candles_cache.set(cache_key, candles)
    return candles


def fetch_all_live_data() -> Dict[str, Any]:
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
