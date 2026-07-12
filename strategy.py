"""
Strategy Engine — Price polling, indicators, signals, real data fetchers
"""

import os
import time
import math
import random
import threading
import logging
from datetime import datetime, timedelta
import requests
import yfinance as yf

import indicators
import trading
from angel_client import get_angel_client, get_ltp, get_candle_data, get_token
from config import SYMBOLS, AUTO_TRADE_ENABLED

logger = logging.getLogger(__name__)

# ─── Shared state (read by routes.py) ───────────────────────────────
shared_state = {
    "spot": 0,
    "day_open": 0,
    "banknifty": 0,
    "finnifty": 0,
    "sensex": 0,
    "crudeoil": 0,
    "gold": 0,
    "silver": 0,
    "usdinr": 0,
    "midcap": 0,
    "vix": 0,
    "market_status": "CLOSED",
    "risk_ok": True,
    "risk_message": "",
    "auto_trade": {"action": "disabled", "reason": "Off by default"},
    "session_pnl_rs": 0,
    "win_rate": 0,
    "total_trades": 0,
    "institutional_stats": {
        "fii_long": 0, "fii_short": 0, "fii_net": 0,
        "dii_long": 0, "dii_short": 0, "dii_net": 0,
        "win_rate": 0, "total_trades": 0, "status": "Live"
    },
    "options_contract": {
        "index": "NIFTY", "strike": 24250, "option_type": "PE",
        "symbol": "NIFTY 24250 PE", "premium_estimate": "Opens at 9:15 AM"
    },
    "oi_data": {"call_oi": 0, "put_oi": 0, "pcr": 0, "max_pain": 0},
    "greeks": {"iv": 0, "delta": 0, "theta": 0, "gamma": 0, "vega": 0},
    "alerts": [],
    "stocks": {},
    "indicators": {
        "rsi": 50, "ema9": 0, "ema21": 0, "vwap_approx": 0,
        "macd": {"macd": 0, "signal": 0},
        "supertrend": {"trend": "WAIT", "value": 0}
    },
    "global": {"kospi": 0, "nasdaq": 0, "dji": 0},
    "active_trade": None,
    "real_signal": {"signal": "WAIT", "confidence": 0, "checklist": {}, "note": ""},
    "last_update": "",
}

latest_prices = {}
_thread_running = False


# ─── Greeks (Black-Scholes Approx) ──────────────────────────────────
def calculate_greeks(spot, strike, option_type, dte=5, iv_percent=15):
    """Black-Scholes approximation for ATM options."""
    try:
        iv = iv_percent / 100
        t = max(dte, 0.5) / 365
        sqrt_t = math.sqrt(t)
        d1 = (math.log(spot / strike) + (0.07 + 0.5 * iv * iv) * t) / (iv * sqrt_t)
        d2 = d1 - iv * sqrt_t
        nd1 = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
        nd2 = 0.5 * (1 + math.erf(d2 / math.sqrt(2)))
        gamma = (1 / (spot * iv * sqrt_t)) * (1 / math.sqrt(2 * math.pi)) * math.exp(-d1 * d1 / 2)
        vega = spot * sqrt_t * gamma / 100
        theta = -(spot * iv * gamma * d1) / (2 * sqrt_t) / 365

        if option_type.upper() == "PE":
            delta = nd1 - 1
            theta = abs(theta)
        else:
            delta = nd1

        return {
            "iv": round(iv_percent, 2),
            "delta": round(delta, 4),
            "theta": round(theta, 2),
            "gamma": round(gamma, 6),
            "vega": round(vega, 2),
            "source": "BS_APPROX"
        }
    except Exception:
        return {"iv": 0, "delta": 0, "theta": 0, "gamma": 0, "vega": 0, "source": "BS_APPROX"}


