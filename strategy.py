"""
strategy.py — Signal generation, OI analysis, Greeks, Institutional stats

FIXED:
- OI fallback: No hardcoded fake numbers
- FII/DII fallback: No hardcoded fake ratios
- Greeks: VIX proxy for IV, honest theoretical labels
- Removed local fetch_global_markets() shadowing
- ORB: time range 09:14-09:16 IST with timezone awareness
"""

import logging
import math
import threading
import time
from datetime import datetime, timedelta, time as dt_time, timezone
from typing import Optional, Dict, Any, List

import requests

import config
from angel_client import get_angel_client

from live_data_fetcher import (
    fetch_nifty_spot, fetch_banknifty_spot, fetch_sensex_spot,
    fetch_nse_option_chain, fetch_india_vix,
    fetch_global_markets, fetch_fii_dii,
    fetch_candles, fetch_all_live_data,
)

logger = logging.getLogger(__name__)

shared_state: Dict[str, Any] = {
    "spot": 0.0,
    "active_trade": None,
    "signal": "WAIT",
    "confidence": 0,
    "checklist": {},
    "greeks": {},
    "oi_data": {},
    "global": {},
    "institutional_stats": {},
    "last_updated": None,
}


def generate_signal(candles: List[dict], spot: float) -> dict:
    """
    Generate GOAT Signal based on 6 conditions.
    FIXED: ORB uses time range (09:14-09:16 IST) instead of exact match.
    """
    signal = {
        "signal": "WAIT",
        "confidence": 0,
        "checklist": {
            "orb_breakdown": False,
            "below_vwap": False,
            "ema_bearish": False,
            "rsi_not_oversold": False,
            "macd_bearish": False,
            "supertrend_sell": False,
        },
        "orb_high": None,
        "orb_low": None,
        "note": "",
    }

    if not candles or len(candles) < 5:
        signal["note"] = f"Not enough candles for signal (got {len(candles) if candles else 0})"
        return signal

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    from indicators import (
        calculate_rsi,
        calculate_ema,
        calculate_vwap,
        calculate_macd,
        calculate_supertrend,
    )

    rsi_val = calculate_rsi(closes, period=14)
    ema9 = calculate_ema(closes, period=9)
    ema21 = calculate_ema(closes, period=21)
    vwap_val = calculate_vwap(candles)
    macd_data = calculate_macd(closes)
    st_data = calculate_supertrend(highs, lows, closes, period=10, multiplier=3)

    # ── ORB (Opening Range Breakout) — FIXED ──
    IST = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(IST).date()
    
    today_candles = []
    for c in candles:
        ts = c.get("time", 0)
        if ts > 1e11:
            ts = ts / 1000
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(IST)
        if dt.date() == today:
            today_candles.append((dt, c))
    
    today_candles.sort(key=lambda x: x[0])
    
    # Find 09:15 candle using time range (09:14:00 to 09:16:59)
    market_open = None
    for dt, c in today_candles:
        if dt_time(9, 14) <= dt.time() <= dt_time(9, 16, 59):
            market_open = c
            break
    
    if market_open:
        signal["orb_high"] = round(market_open["high"], 2)
        signal["orb_low"] = round(market_open["low"], 2)
    elif today_candles:
        first_c = today_candles[0][1]
        signal["orb_high"] = round(first_c["high"], 2)
        signal["orb_low"] = round(first_c["low"], 2)
        signal["note"] = f"ORB approximated from first candle ({today_candles[0][0].strftime('%H:%M')})"
    else:
        first_c = candles[0]
        signal["orb_high"] = round(first_c["high"], 2)
        signal["orb_low"] = round(first_c["low"], 2)
        signal["note"] = "ORB from historical buffer (no today's data)"

    orb_high = signal["orb_high"] or 0
    orb_low = signal["orb_low"] or 0

    bear_checks = {
        "orb_breakdown": (orb_low > 0 and spot < orb_low),
        "below_vwap": (vwap_val is not None and spot < vwap_val),
        "ema_bearish": (ema9 is not None and ema21 is not None and ema9 < ema21),
        "rsi_not_oversold": (rsi_val is not None and rsi_val > 30),
        "macd_bearish": (
            macd_data is not None
            and macd_data.get("macd") is not None
            and macd_data.get("signal") is not None
            and macd_data["macd"] < macd_data["signal"]
        ),
        "supertrend_sell": (
            st_data is not None and st_data.get("trend") == "SELL"
        ),
    }

    bull_checks = {
        "orb_breakout": (orb_high > 0 and spot > orb_high),
        "above_vwap": (vwap_val is not None and spot > vwap_val),
        "ema_bullish": (ema9 is not None and ema21 is not None and ema9 > ema21),
        "rsi_not_overbought": (rsi_val is not None and rsi_val < 70),
        "macd_bullish": (
            macd_data is not None
            and macd_data.get("macd") is not None
            and macd_data.get("signal") is not None
            and macd_data["macd"] > macd_data["signal"]
        ),
        "supertrend_buy": (
            st_data is not None and st_data.get("trend") == "BUY"
        ),
    }

    bear_count = sum(1 for v in bear_checks.values() if v)
    bull_count = sum(1 for v in bull_checks.values() if v)

    if bear_count >= 4 and bear_count > bull_count:
        signal["signal"] = "SHORT"
        signal["confidence"] = bear_count
        signal["checklist"] = {k: bool(v) for k, v in bear_checks.items()}
        signal["note"] = f"{bear_count}/6 checks agree — ORB breakdown + trend confirmed"
    elif bull_count >= 4 and bull_count > bear_count:
        signal["signal"] = "LONG"
        signal["confidence"] = bull_count
        signal["checklist"] = {
            "orb_breakdown": bull_checks["orb_breakout"],
            "below_vwap": bull_checks["above_vwap"],
            "ema_bearish": bull_checks["ema_bullish"],
            "rsi_not_oversold": bull_checks["rsi_not_overbought"],
            "macd_bearish": bull_checks["macd_bullish"],
            "supertrend_sell": bull_checks["supertrend_buy"],
        }
        signal["note"] = f"{bull_count}/6 checks agree — ORB breakout + trend confirmed"
    else:
        signal["confidence"] = max(bear_count, bull_count)
        signal["note"] = f"No strong signal (bull={bull_count}, bear={bear_count})"

    return signal


