import threading
import requests
import pyotp
import pandas as pd
from typing import Optional
from SmartApi import SmartConnect
from logzero import logger
import config

smart_api: Optional[SmartConnect] = None
session_lock = threading.Lock()
_scrip_master_df: Optional[pd.DataFrame] = None

def angel_login() -> bool:
    """Logs into Angel One SmartAPI using pyotp."""
    global smart_api
    if not all([config.ANGEL_API_KEY, config.ANGEL_CLIENT_ID, config.ANGEL_MPIN, config.ANGEL_TOTP_SECRET]):
        logger.error("Angel One credentials missing in env. Login skipped.")
        return False
    try:
        totp = pyotp.TOTP(config.ANGEL_TOTP_SECRET).now()
        obj = SmartConnect(api_key=config.ANGEL_API_KEY)
        data = obj.generateSession(config.ANGEL_CLIENT_ID, config.ANGEL_MPIN, totp)
        if data and data.get("status"):
            smart_api = obj
            logger.info("Angel One login successful")
            return True
        logger.error(f"Angel One login failed: {data}")
        return False
    except Exception as e:
        logger.error(f"Angel One login exception: {e}")
        return False

def refresh_scrip_master() -> bool:
    """Downloads scrip master from public Angel One URL."""
    global _scrip_master_df
    try:
        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data and isinstance(data, list) and len(data) > 0:
            _scrip_master_df = pd.DataFrame(data)
            logger.info(f"Scrip master loaded: {len(data)} instruments")
            return True
        logger.error("Scrip master returned empty data")
        return False
    except Exception as e:
        logger.error(f"refresh_scrip_master error: {e}")
        return False

def get_token(exchange: str, symbol: str) -> Optional[str]:
    """Looks up symbol token from cached scrip master with precise matching."""
    global _scrip_master_df
    if _scrip_master_df is None or _scrip_master_df.empty:
        if not refresh_scrip_master():
            return None
    try:
        # Normalize symbol names for better matching (e.g., SBIN-EQ vs SBIN)
        # 1. Try exact match on name
        match = _scrip_master_df[
            (_scrip_master_df["exch_seg"] == exchange) & 
            (_scrip_master_df["name"].str.upper() == symbol.upper())
        ]
        if not match.empty:
            return str(match.iloc[0]["token"])

        # 2. Try exact match on symbol
        match = _scrip_master_df[
            (_scrip_master_df["exch_seg"] == exchange) & 
            (_scrip_master_df["symbol"].str.upper() == symbol.upper())
        ]
        if not match.empty:
            return str(match.iloc[0]["token"])

        # 3. Special case for NIFTY/BANKNIFTY/VIX
        if symbol.upper() == "NIFTY":
            symbol = "Nifty 50"
        elif symbol.upper() == "BANKNIFTY":
            symbol = "Nifty Bank"
        
        match = _scrip_master_df[
            (_scrip_master_df["exch_seg"] == exchange) & 
            (_scrip_master_df["name"].str.contains(symbol, case=False, na=False))
        ]
        if not match.empty:
            return str(match.iloc[0]["token"])

    except Exception as e:
        logger.error(f"get_token error for {symbol}: {e}")
    return None

def get_ltp(exchange: str, symbol: str, token: Optional[str] = None) -> Optional[float]:
    """Fetches live Last Traded Price (LTP) for an asset."""
    global smart_api
    if smart_api is None:
        if not angel_login():
            return None
    # Always re-resolve token if it's not provided to avoid stale token issues
    token = get_token(exchange, symbol)
    if token is None:
        logger.warning(f"Could not resolve token for {exchange}:{symbol}")
        return None
    
    # Precise trading symbol from scrip master for the API call
    trading_symbol = symbol
    try:
        match = _scrip_master_df[_scrip_master_df["token"] == token]
        if not match.empty:
            trading_symbol = match.iloc[0]["symbol"]
    except: pass

    try:
        with session_lock:
            resp = smart_api.ltpData(exchange, trading_symbol, token)
        if resp and resp.get("status"):
            data = resp.get("data", {})
            if isinstance(data, dict):
                return data.get("ltp")
            elif isinstance(data, list) and len(data) > 0:
                return data[0].get("ltp")
        logger.error(f"get_ltp non-success response for {symbol}: {resp}")
        return None
    except Exception as e:
        logger.error(f"get_ltp error for {symbol}: {e}")
        angel_login()
        return None

