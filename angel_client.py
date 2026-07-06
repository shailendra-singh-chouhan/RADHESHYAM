import threading
import pyotp
from typing import Optional
from SmartApi import SmartConnect
from logzero import logger
import config

smart_api: Optional[SmartConnect] = None
session_lock = threading.Lock()

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

def get_ltp(exchange: str, symbol: str, token: str) -> Optional[float]:
    """Fetches live Last Traded Price (LTP) for an asset."""
    global smart_api
    if smart_api is None:
        return None
    try:
        with session_lock:
            resp = smart_api.ltpData(exchange, symbol, token)
        if resp and resp.get("status"):
            return resp["data"]["ltp"]
        logger.error(f"get_ltp non-success response for {symbol}: {resp}")
        return None
    except Exception as e:
        logger.error(f"get_ltp error for {symbol}: {e}")
        return None

def fetch_todays_candles() -> Optional[list]:
    """Fetches 1-minute historical candles for NIFTY from 9:15 AM to now."""
    global smart_api
    if smart_api is None:
        return None
    try:
        now = config.get_ist_now()
        from_dt = now.replace(hour=9, minute=15, second=0, microsecond=0)
        params = {
            "exchange": "NSE",
            "symboltoken": config.NIFTY_TOKEN,
            "interval": "ONE_MINUTE",
            "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
            "todate": now.strftime("%Y-%m-%d %H:%M"),
        }
        resp = smart_api.getCandleData(params)
        if resp and resp.get("status") and resp.get("data"):
            candles = []
            for row in resp["data"]:
                candles.append({
                    "time": row[0], "open": row[1], "high": row[2],
                    "low": row[3], "close": row[4],
                })
            return candles
        return None
    except Exception as e:
        logger.error(f"fetch_todays_candles error: {e}")
        return None