def _fetch_real_option_chain(index: str = "NIFTY") -> dict:
    try:
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={index}"
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        })
        session.get("https://www.nseindia.com", timeout=10)
        resp = session.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            records = data.get("records", {})
            underlying = records.get("underlyingValue", 0)
            expiry_dates = records.get("expiryDates", [])
            nearest_expiry = expiry_dates[0] if expiry_dates else None

            ce_oi = 0
            pe_oi = 0
            strikes_data = []

            for item in records.get("data", []):
                ce = item.get("CE", {})
                pe = item.get("PE", {})
                ce_oi += ce.get("openInterest", 0)
                pe_oi += pe.get("openInterest", 0)
                strike = item.get("strikePrice", 0)
                total_oi = ce.get("openInterest", 0) + pe.get("openInterest", 0)
                if total_oi > 0:
                    strikes_data.append({
                        "strike": strike,
                        "oi": total_oi,
                        "ce_oi": ce.get("openInterest", 0),
                        "pe_oi": pe.get("openInterest", 0),
                    })

            strikes_data.sort(key=lambda x: x["oi"], reverse=True)
            top_strikes = strikes_data[:7]
            pcr = round(pe_oi / ce_oi, 2) if ce_oi > 0 else 0.0

            max_pain = None
            if strikes_data:
                max_pain = min(strikes_data, key=lambda x: abs(x["strike"] - underlying))["strike"]

            return {
                "call_oi": ce_oi,
                "put_oi": pe_oi,
                "pcr": pcr,
                "max_pain": max_pain,
                "expiry": nearest_expiry,
                "top_strikes": top_strikes,
                "strike_count": len(strikes_data),
                "source": "NSE_LIVE",
                "underlying": underlying,
            }
    except Exception as e:
        logger.warning(f"NSE option chain fetch failed: {e}")

    logger.warning("NSE chain failed, using fallback OI")
    return _fallback_oi_data()


