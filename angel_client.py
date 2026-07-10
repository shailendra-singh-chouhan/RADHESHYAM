
\"\"\"
import os
import time
import logging
import requests
import pyotp
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from SmartApi import SmartConnect
import config

logger = logging.getLogger(__name__)

SCRIP_MASTER_URL = (
    \"https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json\"
)

# Known fallback tokens (Angel One identifiers for indices)
_INDEX_TOKEN_FALLBACK: Dict[str, str] = {
    (\"NSE\", \"NIFTY\"): \"26000\",
    (\"NSE\", \"BANKNIFTY\"): \"26009\",
    (\"NSE\", \"FINNIFTY\"): \"26037\",
    (\"NSE\", \"MIDCPNIFTY\"): \"26074\",
    (\"NSE\", \"NIFTY MIDCAP 100\"): \"26011\",
    (\"NSE\", \"INDIA VIX\"): \"26017\",
    (\"BSE\", \"SENSEX\"): \"99919000\",  # BSE Sensex index token
}


class AngelClient:
    def __init__(self):
        self.api: Optional[SmartConnect] = None
        self.jwt_token: Optional[str] = None
        self.last_login: float = 0
        self.master_df: Optional[pd.DataFrame] = None
        self.login()
        self.load_master()

    # ────────────────────── Auth ──────────────────────
    def login(self) -> bool:
        if not (config.ANGEL_API_KEY and config.ANGEL_CLIENT_ID and config.ANGEL_MPIN
                and config.ANGEL_TOTP_SECRET):
            logger.warning(\"Angel One credentials missing — running in offline mode.\")
            return False
        try:
            self.api = SmartConnect(api_key=config.ANGEL_API_KEY)
            totp = pyotp.TOTP(config.ANGEL_TOTP_SECRET).now()
            res = self.api.generateSession(
                config.ANGEL_CLIENT_ID, config.ANGEL_MPIN, totp
            )
            if res and res.get(\"status\"):
                self.jwt_token = res.get(\"data\", {}).get(\"jwtToken\")
                self.last_login = time.time()
                logger.info(\"Angel One login successful.\")
                return True
            logger.warning(f\"Angel One login failed: {res}\")
        except Exception as e:
            logger.warning(f\"Angel One login exception: {e}\")
        return False

    def ensure(self) -> bool:
        if self.api is None:
            return self.login()
        if time.time() - self.last_login > 1800:
            return self.login()
        return True

    # ────────────────────── Scrip master ──────────────────────
    def load_master(self) -> None:
        try:
            r = requests.get(SCRIP_MASTER_URL, timeout=15)
            if r.status_code == 200:
                self.master_df = pd.DataFrame(r.json())
                self.master_df[\"symbol\"] = self.master_df[\"symbol\"].astype(str).str.upper()
                self.master_df[\"name\"] = self.master_df[\"name\"].astype(str).str.upper()
                logger.info(f\"Scrip master loaded: {len(self.master_df)} rows.\")
        except Exception as e:
            logger.warning(f\"Scrip master load failed: {e}\")

    def get_token(self, exch: str, sym: str) -> str:
        \"\"\"Resolve tradingsymbol → numeric token. Falls back for indices.\"\"\"
        sym_u = sym.upper()
        if self.master_df is not None:
            m = self.master_df[
                (self.master_df[\"exch_seg\"] == exch)
                & (self.master_df[\"symbol\"] == sym_u)
            ]
            if not m.empty:
                return str(m.iloc[0][\"token\"])
            # Prefix match fallback (useful for USDINR-XX, GOLD-XX contracts)
            m2 = self.master_df[
                (self.master_df[\"exch_seg\"] == exch)
                & (self.master_df[\"symbol\"].str.startswith(sym_u))
            ]
            if not m2.empty:
                return str(m2.iloc[0][\"token\"])
        # Hardcoded fallbacks for indices
        fb = _INDEX_TOKEN_FALLBACK.get((exch, sym_u))
        if fb:
            return fb
        return \"26000\"  # NIFTY default (safe)

    # ────────────────────── LTP ──────────────────────
    def get_ltp(self, exch: str, sym: str) -> float:
        if not self.ensure():
            return 0.0
        try:
            token = self.get_token(exch, sym)
            r = self.api.ltpData(exch, sym, token)
            if r and r.get(\"status\"):
                return float(r[\"data\"][\"ltp\"])
        except Exception as e:
            logger.debug(f\"LTP fetch failed for {exch}/{sym}: {e}\")
        return 0.0

    def get_ltp_by_token(self, exch: str, symbol: str, token: str) -> float:
        \"\"\"Fetch LTP when token is already known (e.g., resolved option contract).\"\"\"
        if not self.ensure():
            return 0.0
        try:
            r = self.api.ltpData(exch, symbol, token)
            if r and r.get(\"status\"):
                return float(r[\"data\"][\"ltp\"])
        except Exception as e:
            logger.debug(f\"LTP-by-token fetch failed for {symbol}: {e}\")
        return 0.0

    # ────────────────────── Candles ──────────────────────
    def get_candle_data(
        self, exch: str, sym: str, interval: str = \"FIFTEEN_MINUTE\", days: int = 2
    ) -> List[Dict[str, Any]]:
        if not self.ensure():
            return []
        try:
            to_dt = datetime.now()
            fr_dt = to_dt - timedelta(days=days)
            p = {
                \"exchange\": exch,
                \"symbol\": sym,
                \"token\": self.get_token(exch, sym),
                \"interval\": interval,
                \"fromdate\": fr_dt.strftime(\"%Y-%m-%d %H:%M\"),
                \"todate\": to_dt.strftime(\"%Y-%m-%d %H:%M\"),
            }
            r = self.api.getCandleData(p)
            if r and r.get(\"status\"):
                return [
                    {
                        \"time\": c[0],
                        \"open\": float(c[1]),
                        \"high\": float(c[2]),
                        \"low\": float(c[3]),
                        \"close\": float(c[4]),
                        \"volume\": int(c[5]) if len(c) > 5 else 0,
                    }
                    for c in (r.get(\"data\") or [])
                ]
        except Exception as e:
            logger.debug(f\"Candle fetch failed for {sym}: {e}\")
        return []

    # ────────────────────── Options contract discovery ──────────────────────
    def get_options_contract_details(
        self, index: str, strike: int, option_type: str
    ) -> Optional[Dict[str, Any]]:
        \"\"\"
        Find the nearest-expiry option contract for (index, strike, option_type).
        Returns {symbol, token, expiry, lotsize} or None.
        \"\"\"
        if self.master_df is None:
            return None
        try:
            df = self.master_df[
                (self.master_df[\"exch_seg\"] == \"NFO\")
                & (self.master_df[\"name\"] == index.upper())
                & (self.master_df[\"instrumenttype\"].isin([\"OPTIDX\", \"OPTFUT\"]))
            ]
            if df.empty:
                return None
            # Strike is stored as string, in paise (multiplied by 100) in some files
            strike_str = str(int(strike))
            strike_paise = str(int(strike) * 100)
            df2 = df[
                df[\"strike\"].astype(str).isin([strike_str, strike_paise, f\"{strike}.000000\"])
            ]
            if df2.empty:
                return None
            # Filter by option type (CE/PE) via symbol suffix
            df3 = df2[df2[\"symbol\"].str.endswith(option_type.upper())]
            if df3.empty:
                return None
            # Sort by expiry (date-aware)
            df3 = df3.copy()
            df3[\"expiry_dt\"] = pd.to_datetime(df3[\"expiry\"], errors=\"coerce\")
            df3 = df3.dropna(subset=[\"expiry_dt\"]).sort_values(\"expiry_dt\")
            if df3.empty:
                return None
            row = df3.iloc[0]
            return {
                \"symbol\": str(row[\"symbol\"]),
                \"token\": str(row[\"token\"]),
                \"expiry\": row[\"expiry_dt\"].strftime(\"%d-%b-%Y\"),
                \"lotsize\": int(row.get(\"lotsize\", 75) or 75),
            }
        except Exception as e:
            logger.debug(f\"options_contract_details failed: {e}\")
            return None

    def get_option_ltp(
        self, index: str, strike: int, option_type: str
    ) -> Optional[Dict[str, Any]]:
        \"\"\"
        High-level: fetch live LTP for the ATM option contract.
        Returns {ltp, symbol, token, expiry, lotsize, source} or None.
        \"\"\"
        details = self.get_options_contract_details(index, strike, option_type)
        if not details:
            return None
        ltp = self.get_ltp_by_token(\"NFO\", details[\"symbol\"], details[\"token\"])
        if ltp <= 0:
            return None
        return {
            \"ltp\": round(ltp, 2),
            \"symbol\": details[\"symbol\"],
            \"token\": details[\"token\"],
            \"expiry\": details[\"expiry\"],
            \"lotsize\": details[\"lotsize\"],
            \"source\": \"ANGEL_ONE_LIVE\",
        }


# ────────────────────── Module-level helpers ──────────────────────
_instance: Optional[AngelClient] = None


def get_angel_client() -> AngelClient:
    global _instance
    if _instance is None:
        _instance = AngelClient()
    return _instance


# Legacy shims (kept for backward compatibility with older callers)
def get_options_contract_details(index: str, strike: int, option_type: str):
    return get_angel_client().get_options_contract_details(index, strike, option_type)


def get_ltp_by_token(exch: str, symbol: str, token: str) -> float:
    return get_angel_client().get_ltp_by_token(exch, symbol, token)
"
