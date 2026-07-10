"""
angel_client.py — Angel One SmartAPI wrapper
Handles: login, token resolution, LTP, candles, option contracts, live option LTP

FIXED: getCandleData params — symboltoken (not token), fromdate/todate (not from/to)
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

    # Well-known tokens — fallback if scrip master lookup fails
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

    # ────────────────────────────────────────────────────────
    # LOGIN / SESSION MANAGEMENT
    # ────────────────────────────────────────────────────────

    def _login(self) -> bool:
        """Login to Angel One SmartAPI using client credentials + TOTP."""
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
        """Re-login if session is older than ~30 minutes."""
        if time.time() - self._last_login_time > 1800:
            logger.info("Session expired, re-logging in...")
            return self._login()
        return self.smart_api is not None

    # ────────────────────────────────────────────────────────
    # SCRIP MASTER (instrument lookup table)
    # ────────────────────────────────────────────────────────

    def _load_scrip_master(self):
        """Download and parse Angel One scrip master JSON."""
        try:
            url = (
                "https://margincalculator.angelbroking.com/"
                "OpenAPI_File/files/OpenAPIScripMaster.json"
            )
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            self._scrip_master_df = pd.DataFrame(data)

            # Normalize string columns
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

            # Also normalize strike if present
            if "strike" in self._scrip_master_df.columns:
                self._scrip_master_df["strike"] = pd.to_numeric(
                    self._scrip_master_df["strike"], errors="coerce"
                )

            logger.info(
                "Scrip master loaded: %d instruments", len(self._scrip_master_df)
            )
        except Exception as e:
            logger.error("Failed to load scrip master: %s", e)
            self._scrip_master_df = pd.DataFrame()

    # ────────────────────────────────────────────────────────
    # TOKEN RESOLUTION
    # ────────────────────────────────────────────────────────

    def get_token(self, exchange: str, symbol: str) -> Optional[str]:
        """
        Resolve exchange + symbol → Angel One token string.
        """
        if self._scrip_master_df is None or self._scrip_master_df.empty:
            logger.warning("Scrip master not loaded, using known tokens fallback")
            return self._KNOWN_TOKENS.get((exchange.upper(), symbol.upper()))

        exch = exchange.upper()
        sym = symbol.upper().strip()
        df = self._scrip_master_df

        # Helper: MCX/CDS/NFO के लिए हमेशा nearest future expiry सॉर्ट करें
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

        # 1. Exact match
        match = df[(df["exch_seg"] == exch) & (df["symbol"] == sym)]
        tok = _get_best_token(match)
        if tok:
            return tok

        # 2. Symbol contains
        match = df[(df["exch_seg"] == exch) & (df["symbol"].str.contains(sym, na=False))]
        tok = _get_best_token(match)
        if tok:
            return tok

        # 3. Name contains
        match = df[(df["exch_seg"] == exch) & (df["name"].str.contains(sym, na=False))]
        tok = _get_best_token(match)
        if tok:
            return tok

        # 4. BSE SENSEX special handling
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

        # 5. Known tokens fallback
        known = self._KNOWN_TOKENS.get((exch, sym))
        if known:
            logger.info("Token from known fallback: %s/%s → %s", exch, sym, known)
            return known

        logger.warning("Token not found: %s/%s", exchange, symbol)
        return None

    # ────────────────────────────────────────────────────────
    # LTP (Last Traded Price)
    # ────────────────────────────────────────────────────────

    def get_ltp(self, exchange: str, symbol: str) -> Optional[float]:
        """Get last traded price for a given instrument."""
        if not self._ensure_session():
            return None

        token = self.get_token(exchange, symbol)
        if not token:
            return None

        try:
            result = self.smart_api.ltpData(exchange, symbol, token)
            if result and result.get("status"):
                return float(result["data"]["ltp"])
            logger.warning(
                "ltpData failed for %s/%s: %s", exchange, symbol, result
            )
        except Exception as e:
            err_str = str(e).lower()
            if "session" in err_str or "token" in err_str or "unauthorized" in err_str:
                self._login()
            else:
                logger.error("get_ltp exception %s/%s: %s", exchange, symbol, e)
        return None

    def get_ltp_by_token(self, exchange: str, symbol: str, token: str) -> Optional[float]:
        """Get LTP when token is already resolved (skip scrip-master lookup)."""
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
        """Get today's OHLC + LTP snapshot for a given instrument."""
        if not self._ensure_session():
            return None

        token = self.get_token(exchange, symbol)
        if not token:
            return None

        try:
            result = self.smart_api.ltpData(exchange, symbol, token)
            if result and result.get("status") and result.get("data"):
                d = result["data"]
                return {
                    "symbol": symbol,
                    "token": token,
                    "ltp":   float(d.get("ltp", 0) or 0),
                    "open":  float(d.get("open", 0) or 0),
                    "high":  float(d.get("high", 0) or 0),
                    "low":   float(d.get("low", 0) or 0),
                    "close": float(d.get("close", 0) or 0),
                }
            logger.warning("get_ohlc empty for %s/%s: %s", exchange, symbol, result)
        except Exception as e:
            err_str = str(e).lower()
            if "session" in err_str or "token" in err_str or "unauthorized" in err_str:
                self._login()
            else:
                logger.error("get_ohlc exception %s/%s: %s", exchange, symbol, e)
        return None

    # ────────────────────────────────────────────────────────
    # CANDLE DATA (Historical OHLCV) — FIXED PARAMS
    # ────────────────────────────────────────────────────────

    def get_candle_data(
        self,
        exchange: str,
        symbol: str,
        interval: str = "FIFTEEN_MINUTE",
        days: int = 5,
    ) -> List[dict]:
        """Get historical OHLCV candle data."""
        if not self._ensure_session():
            return []

        token = self.get_token(exchange, symbol)
        if not token:
            logger.error("No token for %s/%s, cannot fetch candles", exchange, symbol)
            return []

        try:
            to_dt = datetime.now()
            from_dt = to_dt - timedelta(days=days)

            # FIXED: SmartAPI expects symboltoken, fromdate, todate
            params = {
                "exchange": exchange,
                "symboltoken": token,
                "interval": interval,
                "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
                "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
            }

            result = self.smart_api.getCandleData(params)

            # Handle empty response gracefully
            if not result:
                logger.warning("getCandleData returned empty/None for %s/%s", exchange, symbol)
                return []

            if not result.get("status"):
                logger.warning("getCandleData status=False for %s/%s: %s", exchange, symbol, result.get("message", "unknown"))
                return []

            if not result.get("data"):
                logger.warning("getCandleData no data for %s/%s", exchange, symbol)
                return []

            candles = []
            for c in result["data"]:
                if not isinstance(c, (list, tuple)) or len(c) < 5:
                    continue
                candles.append(
                    {
                        "time": int(c[0]) if len(c) > 0 else 0,
                        "open": float(c[1]) if len(c) > 1 else 0,
                        "high": float(c[2]) if len(c) > 2 else 0,
                        "low": float(c[3]) if len(c) > 3 else 0,
                        "close": float(c[4]) if len(c) > 4 else 0,
                        "volume": int(c[5]) if len(c) > 5 else 0,
                    }
                )

            logger.info("Fetched %d candles for %s/%s (%s)", len(candles), exchange, symbol, interval)
            return candles

        except Exception as e:
            err_str = str(e).lower()
            if "session" in err_str or "token" in err_str or "unauthorized" in err_str:
                logger.warning("Session error in getCandleData, re-logging in...")
                self._login()
            else:
                logger.error("get_candle_data exception for %s/%s: %s", exchange, symbol, e)
        return []

    # ────────────────────────────────────────────────────────
    # OPTION CONTRACT RESOLUTION
    # ────────────────────────────────────────────────────────

    def get_options_contract_details(
        self, index: str, strike: int, option_type: str
    ) -> Optional[dict]:
        """
        Find the nearest weekly-expiry option contract for given index/strike/type.
        Returns dict: {token, symbol, expiry, lotsize}  or  None.
        """
        if self._scrip_master_df is None or self._scrip_master_df.empty:
            logger.warning("Cannot resolve option: scrip master not loaded")
            return None

        ot = option_type.upper().strip()
        if ot not in ("CE", "PE"):
            ot = "CE"

        strike_str = str(strike)

        opts = self._scrip_master_df[
            (self._scrip_master_df["exch_seg"] == "NFO")
            & (self._scrip_master_df["symbol"].str.upper().str.contains(index.upper(), na=False))
            & (self._scrip_master_df["symbol"].str.contains(strike_str, na=False))
            & (self._scrip_master_df["symbol"].str.upper().str.contains(ot, na=False))
        ].copy()

        if opts.empty:
            logger.warning(
                "No option contract found: %s %s %s", index, strike, option_type
            )
            return None

        # Parse & filter to nearest future expiry
        opts["_expiry_dt"] = pd.to_datetime(opts.get("expiry"), errors="coerce", dayfirst=True)
        opts = opts.dropna(subset=["_expiry_dt"])
        opts = opts[opts["_expiry_dt"] >= pd.Timestamp.today().normalize()]

        if opts.empty:
            logger.warning(
                "No future expiry for %s %s %s", index, strike, option_type
            )
            return None

        opts = opts.sort_values("_expiry_dt")
        best = opts.iloc[0]

        result = {
            "token": str(best["token"]),
            "symbol": str(best["symbol"]),
            "expiry": best["_expiry_dt"].strftime("%d-%b-%Y"),
            "lotsize": int(best.get("lotsize", 0) or 0),
        }

        logger.info(
            "Resolved option: %s %s%s → token=%s expiry=%s lotsize=%s",
            index, strike, ot, result["token"], result["expiry"], result["lotsize"],
        )
        return result

    # ────────────────────────────────────────────────────────
    # LIVE OPTION LTP
    # ────────────────────────────────────────────────────────

    def get_option_ltp(self, index: str, strike: int, option_type: str) -> dict:
        """
        Fetch REAL LTP of an option contract from Angel One.
        """
        error_result = {"ltp": None, "source": "UNAVAILABLE"}

        # Step 1 — resolve contract
        contract = self.get_options_contract_details(index, strike, option_type)
        if not contract:
            error_result["source"] = "ERROR: Contract not found in scrip master"
            logger.warning(
                "get_option_ltp: contract not resolved for %s %s %s",
                index, strike, option_type,
            )
            return error_result

        # Step 2 — fetch LTP
        if not self._ensure_session():
            error_result["source"] = "ERROR: Session expired, re-login failed"
            return error_result

        try:
            result = self.smart_api.ltpData(
                "NFO", contract["symbol"], contract["token"]
            )
            if result and result.get("status") and result.get("data"):
                ltp = float(result["data"]["ltp"])
                if ltp > 0:
                    return {
                        "ltp": ltp,
                        "symbol": contract["symbol"],
                        "token": contract["token"],
                        "expiry": contract["expiry"],
                        "lotsize": contract["lotsize"],
                        "source": "ANGEL_ONE",
                    }
            error_result["source"] = "ERROR: ltpData returned empty/invalid"
            logger.warning(
                "get_option_ltp: ltpData failed for %s %s %s → %s",
                index, strike, option_type, result,
            )
        except Exception as e:
            error_result["source"] = f"ERROR: {str(e)}"
            logger.error(
                "get_option_ltp exception for %s %s %s: %s",
                index, strike, option_type, e,
            )

        return error_result


# ────────────────────────────────────────────────────────
# MODULE-LEVEL HELPER (singleton)
# ────────────────────────────────────────────────────────

_angel_client_instance: Optional[AngelClient] = None


def get_angel_client() -> AngelClient:
    """Return a singleton AngelClient instance."""
    global _angel_client_instance
    if _angel_client_instance is None:
        _angel_client_instance = AngelClient()
    return _angel_client_instance