def _fallback_oi_data() -> dict:
    return {
        "call_oi": None,
        "put_oi": None,
        "pcr": None,
        "max_pain": None,
        "expiry": None,
        "top_strikes": [],
        "strike_count": 0,
        "source": "UNAVAILABLE",
        "underlying": None,
        "note": "NSE OI API blocked on cloud / Angel One has no OI field",
    }


def get_oi_data(index: str = "NIFTY") -> dict:
    return _fetch_real_option_chain(index)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def calculate_greeks(
    spot: float,
    strike: float,
    option_type: str = "PE",
    days_to_expiry: float = 5.0,
    risk_free_rate: float = 0.07,
    iv_percent: float = 13.0,
) -> dict:
    S = spot
    K = strike
    T = max(days_to_expiry / 365.0, 0.001)
    r = risk_free_rate
    sigma = iv_percent / 100.0

    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    is_ce = option_type.upper() == "CE"

    delta = _norm_cdf(d1) if is_ce else _norm_cdf(d1) - 1.0
    theta = (
        (
            -S * _norm_pdf(d1) * sigma / (2 * math.sqrt(T))
            - r * K * math.exp(-r * T) * (_norm_cdf(d2) if is_ce else _norm_cdf(d2) - 1.0)
        )
        / 365.0
    )
    gamma = _norm_pdf(d1) / (S * sigma * math.sqrt(T))
    vega = S * _norm_pdf(d1) * math.sqrt(T) / 100.0

    return {
        "iv": round(iv_percent, 2),
        "delta": round(delta, 4),
        "theta": round(theta, 2),
        "gamma": round(gamma, 6),
        "vega": round(vega, 2),
        "source": "BS_APPROX",
    }


def get_atm_greeks(spot: float, index: str = "NIFTY") -> dict:
    step = 50 if index == "NIFTY" else 100
    atm_strike = round(spot / step) * step

    # Try to use live VIX as IV proxy; fallback to 13.0 but mark it clearly
    try:
        vix_data = fetch_india_vix()
        iv = vix_data.get("value")
    except Exception:
        iv = None

    if iv is None or iv <= 0:
        iv = 13.0
        iv_source = "HARDCODED_DEFAULT"
    else:
        iv_source = "INDIA_VIX_PROXY"

    ce_greeks = calculate_greeks(spot, atm_strike, "CE", iv_percent=iv)
    pe_greeks = calculate_greeks(spot, atm_strike, "PE", iv_percent=iv)

    return {
        "atm_strike": atm_strike,
        "ce": ce_greeks,
        "pe": pe_greeks,
        "source": "BS_THEORETICAL",
        "iv_source": iv_source,
        "iv_value": iv,
        "note": "Greeks are theoretical (Black-Scholes). IV may not reflect market implied volatility.",
    }


def fetch_institutional_stats() -> dict:
    result = {
        "fii_long": None,
        "fii_short": None,
        "fii_net": None,
        "dii_long": None,
        "dii_short": None,
        "dii_net": None,
        "win_rate": 0.0,
        "total_trades": 0,
        "status": "Unavailable",
        "source": "NSE_BLOCKED",
        "note": "NSE blocks cloud IPs. Deploy locally or use VPN for real FII/DII.",
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
            },
            timeout=10,
        )

        url = "https://www.nseindia.com/api/fii-dii"
        resp = session.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Referer": "https://www.nseindia.com/",
            },
            timeout=10,
        )

        if resp.status_code == 200:
            data = resp.json()
            fii_data = data.get("FII", []) or data.get("fii", [])
            dii_data = data.get("DII", []) or data.get("dii", [])

            if fii_data and isinstance(fii_data, list) and len(fii_data) > 0:
                latest_fii = fii_data[0]
                fii_buy = float(latest_fii.get("buyValue", 0) or 0)
                fii_sell = float(latest_fii.get("sellValue", 0) or 0)
                fii_total = fii_buy + fii_sell
                if fii_total > 0:
                    result["fii_long"] = round(fii_buy / fii_total, 4)
                    result["fii_short"] = round(fii_sell / fii_total, 4)
                    result["fii_net"] = round(
                        (fii_buy - fii_sell) / fii_total, 4
                    )

            if dii_data and isinstance(dii_data, list) and len(dii_data) > 0:
                latest_dii = dii_data[0]
                dii_buy = float(latest_dii.get("buyValue", 0) or 0)
                dii_sell = float(latest_dii.get("sellValue", 0) or 0)
                dii_total = dii_buy + dii_sell
                if dii_total > 0:
                    result["dii_long"] = round(dii_buy / dii_total, 4)
                    result["dii_short"] = round(dii_sell / dii_total, 4)
                    result["dii_net"] = round(
                        (dii_buy - dii_sell) / dii_total, 4
                    )

            result["status"] = "Live (NSE)"
            result["source"] = "NSE_LIVE"

    except Exception as e:
        logger.error("Institutional stats error: %s", e)

    return result


