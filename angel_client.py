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
        ("NSE", "NIFTY 50"): "26000", ("NSE", "NIFTY"): "26000",
        ("NFO", "NIFTY"): "26000", ("NSE", "BANKNIFTY"): "26009",
        ("NFO", "BANKNIFTY"): "26009", ("BSE", "SENSEX"): "1"
    }

    def __init__(self):
        self.smart_api = None
        self._last_login_time = 0
        self._scrip_master_df = None
        self._login()
        self._load_scrip_master()

    def _login(self):
        try:
            self.smart_api = SmartConnect(api_key=config.ANGEL_API_KEY)
            data = self.smart_api.generateSession(config.ANGEL_CLIENT_ID, config.ANGEL_MPIN, pyotp.TOTP(config.ANGEL_TOTP_SECRET).now())
            if data.get("status"):
                self._last_login_time = time.time()
                logger.info("Login Successful")
                return True
        except Exception as e:
            logger.error(f"Login Error: {e}")
        return False

    def _ensure_session(self):
        if time.time() - self._last_login_time > 1800: return self._login()
        return self.smart_api is not None

    def _load_scrip_master(self):
        try:
            resp = requests.get("https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json", timeout=15)
            if resp.status_code == 200 and resp.text.strip():
                self._scrip_master_df = pd.DataFrame(resp.json())
                for col in ("symbol", "name", "exch_seg"):
                    self._scrip_master_df[col] = self._scrip_master_df[col].astype(str).str.upper().str.strip()
                self._scrip_master_df["token"] = self._scrip_master_df["token"].astype(str)
        except Exception as e:
            logger.error(f"Scrip Master Error: {e}")

    def get_token(self, exchange, symbol):
        if self._scrip_master_df is None or self._scrip_master_df.empty:
            return self._KNOWN_TOKENS.get((exchange.upper(), symbol.upper()))
        df = self._scrip_master_df
        match = df[(df["exch_seg"] == exchange.upper()) & (df["symbol"] == symbol.upper())]
        if not match.empty:
            if exchange.upper() in ("NFO", "MCX"):
                match = match.copy()
                match["_exp"] = pd.to_datetime(match["expiry"], format="%d%b%Y", errors="coerce")
                match = match.dropna(subset=["_exp"]).sort_values("_exp")
                return str(match.iloc[0]["token"])
            return str(match.iloc[0]["token"])
        return self._KNOWN_TOKENS.get((exchange.upper(), symbol.upper()))

    def get_ltp(self, exchange, symbol):
        if not self._ensure_session(): return None
        token = self.get_token(exchange, symbol)
        try:
            res = self.smart_api.ltpData(exchange, symbol, token)
            if res.get("status"): return float(res["data"]["ltp"])
        except: pass
        return None

    def get_candle_data(self, exchange, symbol, interval="FIFTEEN_MINUTE", days=5):
        if not self._ensure_session(): return []
        token = self.get_token(exchange, symbol)
        try:
            to_dt = datetime.now()
            from_dt = to_dt - timedelta(days=days)
            params = {"exchange": exchange, "symbol": symbol, "token": token, "interval": interval, "from": from_dt.strftime("%Y-%m-%d %H:%M"), "to": to_dt.strftime("%Y-%m-%d %H:%M")}
            res = self.smart_api.getCandleData(params)
            if res and res.get("status") and res.get("data"):
                return [{"time": c[0], "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), "volume": int(c[5])} for c in res["data"] if len(c) >= 5]
        except Exception as e:
            logger.error(f"Candle Error: {e}")
        return []

    def fetch_nse_option_chain(self, index="NIFTY"):
        try:
            s = requests.Session()
            h = {"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": "https://www.nseindia.com/"}
            s.get("https://www.nseindia.com", headers=h, timeout=5)
            res = s.get(f"https://www.nseindia.com/api/option-chain-indices?symbol={index.upper()}", headers=h, timeout=5)
            if res.status_code == 200:
                d = res.json()
                f = d["filtered"]["data"]
                return {"source": "NSE_LIVE", "pcr": round(sum(x.get("PE",{}).get("openInterest",0) for x in f)/sum(x.get("CE",{}).get("openInterest",0) for x in f), 2) if sum(x.get("CE",{}).get("openInterest",0) for x in f) > 0 else 0}
        except: pass
        return {"source": "FALLBACK", "pcr": 1.0}

_inst = None
def get_angel_client():
    global _inst
    if _inst is None: _inst = AngelClient()
    return _inst