# ─── Real Data Fetchers ────────────────────────────────────────────
def _fetch_global_indices():
    """Fetch global indices from Yahoo Finance."""
    result = {"kospi": 0, "nasdaq": 0, "dji": 0}
    try:
        for name, ticker in [("kospi", "^KS11"), ("nasdaq", "^IXIC"), ("dji", "^DJI")]:
            t = yf.Ticker(ticker)
            d = t.fast_info
            val = getattr(d, "last_price", None)
            if val and val > 0:
                result[name] = round(val, 2)
            else:
                h = t.history(period="1d")
                if not h.empty:
                    result[name] = round(h["Close"].iloc[-1], 2)
    except Exception as e:
        logger.error(f"Global indices error: {e}")
    result["source"] = "YAHOO_FINANCE"
    return result


def _fetch_real_option_chain(spot):
    """Fetch OI data from NSE India option chain."""
    result = {"call_oi": 0, "put_oi": 0, "pcr": 0, "max_pain": 0, "source": "FALLBACK_SPOT_ONLY"}
    try:
        url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "application/json",
        }
        s = requests.Session()
        s.get("https://www.nseindia.com", headers=headers, timeout=5)
        resp = s.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            records = data.get("records", {}).get("data", [])
            if not records:
                return result
            total_ce_oi = 0
            total_pe_oi = 0
            pain_map = {}
            for r in records:
                ce = r.get("CE", {})
                pe = r.get("PE", {})
                strike = int(r.get("strikePrice", 0))
                ce_oi = int(ce.get("openInterest", 0) or 0)
                pe_oi = int(pe.get("openInterest", 0) or 0)
                total_ce_oi += ce_oi
                total_pe_oi += pe_oi
                pain_map[strike] = pain_map.get(strike, 0) + ce_oi + pe_oi
            max_pain = max(pain_map, key=pain_map.get) if pain_map else round(spot / 50) * 50
            pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi > 0 else 0
            result = {
                "call_oi": total_ce_oi,
                "put_oi": total_pe_oi,
                "pcr": pcr,
                "max_pain": max_pain,
                "source": "NSE_LIVE"
            }
    except Exception as e:
        logger.error(f"Option chain error: {e}")
    # Fallback max_pain from spot
    if result.get("max_pain", 0) == 0 and spot > 0:
        result["max_pain"] = round(spot / 50) * 50
    return result


def _fetch_real_fii_dii():
    """Fetch FII/DII data from NSE India."""
    result = {
        "fii_long": 0, "fii_short": 0, "fii_net": 0,
        "dii_long": 0, "dii_short": 0, "dii_net": 0,
        "status": "Live"
    }
    try:
        url = "https://www.nseindia.com/api/fiidiiTradeRpt"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "application/json",
        }
        s = requests.Session()
        s.get("https://www.nseindia.com", headers=headers, timeout=5)
        resp = s.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for day in data[:1]:
                result["fii_long"] = float(day.get("FIINetBuy", {}).get("grossBuy", 0) or 0)
                result["fii_short"] = float(day.get("FIINetBuy", {}).get("grossSell", 0) or 0)
                result["fii_net"] = float(day.get("FIINetBuy", {}).get("netBuy", 0) or 0)
                result["dii_long"] = float(day.get("DIINetBuy", {}).get("grossBuy", 0) or 0)
                result["dii_short"] = float(day.get("DIINetBuy", {}).get("grossSell", 0) or 0)
                result["dii_net"] = float(day.get("DIINetBuy", {}).get("netBuy", 0) or 0)
                result["status"] = "Live (NSE)"
    except Exception as e:
        logger.error(f"FII/DII error: {e}")
    return result


# ─── ORB Calculation ────────────────────────────────────────────────
_orb_high = None
_orb_low = None
_orb_set = False


