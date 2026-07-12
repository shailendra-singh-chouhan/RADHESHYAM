"""
Angel One SmartAPI Client — LOGIN DISABLED UNTIL CREDENTIALS UPDATED
"""

import os
import time
import logging
from SmartApi import SmartConnect
import pyotp

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# ⛔ LOGIN DISABLED — Set to False AFTER updating credentials
# ═══════════════════════════════════════════════════════════════════
LOGIN_DISABLED = True
# ═══════════════════════════════════════════════════════════════════

_client = None
_token_map = {}
_last_login_attempt = 0
_login_cooldown = 300


def get_angel_client():
    global _client, _last_login_attempt

    if LOGIN_DISABLED:
        return None

    if _client and _client.accessToken:
        return _client

    now = time.time()
    if now - _last_login_attempt < _login_cooldown:
        return None

    client_id = os.getenv("ANGEL_CLIENT_ID")
    api_key = os.getenv("ANGEL_API_KEY")
    totp_secret = os.getenv("ANGEL_TOTP_SECRET")
    mpin = os.getenv("ANGEL_MPIN", "123456")

    if not all([client_id, api_key, totp_secret]):
        _last_login_attempt = now
        return None

    totp = pyotp.TOTP(totp_secret)
    _last_login_attempt = now

    try:
        client = SmartConnect(api_key=api_key)
        data = client.generateSession(client_id, totp.now(), mpin)
        if data.get("status"):
            _client = client
            logger.info("Angel One login successful")
            return _client
        else:
            logger.error(f"Login failed: {data.get('message', data)}")
            return None
    except Exception as e:
        logger.error(f"Login error: {e}")
        return None


def get_token(exchange, symbol):
    cache_key = f"{exchange}:{symbol}"
    if cache_key in _token_map:
        return _token_map[cache_key]

    client = get_angel_client()
    if not client:
        return None

    try:
        search_str = symbol
        if exchange == "MCX":
            search_str = f"{symbol}26JULFUT"

        resp = client.searchScrip(search_str)
        if resp and resp.get("status") and resp.get("data"):
            for item in resp["data"]:
                if item.get("exchange") == exchange and symbol.upper() in item.get("symbol", "").upper():
                    token = item.get("symboltoken")
                    if token:
                        _token_map[cache_key] = token
                        return token

        fallbacks = {
            "NSE:NIFTY": "25843",
            "NFO:NIFTY26JULFUT": "25843",
            "NSE:BANKNIFTY": "26009",
            "NFO:BANKNIFTY26JULFUT": "26009",
            "NSE:FINNIFTY": "25844",
            "NFO:FINNIFTY26JULFUT": "25844",
            "BSE:SENSEX": "1",
        }
        if cache_key in fallbacks:
            _token_map[cache_key] = fallbacks[cache_key]
            return fallbacks[cache_key]

        return None
    except Exception as e:
        logger.error(f"Token error for {symbol}: {e}")
        return None


def get_ltp(exchange, symbol):
    client = get_angel_client()
    if not client:
        return None

    token = get_token(exchange, symbol)
    if not token:
        return None

    try:
        resp = client.ltpData(exchange, symbol, token)
        if resp and resp.get("status") and resp.get("data"):
            return float(resp["data"].get("ltp", 0))
        return None
    except Exception as e:
        logger.error(f"LTP error for {symbol}: {e}")
        return None


def get_candle_data(exchange, symbol, interval="ONE_MINUTE", days=1):
    client = get_angel_client()
    if not client:
        return None

    token = get_token(exchange, symbol)
    if not token:
        return None

    try:
        from datetime import datetime, timedelta
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days)

        params = {
            "exchange": exchange,
            "symboltoken": token,
            "interval": interval,
            "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
            "todate": to_date.strftime("%Y-%m-%d %H:%M"),
        }

        resp = client.getCandleData(params)
        if resp and resp.get("status") and resp.get("data"):
            return resp["data"]
        return None
    except Exception as e:
        logger.error(f"Candle error for {symbol}: {e}")
        return None


def get_options_contract_details(index, strike, option_type, expiry_date=None):
    client = get_angel_client()
    if not client:
        return None

    try:
        from datetime import datetime, timedelta

        if not expiry_date:
            today = datetime.now()
            days_until_thurs = (3 - today.weekday()) % 7
            if days_until_thurs == 0 and today.hour >= 15:
                days_until_thurs = 7
            expiry_date = today + timedelta(days=days_until_thurs)

        expiry_str = expiry_date.strftime("%d%b%Y").upper()
        symbol = f"{index}{expiry_str}{strike}{option_type}"
        token = get_token("NFO", symbol)

        return {
            "symbol": symbol,
            "strike": strike,
            "option_type": option_type,
            "expiry": expiry_date.strftime("%d-%b-%Y"),
            "token": token,
            "lot_size": 75 if index == "NIFTY" else 25,
        }
    except Exception as e:
        logger.error(f"Options error: {e}")
        return None
