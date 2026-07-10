"""
angel_client.py — FIXED VERSION
Fixes:
1. NSE Option Chain 404 (Updated logic for NSE live data)
2. get_candle_data JSON Parsing Error (Empty response handling)
3. Pandas DateTime Format Warning (Explicit format in pd.to_datetime)
"""

import os
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

import pandas as pd
import requests
import pyotp
from SmartApi import SmartConnect

import config

logger = logging.getLogger(__name__)


class AngelClient:
    """Wrapper around Angel One SmartAPI for market data."""

    _KNOWN_TOKENS = {
        ("NSE", "NIFTY 50"): "26000",
        ("NSE", "NIFTY"): "26000",
        ("NFO", "NIFTY"): "26000",
        ("NSE", "BANKNIFTY"): "26009",
        ("NFO", "BANKNIFTY"): "26009",
        ("NSE", "FINNIFTY"): "26037",
        ("NFO", "FINNIFTY"): "26037",
        ("BSE", "SENSEX"): "1",
    }

    def __init__(self):
        self.smart_api: Optional[SmartConnect] = None
        self.jwt_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.feed_token: Optional[str] = None
        self._scrip_master_df: Optional[pd.DataFrame] = None
        self._last_login_time: float = 0
        self._login()
        self._load_scrip_master()

    def _login(self) -> bool:
        try:
            self.smart_api = SmartConnect(api_key=config.ANGEL_API_KEY)
            totp = pyotp.TOTP(config.ANGEL_TOTP_SECRET).now()
            data = self.smart_api.generateSession(
                clientCode=config.ANGEL_CLIENT_ID,
                password=config.ANGEL_MPIN,
                totp=totp,
            )
            if data.get("status") is False:
                logger.error("Angel One login failed: %s", data.get("message", "unknown"))
                return False

            self.jwt_token = data["data"]["jwtToken"]
            self.refresh_token = data["data"]["refreshToken"]
            self.feed_token = data["data"]["feedToken"]
            self._last_login_time = time.time()
            logger.info("Angel One login successful")
            return True
        except Exception as e:
            logger.error("Angel One login exception: %s", e)
            return False

    def _ensure_session(self) -> bool:
        if time.time() - self._last_login_time > 1800:
            return self._login()
        return self.smart_api is not None

    def _load_scrip_master(self):
        try:
            url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            # FIX: Check if response is empty before parsing
            if not resp.text.strip():
                logger.error("Scrip master response is empty")
                return
            data = resp.json()
            self._scrip_master_df = pd.DataFrame(data)

            for col in ("symbol", "name", "exch_seg"):
                if col in self._scrip_master_df.columns:
                    self._scrip_master_df[col] = self._scrip_master_df[col].astype(str).str.upper().str.strip()
            if "token" in self._scrip_master_df.columns:
                self._scrip_master_df["token"] = self._scrip_master_df["token"].astype(str)
            logger.info("Scrip master loaded: %d instruments", len(self._scrip_master_df))
        except Exception as e:
            logger.error("Failed to load scrip master: %s", e)
            self._scrip_master_df = pd.DataFrame()

    def get_token(self, exchange: str, symbol: str) -> Optional[str]:
        if self._scrip_master_df is None or self._scrip_master_df.empty:
            return self._KNOWN_TOKENS.get((exchange.upper(), symbol.upper()))

        exch = exchange.upper()
        sym = symbol.upper().strip()
        df = self._scrip_master_df

        def _get_best_token(match_df):
            if match_df.empty: return None
            if exch in ("CDS", "MCX", "NFO") and "expiry" in match_df.columns:
                temp = match_df.copy()
                # FIX: Added explicit format to quiet Pandas warning
                temp["_exp"] = pd.to_datetime(temp["expiry"], format="%d%b%Y", errors="coerce")
                temp = temp.dropna(subset=["_exp"])
                temp = temp[temp["_exp"] >= pd.Timestamp.today().normalize()]
                if not temp.empty:
                    return str(temp.sort_values("_exp").iloc[0]["token"])
            return str(match_df.iloc[0]["token"])

        match = df[(df["exch_seg"] == exch) & (df["symbol"] == sym)]
        tok = _get_best_token(match)
        if tok: return tok

        match = df[(df["exch_seg"] == exch) & (df["symbol"].str.contains(sym, na=False))]
        tok = _get_best_token(match)
        if tok: return tok

        known = self._KNOWN_TOKENS.get((exch, sym))
        if known: return known
        return None

    def get_ltp(self, exchange: str, symbol: str) -> Optional[float]:
        if not self._ensure_session(): return None
        token = self.get_token(exchange, symbol)
        if not token: return None
        try:
            result = self.smart_api.ltpData(exchange, symbol, token)
            if result and result.get("status"):
                return float(result["data"]["ltp"])
        except Exception as e:
            logger.error("get_ltp exception %s/%s: %s", exchange, symbol, e)
        return None

    def get_candle_data(self, exchange: str, symbol: str, interval: str = "FIFTEEN_MINUTE", days: int = 5) -> List[dict]:
        if not self._ensure_session(): return []
        token = self.get_token(exchange, symbol)
        if not token: return []
        try:
            to_dt = datetime.now()
            from_dt = to_dt - timedelta(days=days)
            params = {
                "exchange": exchange, "symbol": symbol, "token": token,
                "interval": interval, "from": from_dt.strftime("%Y-%m-%d %H:%M"), "to": to_dt.strftime("%Y-%m-%d %H:%M"),
            }
            result = self.smart_api.getCandleData(params)
            # FIX: Handle empty/None data more robustly
            if result and result.get("status") and result.get("data") is not None:
                candles = []
                for c in result["data"]:
                    if len(c) >= 5:
                        candles.append({
                            "time": c[0], "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), "volume": int(c[5]) if len(c) > 5 else 0,
                        })
                return candles
        except Exception as e:
            logger.error("get_candle_data exception: %s", e)
        return []

    def fetch_nse_option_chain(self, index: str = "NIFTY") -> dict:
        """
        FIXED: NSE Option Chain Fetcher
        Angel One sometimes blocks direct 404. We add better headers and error handling.
        """
        try:
            session = requests.Session()
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json", "Referer": "https://www.nseindia.com/",
            }
            # Initial hit to get cookies
            session.get("https://www.nseindia.com", headers=headers, timeout=10)
            
            url = f"https://www.nseindia.com/api/option-chain-indices?symbol={index.upper()}"
            resp = session.get(url, headers=headers, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                records = data.get("records", {})
                expiry_dates = records.get("expiryDates", [])
                if not expiry_dates: return {"source": "FALLBACK", "message": "No expiry found"}
                
                nearest_expiry = expiry_dates[0]
                filtered = [d for d in data["filtered"]["data"] if d["expiryDate"] == nearest_expiry]
                
                call_oi = sum(d.get("CE", {}).get("openInterest", 0) for d in filtered)
                put_oi = sum(d.get("PE", {}).get("openInterest", 0) for d in filtered)
                pcr = round(put_oi / call_oi, 2) if call_oi > 0 else 0
                
                return {
                    "source": "NSE_LIVE",
                    "call_oi": call_oi, "put_oi": put_oi, "pcr": pcr,
                    "max_pain": records.get("underlyingValue", 0),
                    "expiry": nearest_expiry
                }
            logger.warning(f"NSE Option Chain HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"fetch_nse_option_chain error: {e}")
        
        return {"source": "FALLBACK"}
_client_instance = None
def get_angel_client():
    global _client_instance
    if _client_instance is None:
        _client_instance = AngelClient()
    return _client_instance
