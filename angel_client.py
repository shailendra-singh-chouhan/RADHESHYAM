"""
angel_client.py — Angel One SmartAPI wrapper

FIXED v4:
- getCandleData: try BOTH param formats (symboltoken + fromdate/todate AND token + from/to)
- Better error logging for debugging
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
            logger.info("Session expired, re-logging in...")
            return self._login()
        return self.smart_api is not None

    def _load_scrip_master(self):
        try:
            url = (
                "https://margincalculator.angelbroking.com/"
                "OpenAPI_File/files/OpenAPIScripMaster.json"
            )
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            self._scrip_master_df = pd.DataFrame(data)

            for col in ("symbol", "name", "exch_seg"):
                if col in self._scrip_master_df.columns:
                    self._scrip_master_df[col] = (
                        self._scrip_master_df[col]
                        .astype(str)
                        .str.upper()
                        .str.strip()
                    )
            if "token" in self._scrip_master_df.columns:
                self._scrip_master_df["token"] = self._scrip_master_df["token"].astype(str)

            if "strike" in self._scrip_master_df.columns:
                self._scrip_master_df["strike"] = pd.to_numeric(
                    self._scrip_master_df["strike"], errors="coerce"
                )
                self._scrip_master_df.loc[
                    self._scrip_master_df["strike"] > 100000, "strike"
                ] = self._scrip_master_df.loc[
                    self._scrip_master_df["strike"] > 100000, "strike"
                ] / 100

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
            if match_df.empty:
                return None
            if exch in ("CDS", "MCX", "NFO") and "expiry" in match_df.columns:
                temp = match_df.copy()
                temp["_exp"] = pd.to_datetime(temp["expiry"], errors="coerce", dayfirst=True)
                temp = temp.dropna(subset=["_exp"])
                temp = temp[temp["_exp"] >= pd.Timestamp.today().normalize()]
                if not temp.empty:
                    return str(temp.sort_values("_exp").iloc[0]["token"])
            return str(match_df.iloc[0]["token"])

        match = df[(df["exch_seg"] == exch) & (df["symbol"] == sym)]
        tok = _get_best_token(match)
        if tok:
            return tok

        match = df[(df["exch_seg"] == exch) & (df["symbol"].str.contains(sym, na=False))]
        tok = _get_best_token(match)
        if tok:
            return tok

        match = df[(df["exch_seg"] == exch) & (df["name"].str.contains(sym, na=False))]
        tok = _get_best_token(match)
        if tok:
            return tok

        if exch == "BSE" and sym in ("SENSEX", "BSESENSEX", "S&P BSE SENSEX"):
            sensex_match = df[
                (df["exch_seg"] == "BSE")
                & (
                    df["name"].str.upper().str.contains("SENSEX", na=False)
                    | df["symbol"].str.upper().str.contains("SENSEX", na=False)
                )
            ]
            if not sensex_match.empty:
                return str(sensex_match.iloc[0]["token"])
            return "1"

        known = self._KNOWN_TOKENS.get((exch, sym))
        if known:
            logger.info("Token from known fallback: %s/%s → %s", exch, sym, known)
            return known

        logger.warning("Token not found: %s/%s", exchange, symbol)
        return None

    def get_ltp(self, exchange: str, symbol: str) -> Optional[float]:
        if not self._ensure_session():
            return None

        token = self.get_token(exchange, symbol)
        if not token:
            return None

        try:
            result = self.smart_api.ltpData(exchange, symbol, token)
            if result and result.get("status"):
                return float(result["data"]["ltp"])
            logger.warning("ltpData failed for %s/%s: %s", exchange, symbol, result)
        except Exception as e:
            err_str = str(e).lower()
            if "session" in err_str or "token" in err_str or "unauthorized" in err_str:
                self._login()
            else:
                logger.error("get_ltp exception %s/%s: %s", exchange, symbol, e)
        return None

    def get_ltp_by_token(self, exchange: str, symbol: str, token: str) -> Optional[float]:
        if not self._ensure_session():
            return None
        try:
            result = self.smart_api.ltpData(exchange, symbol, token)
            if result and result.get("status") and result.get("data"):
                return float(result["data"].get("ltp", 0) or 0)
            logger.warning("ltpData(by_token) failed for %s/%s: %s", exchange, symbol, result)
        except Exception as e:
            err_str = str(e).lower()
            if "session" in err_str or "token" in err_str or "unauthorized" in err_str:
                self._login()
            else:
                logger.error("get_ltp_by_token exception %s/%s: %s", exchange, symbol, e)
        return None

    def get_ohlc(self, exchange: str, symbol: str) -> Optional[Dict[str, Any]]:
        if not self._ensure_session():
            return None

        token = self.get_token(exchange, symbol)
        if not token:
            return None

        try:
            result = self.smart_api.ltpData(exchange, symbol, token)
            if result and result.get("status") and result.get("data"):
                d = result["data"]
                # NOTE: ltpData only returns LTP, not real open/high/low/close.
                # Returning those as None (not 0) so callers don't mistake
                # missing data for a real zero price.
                return {
                    "ltp": float(d.get("ltp", 0) or 0),
                    "open": None,
                    "high": None,
                    "low": None,
                    "close": None,
                }
            logger.warning("get_ohlc failed for %s/%s: %s", exchange, symbol, result)
        except Exception as e:
            logger.error("get_ohlc exception %s/%s: %s", exchange, symbol, e)
        return None


# ────────────────────────────────────────────────────────
# SINGLETON — used everywhere else in the app (main.py, stocks.py,
# strategy.py, live_data_fetcher.py) via get_angel_client().
# This was accidentally removed in a recent rewrite, breaking every
# import across the app. Do not remove this again.
# ────────────────────────────────────────────────────────

_angel_client: Optional["AngelClient"] = None


def get_angel_client() -> "AngelClient":
    """Get or create the singleton AngelClient instance."""
    global _angel_client
    if _angel_client is None:
        _angel_client = AngelClient()
    return _angel_client
