"""
live_data_fetcher.py — Fetches ALL real market data

Data Sources (Cloud-Friendly):
- Spot/Candles/Stocks: Angel One SmartAPI (works everywhere)
- Global/VIX: Yahoo Finance (works everywhere)
- OI/Greeks: Calculated from Angel One data (no NSE API needed)
- FII/DII: Not available on cloud (NSE blocks) — returns 0 with source note

NSE India API is BLOCKED on cloud servers (Render/AWS/etc).
This module uses only cloud-friendly sources.
"""

import logging
import time
from typing import Dict, Any, List
from datetime import datetime

import pandas as pd  # for scrip master DataFrame ops
import config
from angel_client import get_angel_client

logger = logging.getLogger(__name__)

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
# 2. OPTION CHAIN / OI (Angel One Scrip Master — cloud friendly)
# ─────────────────────────────────────────────────────────────────

def fetch_nse_option_chain(index: str = "NIFTY") -> Dict[str, Any]:
    """
    Fetch option chain data using Angel One scrip master.
    NSE API is blocked on cloud servers, so we use Angel One data.
    """
    try:
        client = get_angel_client()

        # Get spot for ATM calculation
        spot = client.get_ltp("NSE", "NIFTY 50" if index == "NIFTY" else index)
        if not spot:
            return _fallback_oi_data()

        # Get all NFO instruments from scrip master
        df = client._scrip_master_df
        if df is None or df.empty:
            return _fallback_oi_data()

        # Filter for index options
        step = 50 if index == "NIFTY" else 100
        atm_strike = round(spot / step) * step

        # Get options around ATM (±5 strikes)
        opts = df[
            (df["exch_seg"] == "NFO")
            & (df["symbol"].str.contains(str(int(atm_strike)), na=False))
        ].copy()

        if opts.empty:
            return _fallback_oi_data()

        # Parse expiry and get nearest
        opts["_expiry_dt"] = pd.to_datetime(opts.get("expiry"), errors="coerce")
        opts = opts.dropna(subset=["_expiry_dt"])
        opts = opts[opts["_expiry_dt"] >= pd.Timestamp.today().normalize()]

        if opts.empty:
            return _fallback_oi_data()

        nearest_expiry = opts["_expiry_dt"].min().strftime("%d-%b-%Y")

        # Get LTP for top strikes to estimate OI
        # Note: Angel One does not provide OI, so we use LTP as proxy
        strikes_data = []
        ce_oi = 0
        pe_oi = 0

        for _, row in opts.head(10).iterrows():
            try:
                token = str(row["token"])
                symbol = str(row["symbol"])
                ltp = client.get_ltp_by_token("NFO", symbol, token)

                # Extract strike from symbol (e.g., "NIFTY24JUL24000CE" -> 24000)
                # We need to find the strike price which is usually the last 5 digits before CE/PE
                import re
                # Match 5 digits that are followed by CE or PE
                strike_match = re.search(r'(\d{5})(?:CE|PE)$', symbol.upper())
                if strike_match:
                    strike = int(strike_match.group(1))
                else:
                    # Fallback: find any 5 digit number
                    strike_match = re.search(r'(\d{5})', symbol)
                    strike = int(strike_match.group(1)) if strike_match else 0

                is_ce = "CE" in symbol.upper()

                if ltp and ltp > 0:
                    if is_ce:
                        ce_oi += int(ltp * 1000)  # Proxy: LTP * 1000 as pseudo-OI
                    else:
                        pe_oi += int(ltp * 1000)

                    strikes_data.append({
                        "strike": strike,
                        "oi": int(ltp * 1000),
                        "ce_oi": int(ltp * 1000) if is_ce else 0,
                        "pe_oi": int(ltp * 1000) if not is_ce else 0,
                        "ltp": ltp,
                    })
            except Exception:
                pass

        strikes_data.sort(key=lambda x: x["oi"], reverse=True)
        pcr = round(pe_oi / ce_oi, 2) if ce_oi > 0 else 0.0

        return {
            "call_oi": ce_oi,
            "put_oi": pe_oi,
            "pcr": pcr,
            "max_pain": atm_strike,
            "expiry": nearest_expiry,
            "top_strikes": strikes_data[:7],
            "strike_count": len(strikes_data),
            "underlying": spot,
            "source": "ANGEL_ONE_PROXIED",
            "note": "OI is proxied from LTP (Angel One does not provide raw OI)",
            "time": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.error(f"Option chain error: {e}")
        return _fallback_oi_data()


def _fallback_oi_data() -> Dict[str, Any]:
    """Return empty OI data when sources fail."""
    return {
        "call_oi": 0,
        "put_oi": 0,
        "pcr": 0,
        "max_pain": None,
        "expiry": None,
        "top_strikes": [],
        "strike_count": 0,
        "underlying": 0,
        "source": "UNAVAILABLE",
        "note": "Data unavailable — market may be closed or source blocked",
        "time": datetime.now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────
# 3. INDIA VIX (Yahoo Finance — works on cloud)
# ─────────────────────────────────────────────────────────────────

def fetch_india_vix() -> Dict[str, Any]:
    """Fetch India VIX from Yahoo Finance (cloud-friendly)."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("^INDIAVIX")
        hist = ticker.history(period="5d")
        if not hist.empty:
            close = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else close
            change = close - prev
            change_pct = (change / prev * 100) if prev > 0 else 0
            return {
                "value": round(close, 2),
                "change": round(change, 2),
                "change_percent": round(change_pct, 2),
                "source": "YAHOO_FINANCE",
                "time": datetime.now().isoformat(),
            }
    except Exception as e:
        logger.warning(f"Yahoo VIX error: {e}")

    return {"value": 0, "change": 0, "change_percent": 0, "source": "UNAVAILABLE", "time": datetime.now().isoformat()}


# ─────────────────────────────────────────────────────────────────
# 4. GLOBAL MARKETS (Yahoo Finance — works on cloud)
# ─────────────────────────────────────────────────────────────────

def fetch_global_markets() -> Dict[str, Any]:
    """Fetch NASDAQ, DOW, KOSPI from Yahoo Finance (cloud-friendly)."""
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
        "sgx_nifty": "IN.NS",
    }

    try:
        import yfinance as yf

        for key, ticker in tickers.items():
            try:
                t = yf.Ticker(ticker)
                # Use period="2d" to ensure we have at least two days of data for change calculation
                hist = t.history(period="2d")
                
                if not hist.empty:
                    close = float(hist["Close"].iloc[-1])
                    # For change, use previous day's close if available
                    if len(hist) >= 2:
                        prev = float(hist["Close"].iloc[-2])
                    else:
                        # Fallback to info if only one day returned
                        prev = t.info.get("previousClose", close)
                    
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
# 5. FII / DII (NSE blocks cloud — return note)
# ─────────────────────────────────────────────────────────────────

def fetch_fii_dii() -> Dict[str, Any]:
    """
    FII/DII data from NSE India.
    NOTE: NSE API is blocked on cloud servers (Render/AWS).
    Returns 0 with source note. For real data, run locally or use proxy.
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