def _update_orb(candles):
    """Update ORB high/low from first 15-min candles."""
    global _orb_high, _orb_low, _orb_set
    if _orb_set or not candles:
        return
    now = datetime.now()
    if now.hour > 9 or (now.hour == 9 and now.minute > 20):
        # Use first 15 min candles
        first_candles = [c for c in candles if 540 <= c[0] // 60 <= 555]
        if first_candles:
            _orb_high = max(c[2] for c in first_candles)
            _orb_low = min(c[3] for c in first_candles)
            _orb_set = True
            logger.info(f"ORB set: H={_orb_high} L={_orb_low}")


def compute_real_signal(candles, spot):
    """Compute GOAT signal from indicators."""
    if not candles or len(candles) < 21:
        return {"signal": "WAIT", "confidence": 0, "checklist": {}, "note": "Not enough candles"}

    closes = [c[4] for c in candles]
    highs = [c[2] for c in candles]
    lows = [c[3] for c in candles]

    rsi = indicators.calc_rsi(closes, 14)
    ema9 = indicators.calc_ema(closes, 9)
    ema21 = indicators.calc_ema(closes, 21)
    vwap = indicators.calc_vwap_approx(candles)
    macd_line, signal_line = indicators.calc_macd(closes)
    st_trend, st_val = indicators.calc_supertrend(highs, lows, closes, 10, 3)

    # Checklist
    orb_breakdown = _orb_low and spot < _orb_low
    orb_breakout = _orb_high and spot > _orb_high
    below_vwap = spot < vwap
    above_vwap = spot > vwap
    ema_bearish = ema9 < ema21
    ema_bullish = ema9 > ema21
    rsi_not_oversold = rsi > 30
    rsi_not_overbought = rsi < 70
    macd_bearish = macd_line < signal_line
    macd_bullish = macd_line > signal_line
    supertrend_sell = st_trend == "SELL"
    supertrend_buy = st_trend == "BUY"

    short_score = sum([orb_breakdown, below_vwap, ema_bearish, rsi_not_oversold, macd_bearish, supertrend_sell])
    long_score = sum([orb_breakout, above_vwap, ema_bullish, rsi_not_overbought, macd_bullish, supertrend_buy])

    if short_score >= 4:
        sig = "SHORT"
        conf = short_score
        checklist = {
            "orb_breakdown": orb_breakdown, "below_vwap": below_vwap,
            "ema_bearish": ema_bearish, "rsi_not_oversold": rsi_not_oversold,
            "macd_bearish": macd_bearish, "supertrend_sell": supertrend_sell
        }
        note = f"{conf}/6 checks agree"
        if orb_breakdown:
            note += " — ORB breakdown confirmed"
        else:
            note += " — trend confirmed (no ORB)"
    elif long_score >= 4:
        sig = "LONG"
        conf = long_score
        checklist = {
            "orb_breakout": orb_breakout, "above_vwap": above_vwap,
            "ema_bullish": ema_bullish, "rsi_not_overbought": rsi_not_overbought,
            "macd_bullish": macd_bullish, "supertrend_buy": supertrend_buy
        }
        note = f"{conf}/6 checks agree"
    else:
        sig = "WAIT"
        conf = max(short_score, long_score)
        checklist = {}
        note = f"No strong signal (short={short_score}, long={long_score})"

    # Update shared state indicators
    shared_state["indicators"] = {
        "rsi": round(rsi, 1),
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "vwap_approx": round(vwap, 2),
        "macd": {"macd": round(macd_line, 2), "signal": round(signal_line, 2)},
        "supertrend": {"trend": st_trend, "value": round(st_val, 2)}
    }

    return {"signal": sig, "confidence": conf, "checklist": checklist, "note": note}


# ─── Price Poller (runs every 5s) ──────────────────────────────────
def price_poller():
    """Background thread — fetch all prices every 5 seconds."""
    global latest_prices
    client = get_angel_client()

    while _thread_running:
        try:
            for name, cfg in SYMBOLS.items():
                exchange = cfg["exchange"]
                symbol = cfg["symbol"]
                val = get_ltp(exchange, symbol)
                if val and val > 0:
                    latest_prices[name] = val
                    shared_state[name] = val

            # Set day_open on first valid NIFTY price
            if shared_state.get("day_open", 0) == 0 and latest_prices.get("nifty"):
                shared_state["day_open"] = latest_prices["nifty"]

            shared_state["spot"] = latest_prices.get("nifty", 0)

            # Market status
            now = datetime.now()
            if now.hour == 9 and now.minute < 15:
                shared_state["market_status"] = "PRE_MARKET"
            elif 9 <= now.hour <= 15 and (now.hour < 15 or (now.hour == 15 and now.minute <= 30)):
                shared_state["market_status"] = "OPEN"
            else:
                shared_state["market_status"] = "CLOSED"

            shared_state["last_update"] = datetime.now().isoformat()

        except Exception as e:
            logger.error(f"Price poller error: {e}")

        time.sleep(5)


# ─── Indicator Poller (runs every 180s) ────────────────────────────
def indicator_poller(db_session_factory=None):
    """Background thread — compute indicators and signal every 3 minutes."""
    global _orb_set
    # Reset ORB daily
    _orb_set = False

    while _thread_running:
        try:
            spot = shared_state.get("spot", 0)
            if spot <= 0:
                time.sleep(180)
                continue

            # Fetch candles
            candles = get_candle_data("NFO", "NIFTY26JULFUT", "ONE_MINUTE", 1)
            if not candles:
                candles = get_candle_data("NSE", "NIFTY 50", "ONE_MINUTE", 1)

            if candles and len(candles) >= 21:
                _update_orb(candles)
                signal = compute_real_signal(candles, spot)
                shared_state["real_signal"] = signal

                # Auto-trade
                if AUTO_TRADE_ENABLED and db_session_factory:
                    db = db_session_factory()
                    try:
                        trading.process_auto_signal(db)
                    finally:
                        db.close()

            # Greeks
            oi = shared_state.get("oi_data", {})
            strike = oi.get("max_pain", round(spot / 50) * 50)
            greeks = calculate_greeks(spot=spot, strike=strike, option_type="PE", dte=5, iv_percent=15)
            shared_state["greeks"] = greeks

            # Options contract
            shared_state["options_contract"] = {
                "index": "NIFTY",
                "strike": strike,
                "option_type": "PE",
                "symbol": f"NIFTY {strike} PE",
                "premium_estimate": "Opens at 9:15 AM"
            }

            # OI data
            oi_data = _fetch_real_option_chain(spot)
            shared_state["oi_data"] = oi_data

            # FII/DII
            fii_dii = _fetch_real_fii_dii()
            shared_state["institutional_stats"] = fii_dii

            # Global indices
            globals_data = _fetch_global_indices()
            shared_state["global"] = globals_data

            # Max pain alert
            max_pain = oi_data.get("max_pain", 0)
            if max_pain and spot > 0 and abs(spot - max_pain) < 15:
                alert = {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "badge": "INFO",
                    "msg": f"Market near max pain {max_pain}",
                    "px": spot
                }
                alerts = shared_state.get("alerts", [])
                alerts.insert(0, alert)
                shared_state["alerts"] = alerts[:10]

        except Exception as e:
            logger.error(f"Indicator poller error: {e}")

        time.sleep(180)


# ─── Thread Management ──────────────────────────────────────────────
def start_background_threads(db_session_factory=None):
    """Start price and indicator poller threads."""
    global _thread_running
    if _thread_running:
        return

    _thread_running = True

    t1 = threading.Thread(target=price_poller, daemon=True, name="price_poller")
    t1.start()

    t2 = threading.Thread(target=indicator_poller, args=(db_session_factory,), daemon=True, name="indicator_poller")
    t2.start()

    logger.info("Background pollers started")


def stop_background_threads():
    """Stop all background threads."""
    global _thread_running
    _thread_running = False
    logger.info("Background pollers stopped")