# REMOVED: local fetch_global_markets() that was shadowing the import from live_data_fetcher


_poller_running = False


def _live_data_poller() -> None:
    global _poller_running
    logger.info("Live data poller started — ALL REAL DATA")

    while _poller_running:
        try:
            nifty = fetch_nifty_spot()
            banknifty = fetch_banknifty_spot()
            spot = nifty.get("value", 0)

            if not spot:
                logger.warning("No NIFTY spot from Angel One")
                time.sleep(5)
                continue

            candles = fetch_candles("NIFTY 50", "NSE", "FIFTEEN_MINUTE", 5)

            signal_data = generate_signal(candles, spot) if candles and len(candles) >= 21 else {
                "signal": "WAIT",
                "confidence": 0,
                "checklist": {},
                "note": "Not enough candles" if not candles else "Insufficient data",
            }

            oi_data = fetch_nse_option_chain("NIFTY")
            greeks_data = get_atm_greeks(spot, "NIFTY")
            vix_data = fetch_india_vix()
            global_data = fetch_global_markets()
            fii_dii = fetch_fii_dii()

            shared_state.update({
                "spot": spot,
                "banknifty_spot": banknifty.get("value", 0),
                "signal": signal_data.get("signal", "WAIT"),
                "confidence": signal_data.get("confidence", 0),
                "checklist": signal_data.get("checklist", {}),
                "orb_high": signal_data.get("orb_high"),
                "orb_low": signal_data.get("orb_low"),
                "signal_note": signal_data.get("note", ""),
                "greeks": greeks_data,
                "oi_data": oi_data,
                "vix": vix_data,
                "global": global_data,
                "institutional_stats": {
                    "fii_buy": fii_dii["fii"]["buy"],
                    "fii_sell": fii_dii["fii"]["sell"],
                    "fii_net": fii_dii["fii"]["net"],
                    "dii_buy": fii_dii["dii"]["buy"],
                    "dii_sell": fii_dii["dii"]["sell"],
                    "dii_net": fii_dii["dii"]["net"],
                    "source": fii_dii["source"],
                    "status": "Unavailable on Cloud" if "BLOCKED" in fii_dii.get("source", "") else "Live",
                },
                "last_updated": datetime.now().isoformat(),
            })

            config.state_manager.set_state("latest_prices", {
                "nifty": spot,
                "banknifty": banknifty.get("value", 0),
            })
            config.state_manager.set_state("candle_store", candles)
            config.state_manager.set_state("signal_data", signal_data)
            config.state_manager.set_state("oi_data", oi_data)
            config.state_manager.set_state("greeks_data", greeks_data)
            config.state_manager.set_state("vix_data", vix_data)
            config.state_manager.set_state("global_data", global_data)
            config.state_manager.set_state("fii_dii", fii_dii)
            config.state_manager.last_data_update_time = datetime.now()

            logger.info(
                f"LIVE DATA | Spot: {spot} | Signal: {signal_data['signal']} | "
                f"OI: {oi_data.get('source')} | VIX: {vix_data.get('value')} | "
                f"Global: {global_data.get('source')} | FII/DII: {fii_dii.get('source')}"
            )

        except Exception as e:
            logger.error(f"Live data poller error: {e}")

        time.sleep(15)

    logger.info("Live data poller stopped")


def start_background_threads() -> None:
    global _poller_running
    if _poller_running:
        logger.info("Background poller already running")
        return
    _poller_running = True
    threading.Thread(target=_live_data_poller, daemon=True).start()
    logger.info("Strategy background threads initialized (live data poller).")
