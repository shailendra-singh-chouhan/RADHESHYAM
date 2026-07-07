import threading
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
    """Downloads full scrip master and caches it as DataFrame."""
    global smart_api, _scrip_master_df
    if smart_api is None:
        if not angel_login():
            return False
    try:
        with session_lock:
            data = smart_api.getScripMaster()
        if data and isinstance(data, list) and len(data) > 0:
            _scrip_master_df = pd.DataFrame(data)
            logger.info(f"Scrip master loaded: {len(data)} instruments")
            return True
        logger.error("Scrip master returned empty/invalid data")
        return False
    except Exception as e:
        logger.error(f"refresh_scrip_master error: {e}")
        return False

def get_token(exchange: str, symbol: str) -> Optional[str]:
    """Looks up symbol token from cached scrip master."""
    global _scrip_master_df
    if _scrip_master_df is None or _scrip_master_df.empty:
        if not refresh_scrip_master():
            return None
    try:
        match = _scrip_master_df[
            (_scrip_master_df["exch_seg"] == exchange) & 
            (_scrip_master_df["symbol"] == symbol)
        ]
        if not match.empty:
            return str(match.iloc[0]["token"])
        # Try partial match for indices
        match = _scrip_master_df[
            (_scrip_master_df["exch_seg"] == exchange) & 
            (_scrip_master_df["symbol"].str.contains(symbol, case=False, na=False))
        ]
        if not match.empty:
            return str(match.iloc[0]["token"])
    except Exception as e:
        logger.error(f"get_token error: {e}")
    return None

def get_ltp(exchange: str, symbol: str, token: Optional[str] = None) -> Optional[float]:
    """Fetches live Last Traded Price (LTP) for an asset."""
    global smart_api
    if smart_api is None:
        if not angel_login():
            return None

    # Auto-resolve token if not provided
    if token is None:
        token = get_token(exchange, symbol)
        if token is None:
            logger.warning(f"Could not resolve token for {exchange}:{symbol}")
            return None

    try:
        with session_lock:
            resp = smart_api.ltpData(exchange, symbol, token)
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

def fetch_todays_candles() -> Optional[list]:
    """Fetches 1-minute historical candles for NIFTY from 9:15 AM to now."""
    global smart_api
    if smart_api is None:
        if not angel_login():
            return None

    token = get_token("NSE", config.NIFTY_SYMBOL)
    if token is None:
        logger.error("Could not resolve NIFTY token for candle fetch")
        return None

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
        logger.error(f"fetch_todays_candles non-success: {resp}")
        return None
    except Exception as e:
        logger.error(f"fetch_todays_candles error: {e}")
        angel_login()
        return None
