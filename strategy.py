
\"\"\"
import math
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

import config
import trading
from angel_client import get_angel_client
from indicators import (
    calculate_rsi,
    calculate_ema,
    calculate_vwap_approx,
    calculate_macd,
    calculate_supertrend,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#                        SIGNAL ENGINE (ORB)
# ═══════════════════════════════════════════════════════════════════
def generate_signal(candles: List[Dict[str, Any]], spot: float) -> Dict[str, Any]:
    \"\"\"
    Opening-Range-Breakout (ORB) signal — 6-point checklist.
    Returns: {signal, confidence, checklist, orb_high, orb_low, note}
    \"\"\"
    if not candles or len(candles) < 20 or not spot:
        return {
            \"signal\": \"WAIT\", \"confidence\": 0, \"checklist\": {},
            \"orb_high\": 0, \"orb_low\": 0, \"note\": \"Syncing candle data...\",
        }

    closes = [c[\"close\"] for c in candles]
    highs = [c[\"high\"] for c in candles]
    lows = [c[\"low\"] for c in candles]

    # ORB = first 15-min range (approx first 1 15m-candle or first 3 5m-candles)
    orb_window = candles[:3] if len(candles) >= 3 else candles[:1]
    orb_high = max(c[\"high\"] for c in orb_window)
    orb_low = min(c[\"low\"] for c in orb_window)

    rsi = calculate_rsi(closes, 14) or 50
    ema9 = calculate_ema(closes, 9) or spot
    ema21 = calculate_ema(closes, 21) or spot
    vwap = calculate_vwap_approx(candles) or spot
    macd = calculate_macd(closes) or {\"macd\": 0, \"signal\": 0}
    st = calculate_supertrend(candles) or {\"trend\": \"WAIT\", \"value\": 0}

    # LONG checklist
    long_checks = {
        \"orb_breakout\": spot > orb_high,
        \"above_vwap\": spot > vwap,
        \"ema_bullish\": ema9 > ema21,
        \"rsi_not_overbought\": rsi < 70,
        \"macd_bullish\": macd[\"macd\"] > macd[\"signal\"],
        \"supertrend_buy\": st[\"trend\"] == \"BUY\",
    }
    # SHORT checklist
    short_checks = {
        \"orb_breakdown\": spot < orb_low,
        \"below_vwap\": spot < vwap,
        \"ema_bearish\": ema9 < ema21,
        \"rsi_not_oversold\": rsi > 30,
        \"macd_bearish\": macd[\"macd\"] < macd[\"signal\"],
        \"supertrend_sell\": st[\"trend\"] == \"SELL\",
    }

    long_score = sum(long_checks.values())
    short_score = sum(short_checks.values())

    if long_score >= 4 and long_score > short_score:
        return {
            \"signal\": \"LONG\", \"confidence\": long_score, \"checklist\": long_checks,
            \"orb_high\": round(orb_high, 2), \"orb_low\": round(orb_low, 2),
            \"note\": f\"{long_score}/6 checks agree — ORB breakout + trend confirmed\",
        }
    if short_score >= 4 and short_score > long_score:
        return {
            \"signal\": \"SHORT\", \"confidence\": short_score, \"checklist\": short_checks,
            \"orb_high\": round(orb_high, 2), \"orb_low\": round(orb_low, 2),
            \"note\": f\"{short_score}/6 checks agree — ORB breakdown + trend confirmed\",
        }
    return {
        \"signal\": \"WAIT\",
        \"confidence\": max(long_score, short_score),
        \"checklist\": long_checks if long_score >= short_score else short_checks,
        \"orb_high\": round(orb_high, 2), \"orb_low\": round(orb_low, 2),
        \"note\": \"Awaiting confirmation\",
    }


# ═══════════════════════════════════════════════════════════════════
#                        REAL DATA FETCHERS
# ═══════════════════════════════════════════════════════════════════
def fetch_global_markets() -> Dict[str, Any]:
    \"\"\"Fetch Kospi, Nasdaq, Dow Jones from Yahoo Finance.\"\"\"
    try:
        import yfinance as yf
        tickers = {\"kospi\": \"^KS11\", \"nasdaq\": \"^IXIC\", \"dji\": \"^DJI\"}
        out: Dict[str, Any] = {\"source\": \"YAHOO_FINANCE\"}
        for k, t in tickers.items():
            try:
                info = yf.Ticker(t).fast_info
                price = float(info.last_price) if info and info.last_price else None
                if price:
                    out[k] = round(price, 2)
            except Exception:
                out[k] = None
        return out
    except Exception as e:
        logger.debug(f\"Global markets fetch failed: {e}\")
        return {\"source\": \"UNAVAILABLE\", \"kospi\": None, \"nasdaq\": None, \"dji\": None}


_NSE_HEADERS = {
    \"User-Agent\": (
        \"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 \"
        \"(KHTML, like Gecko) Chrome/120.0 Safari/537.36\"
    ),
    \"Accept\": \"application/json, text/plain, */*\",
    \"Referer\": \"https://www.nseindia.com/\",
    \"Accept-Language\": \"en-US,en;q=0.9\",
}


def _nse_session() -> Optional[requests.Session]:
    try:
        s = requests.Session()
        s.headers.update(_NSE_HEADERS)
        s.get(\"https://www.nseindia.com\", timeout=6)
        return s
    except Exception:
        return None


def _fetch_real_option_chain(index: str = \"NIFTY\") -> Dict[str, Any]:
    \"\"\"Fetch full option chain from NSE. Returns OI totals + top strikes + max_pain.\"\"\"
    s = _nse_session()
    if not s:
        return {\"source\": \"FALLBACK_SPOT_ONLY\", \"call_oi\": 0, \"put_oi\": 0,
                \"pcr\": 0.0, \"max_pain\": 0, \"top_strikes\": [], \"strike_count\": 0}
    try:
        url = f\"https://www.nseindia.com/api/option-chain-indices?symbol={index}\"
        r = s.get(url, timeout=8)
        if r.status_code != 200:
            raise RuntimeError(f\"NSE returned {r.status_code}\")
        data = r.json()
        records = data.get(\"records\", {}) or {}
        filtered = data.get(\"filtered\", {}).get(\"data\", []) or []

        # Nearest expiry only
        expiry_dates = records.get(\"expiryDates\", [])
        nearest_expiry = expiry_dates[0] if expiry_dates else None
        rows = [d for d in filtered if d.get(\"expiryDate\") == nearest_expiry] or filtered

        call_oi = sum((d.get(\"CE\") or {}).get(\"openInterest\", 0) for d in rows)
        put_oi = sum((d.get(\"PE\") or {}).get(\"openInterest\", 0) for d in rows)
        pcr = round(put_oi / call_oi, 2) if call_oi else 0.0

        # Max pain = strike with min combined OI pain
        strikes = sorted({d.get(\"strikePrice\") for d in rows if d.get(\"strikePrice\")})
        max_pain_strike, min_pain = 0, float(\"inf\")
        for k in strikes:
            pain = 0.0
            for d in rows:
                sp = d.get(\"strikePrice\")
                ce_oi = (d.get(\"CE\") or {}).get(\"openInterest\", 0)
                pe_oi = (d.get(\"PE\") or {}).get(\"openInterest\", 0)
                if sp < k:
                    pain += (k - sp) * ce_oi
                elif sp > k:
                    pain += (sp - k) * pe_oi
            if pain < min_pain:
                min_pain, max_pain_strike = pain, k

        # Top 7 strikes around max_pain
        top = sorted(
            rows, key=lambda d: abs((d.get(\"strikePrice\") or 0) - max_pain_strike)
        )[:7]
        top_strikes = [
            {
                \"strike\": d.get(\"strikePrice\"),
                \"ce_oi\": (d.get(\"CE\") or {}).get(\"openInterest\", 0),
                \"pe_oi\": (d.get(\"PE\") or {}).get(\"openInterest\", 0),
                \"ce_ltp\": (d.get(\"CE\") or {}).get(\"lastPrice\", 0),
                \"pe_ltp\": (d.get(\"PE\") or {}).get(\"lastPrice\", 0),
                \"ce_chg_oi\": (d.get(\"CE\") or {}).get(\"changeinOpenInterest\", 0),
                \"pe_chg_oi\": (d.get(\"PE\") or {}).get(\"changeinOpenInterest\", 0),
                \"total_oi\": (d.get(\"CE\") or {}).get(\"openInterest\", 0)
                + (d.get(\"PE\") or {}).get(\"openInterest\", 0),
            }
            for d in top
        ]
        return {
            \"source\": \"NSE_LIVE\",
            \"call_oi\": call_oi,
            \"put_oi\": put_oi,
            \"pcr\": pcr,
            \"max_pain\": max_pain_strike,
            \"expiry\": nearest_expiry,
            \"top_strikes\": top_strikes,
            \"strike_count\": len(strikes),
        }
    except Exception as e:
        logger.debug(f\"NSE option chain fetch failed: {e}\")
        return {\"source\": \"FALLBACK_SPOT_ONLY\", \"call_oi\": 0, \"put_oi\": 0,
                \"pcr\": 0.0, \"max_pain\": 0, \"top_strikes\": [], \"strike_count\": 0}


def get_oi_data(index: str = \"NIFTY\") -> Dict[str, Any]:
    return _fetch_real_option_chain(index)


def _fetch_real_fii_dii() -> Dict[str, Any]:
    \"\"\"Fetch FII/DII cash-market activity from NSE.\"\"\"
    s = _nse_session()
    if not s:
        return {\"status\": \"Live\"}
    try:
        url = \"https://www.nseindia.com/api/fiidiiTradeReact\"
        r = s.get(url, timeout=8)
        if r.status_code != 200:
            raise RuntimeError(f\"NSE returned {r.status_code}\")
        rows = r.json()
        out = {\"status\": \"Live (NSE)\"}
        for row in rows:
            cat = (row.get(\"category\") or \"\").upper()
            if \"FII\" in cat or \"FPI\" in cat:
                out[\"fii_long\"] = float(row.get(\"buyValue\", 0))
                out[\"fii_short\"] = float(row.get(\"sellValue\", 0))
                out[\"fii_net\"] = float(row.get(\"netValue\", 0))
            elif \"DII\" in cat:
                out[\"dii_long\"] = float(row.get(\"buyValue\", 0))
                out[\"dii_short\"] = float(row.get(\"sellValue\", 0))
                out[\"dii_net\"] = float(row.get(\"netValue\", 0))
        return out
    except Exception as e:
        logger.debug(f\"NSE FII/DII fetch failed: {e}\")
        return {\"status\": \"Live\"}


def fetch_institutional_stats() -> Dict[str, Any]:
    stats = _fetch_real_fii_dii()
    stats.setdefault(\"fii_long\", 0)
    stats.setdefault(\"fii_short\", 0)
    stats.setdefault(\"fii_net\", 0)
    stats.setdefault(\"dii_long\", 0)
    stats.setdefault(\"dii_short\", 0)
    stats.setdefault(\"dii_net\", 0)
    return stats


# ═══════════════════════════════════════════════════════════════════
#                     GREEKS (Black-Scholes approximation)
# ═══════════════════════════════════════════════════════════════════
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def calculate_greeks(
    spot: float, strike: float, option_type: str = \"CE\",
    days_to_expiry: int = 5, iv_percent: float = 12.0, r: float = 0.065
) -> Dict[str, Any]:
    \"\"\"Black-Scholes greek approximation for index options.\"\"\"
    try:
        if spot <= 0 or strike <= 0 or days_to_expiry <= 0:
            raise ValueError
        T = max(days_to_expiry, 1) / 365.0
        sigma = max(iv_percent, 1) / 100.0
        d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        if option_type.upper() == \"CE\":
            delta = _norm_cdf(d1)
            theta = (
                -(spot * _norm_pdf(d1) * sigma) / (2 * math.sqrt(T))
                - r * strike * math.exp(-r * T) * _norm_cdf(d2)
            ) / 365.0
        else:
            delta = _norm_cdf(d1) - 1
            theta = (
                -(spot * _norm_pdf(d1) * sigma) / (2 * math.sqrt(T))
                + r * strike * math.exp(-r * T) * _norm_cdf(-d2)
            ) / 365.0
        gamma = _norm_pdf(d1) / (spot * sigma * math.sqrt(T))
        vega = spot * _norm_pdf(d1) * math.sqrt(T) / 100.0
        return {
            \"iv\": round(iv_percent, 2),
            \"delta\": round(delta, 4),
            \"theta\": round(theta, 2),
            \"gamma\": round(gamma, 4),
            \"vega\": round(vega, 2),
            \"source\": \"BS_APPROX\",
        }
    except Exception:
        return {\"iv\": iv_percent, \"delta\": None, \"theta\": None, \"gamma\": None,
                \"vega\": None, \"source\": \"BS_APPROX\"}


# ═══════════════════════════════════════════════════════════════════
#                        BACKGROUND POLLERS
# ═══════════════════════════════════════════════════════════════════
_poller_started = False


def _price_poller():
    \"\"\"Refresh index prices every 10s during market hours.\"\"\"
    while True:
        try:
            if config.get_market_status() in (\"OPEN\", \"PRE_OPEN\"):
                client = get_angel_client()
                updates = {
                    \"nifty\": client.get_ltp(\"NSE\", \"NIFTY\") or None,
                    \"banknifty\": client.get_ltp(\"NSE\", \"BANKNIFTY\") or None,
                    \"finnifty\": client.get_ltp(\"NSE\", \"FINNIFTY\") or None,
                    \"sensex\": client.get_ltp(\"BSE\", \"SENSEX\") or None,
                    \"crudeoil\": client.get_ltp(\"MCX\", \"CRUDEOIL\") or None,
                    \"gold\": client.get_ltp(\"MCX\", \"GOLD\") or None,
                    \"silver\": client.get_ltp(\"MCX\", \"SILVER\") or None,
                    \"usdinr\": client.get_ltp(\"CDS\", \"USDINR\") or None,
                    \"midcap\": client.get_ltp(\"NSE\", \"NIFTY MIDCAP 100\") or None,
                    \"vix\": client.get_ltp(\"NSE\", \"INDIA VIX\") or None,
                    \"last_update\": config.get_ist_now().isoformat(),
                }
                config.state_manager.update_state(\"latest_prices\", updates,
                                                  allow_none_overwrite=False)
                config.state_manager.last_data_update_time = config.get_ist_now()
        except Exception as e:
            logger.debug(f\"price_poller error: {e}\")
        time.sleep(10)


def _indicator_poller():
    \"\"\"Every 3 min: refresh candles, recompute indicators + signal, run auto-trade.\"\"\"
    while True:
        try:
            if config.get_market_status() in (\"OPEN\", \"PRE_OPEN\"):
                client = get_angel_client()
                candles = client.get_candle_data(\"NSE\", \"NIFTY\", \"FIFTEEN_MINUTE\", days=2)
                spot = config.state_manager.latest_prices.get(\"nifty\") or 0
                if candles:
                    config.state_manager.set_state(\"candle_store\", candles)
                    closes = [c[\"close\"] for c in candles]
                    ind = {
                        \"rsi\": calculate_rsi(closes, 14),
                        \"ema9\": calculate_ema(closes, 9),
                        \"ema21\": calculate_ema(closes, 21),
                        \"vwap_approx\": calculate_vwap_approx(candles),
                        \"macd\": calculate_macd(closes),
                        \"supertrend\": calculate_supertrend(candles),
                    }
                    config.state_manager.set_state(\"indicator_data\", ind)
                    sig = generate_signal(candles, spot)
                    config.state_manager.set_state(\"signal_data\", sig)

                # Auto-trade (safety-gated by AUTO_TRADE_ENABLED)
                try:
                    from database import SessionLocal
                    if SessionLocal:
                        db = SessionLocal()
                        try:
                            trading.process_auto_signal(db)
                        finally:
                            db.close()
                except Exception as e:
                    logger.debug(f\"auto-trade tick error: {e}\")
        except Exception as e:
            logger.debug(f\"indicator_poller error: {e}\")
        time.sleep(180)


def _macro_poller():
    \"\"\"Every 5 min: refresh OI, FII/DII, global indices.\"\"\"
    while True:
        try:
            oi = _fetch_real_option_chain(\"NIFTY\")
            if oi:
                config.state_manager.set_state(\"oi_data\", oi)
            fii = fetch_institutional_stats()
            config.state_manager.set_state(\"institutional_stats\", fii)
            gm = fetch_global_markets()
            if gm:
                for k in (\"kospi\", \"nasdaq\", \"dji\"):
                    if gm.get(k) is not None:
                        config.state_manager.update_state(
                            \"latest_prices\", {k: gm[k]}, allow_none_overwrite=False
                        )
        except Exception as e:
            logger.debug(f\"macro_poller error: {e}\")
        time.sleep(300)


def start_background_threads():
    global _poller_started
    if _poller_started:
        return
    _poller_started = True
    for fn, name in (
        (_price_poller, \"price_poller\"),
        (_indicator_poller, \"indicator_poller\"),
        (_macro_poller, \"macro_poller\"),
    ):
        t = threading.Thread(target=fn, name=name, daemon=True)
        t.start()
        logger.info(f\"Background thread started: {name}\")
"
