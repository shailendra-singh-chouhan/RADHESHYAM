"""
angel_client.py — Angel One SmartAPI wrapper
Handles: login, token resolution, LTP, candles, option contracts, live option LTP

Phase 3.C  : Sensex token fallback in get_token()
Phase 3.B1 : NEW get_option_ltp() function
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

        Lookup order:
        1. Exact match (exchange + symbol)
        2. Symbol contains match
        3. Name contains match
        4. BSE SENSEX special handling  ← Phase 3.C (NEW)
        5. Hardcoded known-token fallback
        """
        if self._scrip_master_df is None or self._scrip_master_df.empty:
            logger.warning("Scrip master not loaded, using known tokens fallback")
            return self._KNOWN_TOKENS.get((exchange.upper(), symbol.upper()))

        exch = exchange.upper()
        sym = symbol.upper().strip()
        df = self._scrip_master_df

        # 1. Exact match
        match = df[(df["exch_seg"] == exch) & (df["symbol"] == sym)]
        if not match.empty:
            return str(match.iloc[0]["token"])

        # 2. Symbol contains
        match = df[
            (df["exch_seg"] == exch) & (df["symbol"].str.contains(sym, na=False))
        ]
        if not match.empty:
            return str(match.iloc[0]["token"])

        # 3. Name contains
        match = df[
            (df["exch_seg"] == exch) & (df["name"].str.contains(sym, na=False))
        ]
        if not match.empty:
            return str(match.iloc[0]["token"])

        # ── 4. BSE SENSEX special handling (Phase 3.C — NEW) ──
        if exch == "BSE" and sym in (
            "SENSEX",
            "BSESENSEX",
            "S&P BSE SENSEX",
        ):
            sensex_match = df[
                (df["exch_seg"] == "BSE")
                & (
                    df["name"].str.upper().str.contains("SENSEX", na=False)
                    | df["symbol"].str.upper().str.contains("SENSEX", na=False)
                )
            ]
            if not sensex_match.empty:
                return str(sensex_match.iloc[0]["token"])
            # Hardcoded fallback — BSE Sensex index token is "1"
            logger.warning(
                "SENSEX not found in scrip master, using hardcoded token='1'"
            )
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
    # CANDLE DATA (Historical OHLCV)
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
            return []

        try:
            to_dt = datetime.now()
            from_dt = to_dt - timedelta(days=days)

            params = {
                "exchange": exchange,
                "symbol": symbol,
                "token": token,
                "interval": interval,
                "from": from_dt.strftime("%Y-%m-%d %H:%M"),
                "to": to_dt.strftime("%Y-%m-%d %H:%M"),
            }

            result = self.smart_api.getCandleData(params)
            if result and result.get("status") and result.get("data"):
                candles = []
                for c in result["data"]:
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
                return candles
            logger.warning("getCandleData failed for %s/%s", exchange, symbol)
        except Exception as e:
            err_str = str(e).lower()
            if "session" in err_str or "token" in err_str or "unauthorized" in err_str:
                self._login()
            else:
                logger.error("get_candle_data exception: %s", e)
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
        opts["_expiry_dt"] = pd.to_datetime(opts.get("expiry"), errors="coerce")
        opts = opts.dropna(subset=["_expiry_dt"])
        opts = opts[opts["_expiry_dt"] >= datetime.now()]

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
    # LIVE OPTION LTP  (Phase 3.B1 — NEW FUNCTION)
    # ────────────────────────────────────────────────────────

    def get_option_ltp(self, index: str, strike: int, option_type: str) -> dict:
        """
        Fetch REAL LTP of an option contract from Angel One.

        Returns (on success):
            {
                "ltp": 125.45,
                "token": "12345",
                "symbol": "NIFTY 24000 PE",
                "expiry": "10-Jul-2026",
                "lotsize": 75,
                "source": "ANGEL_ONE_LIVE"
            }

        Returns (on failure):
            {"ltp": None, "source": "UNAVAILABLE"}
            {"ltp": None, "source": "ERROR: <reason>"}
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
                        "token": contract["token"],
                        "symbol": contract["symbol"],
                        "expiry": contract["expiry"],
                        "lotsize": contract["lotsize"],
                        "source": "ANGEL_ONE_LIVE",
                    }
                else:
                    error_result["source"] = (
                        "ERROR: LTP returned 0 (market closed or illiquid)"
                    )
                    return error_result
            else:
                msg = result.get("message", "unknown") if result else "empty response"
                error_result["source"] = f"ERROR: ltpData failed — {msg}"
                logger.warning("get_option_ltp ltpData failed: %s", msg)
                return error_result

        except Exception as e:
            err_str = str(e).lower()
            if "session" in err_str or "token" in err_str or "unauthorized" in err_str:
                self._login()
                error_result["source"] = "ERROR: Session error, will retry next cycle"
            else:
                error_result["source"] = f"ERROR: {type(e).__name__}: {e}"
                logger.error("get_option_ltp exception: %s", e)
            return error_result

    # ────────────────────────────────────────────────────────
    # NSE OPTION CHAIN (direct from NSE India website)
    # ────────────────────────────────────────────────────────

    def fetch_nse_option_chain(self, index: str = "NIFTY") -> dict:
        """
        Fetch option chain from NSE India — OI, PCR, Max Pain, top strikes.

        Phase 3.B2: now returns expiry, top_strikes (7), strike_count.
        """
        result = {
            "call_oi": 0,
            "put_oi": 0,
            "pcr": 0.0,
            "max_pain": 0,
            "expiry": None,
            "top_strikes": [],
            "strike_count": 0,
            "source": "FALLBACK_SPOT_ONLY",
        }

        try:
            session = requests.Session()
            session.get(
                "https://www.nseindia.com",
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                },
                timeout=10,
            )

            url = (
                f"https://www.nseindia.com/api/optionchain-indices"
                f"?symbol={index}"
            )
            resp = session.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "application/json",
                    "Referer": "https://www.nseindia.com/",
                },
                timeout=10,
            )

            if resp.status_code != 200:
                logger.warning("NSE option chain HTTP %d", resp.status_code)
                return result

            data = resp.json()
            records = data.get("records", {})
            all_strikes = records.get("data", [])

            if not all_strikes:
                logger.warning("NSE option chain: empty data")
                return result

            # ── Current weekly expiry ──
            expiry_dates = records.get("expiryDates", [])
            current_expiry = expiry_dates[0] if expiry_dates else None

            # Filter to current weekly expiry only
            if current_expiry:
                filtered = [
                    s for s in all_strikes
                    if s.get("expiryDate") == current_expiry
                ]
            else:
                filtered = all_strikes
                current_expiry = (
                    filtered[0].get("expiryDate", "") if filtered else ""
                )

            if not filtered:
                filtered = all_strikes
                current_expiry = (
                    filtered[0].get("expiryDate", "") if filtered else ""
                )

            # ── Aggregate OI ──
            total_call_oi = 0
            total_put_oi = 0
            strike_data = []

            for s in filtered:
                ce = s.get("CE") or {}
                pe = s.get("PE") or {}
                strike_price = s.get("strikePrice", 0)

                ce_oi = int(ce.get("openInterest", 0) or 0)
                pe_oi = int(pe.get("openInterest", 0) or 0)
                ce_ltp = float(ce.get("lastPrice", 0) or 0)
                pe_ltp = float(pe.get("lastPrice", 0) or 0)
                ce_chg = int(ce.get("changeinOpenInterest", 0) or 0)
                pe_chg = int(pe.get("changeinOpenInterest", 0) or 0)

                total_call_oi += ce_oi
                total_put_oi += pe_oi

                strike_data.append(
                    {
                        "strike": strike_price,
                        "ce_oi": ce_oi,
                        "pe_oi": pe_oi,
                        "ce_ltp": ce_ltp,
                        "pe_ltp": pe_ltp,
                        "ce_chg_oi": ce_chg,
                        "pe_chg_oi": pe_chg,
                        "total_oi": ce_oi + pe_oi,
                    }
                )

            # PCR
            pcr = (
                round(total_put_oi / total_call_oi, 2)
                if total_call_oi > 0
                else 0.0
            )

            # Max Pain — strike with highest total OI
            max_pain = 0
            max_total = 0
            for sd in strike_data:
                if sd["total_oi"] > max_total:
                    max_total = sd["total_oi"]
                    max_pain = sd["strike"]

            # Top 7 strikes by total OI (descending)
            top_sorted = sorted(
                strike_data, key=lambda x: x["total_oi"], reverse=True
            )
            top_strikes = top_sorted[:7]

            # Format expiry
            formatted_expiry = None
            if current_expiry:
                try:
                    dt = datetime.strptime(current_expiry, "%d-%b-%Y")
                    formatted_expiry = dt.strftime("%d-%b-%Y")
                except Exception:
                    formatted_expiry = current_expiry

            result = {
                "call_oi": total_call_oi,
                "put_oi": total_put_oi,
                "pcr": pcr,
                "max_pain": max_pain,
                "expiry": formatted_expiry,
                "top_strikes": top_strikes,
                "strike_count": len(strike_data),
                "source": "NSE_LIVE",
            }

            logger.info(
                "NSE chain OK: %d strikes, PCR=%.2f, MaxPain=%d, expiry=%s",
                len(strike_data), pcr, max_pain, formatted_expiry,
            )
            return result

        except requests.exceptions.Timeout:
            logger.warning("NSE option chain: timeout")
            return result
        except Exception as e:
            logger.error("NSE option chain exception: %s", e)
            return result


# ────────────────────────────────────────────────────────
# SINGLETON
# ────────────────────────────────────────────────────────

_angel_client: Optional[AngelClient] = None


def get_angel_client() -> AngelClient:
    """Get or create the singleton AngelClient instance."""
    global _angel_client
    if _angel_client is None:
        _angel_client = AngelClient()
    return _angel_client


def get_ohlc(exchange: str, symbol: str) -> Optional[Dict[str, Any]]:
    """Module-level convenience wrapper — used by stocks.py."""
    return get_angel_client().get_ohlc(exchange, symbol)


def get_ltp(exchange: str, symbol: str) -> Optional[float]:
    return get_angel_client().get_ltp(exchange, symbol)


def get_ltp_by_token(exchange: str, symbol: str, token: str) -> Optional[float]:
    return get_angel_client().get_ltp_by_token(exchange, symbol, token)