def fetch_option_chain(symbol: str, exchange: str = "NFO") -> Optional[dict]:
    """Fetches full option chain data for a symbol."""
    global smart_api
    if smart_api is None:
        if not angel_login():
            return None
    try:
        # In a real SmartAPI scenario, you'd use getOptionChain or similar
        # For now, we simulate with a more generic approach if specific method is missing
        params = {
            "trading_symbol": symbol,
            "exchange": exchange
        }
        # Note: SmartAPI has specific methods for option chain, this is a placeholder
        # that would be replaced with actual obj.searchScrip or obj.getOptionChain
        with session_lock:
            # This is a conceptual call; real SmartAPI might use different method
            # We'll use a try-except to handle specific API variations
            try:
                data = smart_api.searchScrip(exchange, symbol)
                return data
            except:
                return None
    except Exception as e:
        logger.error(f"fetch_option_chain error: {e}")
        return None

def fetch_todays_candles() -> Optional[list]:
    """Fetches 1-minute historical candles for NIFTY from 9:15 AM to now.

    Handles Angel One rate limit ("Access denied because of exceeding access rate")
    by returning None gracefully — the poller will retry on its next cycle.
    Also retries once after a short sleep, since rate limits are transient.
    """
    global smart_api
    if smart_api is None:
        if not angel_login():
            return None
    token = get_token("NSE", config.NIFTY_SYMBOL)
    if token is None:
        logger.error("Could not resolve NIFTY token for candle fetch")
        return None

    import time as _time
    last_exc = None
    for attempt in range(2):  # 1 retry
        try:
            now = config.get_ist_now()
            from_dt = now.replace(hour=9, minute=15, second=0, microsecond=0)
            params = {
                "exchange": "NSE",
                "symboltoken": token,
                "interval": "ONE_MINUTE",
                "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
                "todate": now.strftime("%Y-%m-%d %H:%M"),
            }
            with session_lock:
                resp = smart_api.getCandleData(params)
            if resp and resp.get("status") and resp.get("data"):
                candles = []
                for row in resp["data"]:
                    candles.append({
                        "time": row[0], "open": row[1], "high": row[2],
                        "low": row[3], "close": row[4],
                    })
                return candles

            # Detect Angel One rate-limit message (returned as error string, not exception)
            err_msg = ""
            if isinstance(resp, dict):
                err_msg = str(resp.get("message", "")) + str(resp.get("errorcode", ""))
            if "exceeding access rate" in err_msg.lower() or "access rate" in err_msg.lower():
                logger.warning(f"⏳ Angel One rate limit on candles (attempt {attempt+1}/2). Will retry next cycle.")
                if attempt == 0:
                    _time.sleep(5)  # short backoff before retry
                    continue
                return None  # give up this cycle — don't spam
            logger.error(f"fetch_todays_candles non-success: {resp}")
            return None
        except Exception as e:
            last_exc = e
            err_str = str(e).lower()
            if "exceeding access rate" in err_str or "access rate" in err_str:
                logger.warning(f"⏳ Angel One rate limit on candles (attempt {attempt+1}/2): {e}")
                if attempt == 0:
                    _time.sleep(5)
                    continue
                return None  # give up this cycle — don't re-login (would make it worse)
            # Other errors — log + try re-login
            logger.error(f"fetch_todays_candles error: {e}")
            angel_login()
            return None
    # All attempts exhausted
    if last_exc:
        logger.warning(f"fetch_todays_candles gave up after retries: {last_exc}")
    return None
