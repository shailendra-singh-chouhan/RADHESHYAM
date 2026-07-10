import os, logging, time, requests, pyotp, pandas as pd
from datetime import datetime, timedelta
from SmartApi import SmartConnect
import config

logger = logging.getLogger(__name__)

class AngelClient:
    def __init__(self):
        self.api = None
        self.last_login = 0
        self.master_df = None
        self.login()
        self.load_master()

    def login(self):
        try:
            self.api = SmartConnect(api_key=config.ANGEL_API_KEY)
            res = self.api.generateSession(config.ANGEL_CLIENT_ID, config.ANGEL_MPIN, pyotp.TOTP(config.ANGEL_TOTP_SECRET).now())
            if res.get("status"): self.last_login = time.time(); return True
        except: pass
        return False

    def ensure(self):
        if time.time() - self.last_login > 1800: return self.login()
        return self.api is not None

    def load_master(self):
        try:
            r = requests.get("https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json", timeout=10)
            if r.status_code == 200:
                self.master_df = pd.DataFrame(r.json())
                self.master_df["symbol"] = self.master_df["symbol"].str.upper()
        except: pass

    def get_token(self, exch, sym):
        if self.master_df is None: return "26000" if "NIFTY" in sym else "26009"
        m = self.master_df[(self.master_df["exch_seg"] == exch) & (self.master_df["symbol"] == sym)]
        return str(m.iloc[0]["token"]) if not m.empty else "26000"

    def get_ltp(self, exch, sym):
        if not self.ensure(): return 0
        try:
            r = self.api.ltpData(exch, sym, self.get_token(exch, sym))
            return float(r["data"]["ltp"]) if r.get("status") else 0
        except: return 0

    def get_candle_data(self, exch, sym, interval="FIFTEEN_MINUTE", days=2):
        if not self.ensure(): return []
        try:
            to_dt = datetime.now()
            fr_dt = to_dt - timedelta(days=days)
            p = {"exchange": exch, "symbol": sym, "token": self.get_token(exch, sym), "interval": interval, "from": fr_dt.strftime("%Y-%m-%d %H:%M"), "to": to_dt.strftime("%Y-%m-%d %H:%M")}
            r = self.api.getCandleData(p)
            if r.get("status"): return [{"time": c[0], "open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4]), "volume": int(c[5])} for c in r["data"]]
        except: pass
        return []

    def fetch_nse_option_chain(self, index="NIFTY"):
        try:
            h = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com/"}
            s = requests.Session()
            s.get("https://www.nseindia.com", headers=h, timeout=5)
            r = s.get(f"https://www.nseindia.com/api/option-chain-indices?symbol={index}", headers=h, timeout=5)
            if r.status_code == 200:
                f = r.json()["filtered"]["data"]
                pcr = sum(x.get("PE",{}).get("openInterest",0) for x in f) / sum(x.get("CE",{}).get("openInterest",0) for x in f)
                return {"source": "NSE", "pcr": round(pcr, 2), "call_oi": sum(x.get("CE",{}).get("openInterest",0) for x in f), "put_oi": sum(x.get("PE",{}).get("openInterest",0) for x in f)}
        except: pass
        return {"source": "FALLBACK", "pcr": 1.0, "call_oi": 0, "put_oi": 0}

_i = None
def get_angel_client():
    global _i
    if _i is None: _i = AngelClient()
    return _i
