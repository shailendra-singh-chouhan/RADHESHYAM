"""
strategy.py — Signal generation, OI analysis, Greeks, Institutional stats

Phase 3.B2: _fetch_real_option_chain() now returns top_strikes, expiry, strike_count
"""

import logging
import math
from datetime import datetime
from typing import Optional, Dict, Any, List

import requests

from angel_client import get_angel_client

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════
# SIGNAL GENERATION — 6-check GOAT system
# ════════════════════════════════════════════════════════

def generate_signal(candles: List[dict], spot: float) -> dict:
    """
    Generate GOAT Signal based on 6 conditions:

    Bearish checks (for SHORT):
      orb_breakdown, below_vwap, ema_bearish,
      rsi_not_oversold, macd_bearish, supertrend_sell

    Bullish checks (for LONG):
      orb_breakout, above_vwap, ema_bullish,
      rsi_not_overbought, macd_bullish, supertrend_buy

    Majority wins → signal + confidence = count of True checks.
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
        "orb_high": 0,
        "orb_low": 0,
        "note": "",
    }

    if not candles or len(candles) < 21:
        signal["note"] = "Not enough candles for signal"
        return signal

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    # ── Import indicators here to avoid circular import ──
    from indicators import (
        calculate_rsi,
        calculate_ema,
        calculate_vwap,
        calculate_macd,
        calculate_supertrend,
    )

    # Calculate indicators
    rsi_val = calculate_rsi(closes, period=14)
    ema9 = calculate_ema(closes, period=9)
    ema21 = calculate_ema(closes, period=21)
    vwap_val = calculate_vwap(candles)
    macd_data = calculate_macd(closes)
    st_data = calculate_supertrend(highs, lows, closes, period=10, multiplier=3)

    # ── ORB (Opening Range Breakout) ──
    first_time = candles[0]["time"]
    orb_candles = [c for c in candles if c["time"] <= first_time + 900]  # 15 min
    if orb_candles:
        orb_high = max(c["high"] for c in orb_candles)
        orb_low = min(c["low"] for c in orb_candles)
        signal["orb_high"] = round(orb_high, 2)
        signal["orb_low"] = round(orb_low, 2)

    # ── Evaluate 6 bearish checks ──
    bear_checks = {
        "orb_breakdown": (signal["orb_low"] > 0 and spot < signal["orb_low"]),
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

    # ── Evaluate 6 bullish checks ──
    bull_checks = {
        "orb_breakout": (signal["orb_high"] > 0 and spot > signal["orb_high"]),
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
        signal["note"] = (
            f"{bear_count}/6 checks agree — ORB breakdown + trend confirmed"
        )
    elif bull_count >= 4 and bull_count > bear_count:
        signal["signal"] = "LONG"
        signal["confidence"] = bull_count
        # For LONG, map to same keys but with bullish meanings
        signal["checklist"] = {
            "orb_breakdown": bull_checks["orb_breakout"],
            "below_vwap": bull_checks["above_vwap"],
            "ema_bearish": bull_checks["ema_bullish"],
            "rsi_not_oversold": bull_checks["rsi_not_overbought"],
            "macd_bearish": bull_checks["macd_bullish"],
            "supertrend_sell": bull_checks["supertrend_buy"],
        }
        signal["note"] = (
            f"{bull_count}/6 checks agree — ORB breakout + trend confirmed"
        )
    else:
        signal["confidence"] = max(bear_count, bull_count)
        signal["note"] = (
            f"No strong signal (bull={bull_count}, bear={bear_count})"
        )

    return signal


# ════════════════════════════════════════════════════════
# OPTION CHAIN / OI DATA
# ════════════════════════════════════════════════════════

def _fetch_real_option_chain(index: str = "NIFTY") -> dict:
    """
    Fetch real OI data from NSE India.

    Phase 3.B2: NOW returns expiry, top_strikes (top 7 by OI), strike_count.
    Falls back to _fallback_oi_data() on failure.
    """
    client = get_angel_client()
    data = client.fetch_nse_option_chain(index)

    if data.get("source") == "NSE_LIVE":
        return data

    # Fallback
    logger.warning("NSE chain failed, using fallback OI")
    return _fallback_oi_data()


def _fallback_oi_data() -> dict:
    """Rough OI estimates when NSE is unreachable."""
    return {
        "call_oi": 4500000,
        "put_oi": 5200000,
        "pcr": 1.16,
        "max_pain": 24000,
        "expiry": None,
        "top_strikes": [],
        "strike_count": 0,
        "source": "FALLBACK_SPOT_ONLY",
    }


def get_oi_data(index: str = "NIFTY") -> dict:
    """Public entry point — always returns a valid dict."""
    return _fetch_real_option_chain(index)


# ════════════════════════════════════════════════════════
# GREEKS (Black-Scholes approximation)
# ════════════════════════════════════════════════════════

def _norm_cdf(x: float) -> float:
    """Cumulative standard normal distribution."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def calculate_greeks(
    spot: float,
    strike: float,
    option_type: str = "PE",
    days_to_expiry: float = 5.0,
    risk_free_rate: float = 0.07,
    iv_percent: float = 13.0,
) -> dict:
    """
    Black-Scholes Greeks approximation.

    Returns: {iv, delta, gamma, theta, vega, source: "BS_APPROX"}
    """
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


# ════════════════════════════════════════════════════════
# INSTITUTIONAL STATS (FII / DII)
# ════════════════════════════════════════════════════════

def fetch_institutional_stats() -> dict:
    """
    Fetch FII/DII long/short ratios from NSE India.
    Falls back to approximate values.
    """
    result = {
        "fii_long": 0.17,
        "fii_short": 0.16,
        "fii_net": 0.02,
        "dii_long": 0.19,
        "dii_short": 0.18,
        "dii_net": 0.01,
        "win_rate": 0.0,
        "total_trades": 0,
        "status": "Fallback",
        "source": "FALLBACK",
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
            # NSE FII/DII API format varies — try common keys
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


# ════════════════════════════════════════════════════════
# GLOBAL MARKETS (Yahoo Finance)
# ════════════════════════════════════════════════════════

def fetch_global_markets() -> dict:
    """Fetch NASDAQ, DOW, KOSPI from Yahoo Finance."""
    result = {
        "kospi": 0.0,
        "nasdaq": 0.0,
        "dji": 0.0,
        "source": "YAHOO_FINANCE",
    }

    tickers = {
        "nasdaq": "^IXIC",
        "dji": "^DJI",
        "kospi": "^KS11",
    }

    try:
        import yfinance as yf

        for key, ticker in tickers.items():
            try:
                t = yf.Ticker(ticker)
                info = t.fast_info
                price = info.get("last_price", 0)
                if price and price > 0:
                    result[key] = round(float(price), 2)
            except Exception:
                pass

    except ImportError:
        logger.warning("yfinance not installed, global markets will be 0")
    except Exception as e:
        logger.error("Global markets error: %s", e)

    return result
