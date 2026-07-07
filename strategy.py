import time
import threading
from typing import Optional
from logzero import logger

import config
import angel_client
import indicators

candle_lock = threading.Lock()

def compute_orb_range(candles: list) -> tuple[Optional[float], Optional[float]]:
    """Opening Range = high/low of candles between 9:15 and 9:30 AM."""
    orb_candles = [c for c in candles if "09:15" <= c["time"][11:16] <= "09:30"]
    if not orb_candles:
        return None, None
    return max(c["high"] for c in orb_candles), min(c["low"] for c in orb_candles)

def compute_real_signal(candles: list) -> dict:
    """Relaxed checklist strategy: needs 3-of-4 indicators confirmation."""
    if not candles or len(candles) < 16:
        return {"signal": "WAIT", "confidence": 0, "checklist": {},
                "orb_high": None, "orb_low": None, "note": "Not enough candles yet"}

    closes = [c["close"] for c in candles]
    current_price = closes[-1]
    orb_high, orb_low = compute_orb_range(candles)
    ema9 = indicators.calculate_ema(closes, 9)
    ema21 = indicators.calculate_ema(closes, 21)
    rsi = indicators.calculate_rsi(closes)
    vwap = indicators.calculate_vwap_approx(candles)

    if orb_high is None or ema9 is None or ema21 is None or rsi is None or vwap is None:
        return {"signal": "WAIT", "confidence": 0, "checklist": {},
                "orb_high": orb_high, "orb_low": orb_low, "note": "Waiting for opening range (9:15-9:30)"}

    checklist_long = {
        "orb_breakout": current_price > orb_high,
        "above_vwap": current_price > vwap,
        "ema_bullish": ema9 > ema21,
        "rsi_not_overbought": rsi < 70,
    }
    checklist_short = {
        "orb_breakdown": current_price < orb_low,
        "below_vwap": current_price < vwap,
        "ema_bearish": ema9 < ema21,
        "rsi_not_oversold": rsi > 30,
    }
    long_score = sum(checklist_long.values())
    short_score = sum(checklist_short.values())

    if long_score >= 3 and long_score > short_score:
        return {"signal": "LONG", "confidence": long_score, "checklist": checklist_long,
                "orb_high": orb_high, "orb_low": orb_low,
                "note": f"{long_score}/4 checks agree — ORB breakout + trend confirmed"}
    elif short_score >= 3 and short_score > long_score:
        return {"signal": "SHORT", "confidence": short_score, "checklist": checklist_short,
                "orb_high": orb_high, "orb_low": orb_low,
                "note": f"{short_score}/4 checks agree — ORB breakdown + trend confirmed"}
    else:
        return {"signal": "WAIT", "confidence": max(long_score, short_score),
                "checklist": checklist_long if long_score >= short_score else checklist_short,
                "orb_high": orb_high, "orb_low": orb_low,
                "note": "Checklist not aligned (need 3-of-4) — no clear setup"}

def price_poller() -> None:
    """Refreshes live prices every 15 seconds during market hours."""
    while True:
        try:
            if config.get_market_status() in ("OPEN", "PRE_OPEN"):
                nifty = angel_client.get_ltp("NSE", config.NIFTY_SYMBOL, config.NIFTY_TOKEN)
                banknifty = angel_client.get_ltp("NSE", config.BANKNIFTY_SYMBOL, config.BANKNIFTY_TOKEN)
                vix = angel_client.get_ltp("NSE", config.VIX_SYMBOL, config.VIX_TOKEN)
                today = config.get_ist_now().date().isoformat()
                
                if nifty is not None:
                    config.latest_prices["nifty"] = nifty
                    if config.latest_prices["day_open_date"] != today:
                        config.latest_prices["day_open"] = nifty
                        config.latest_prices["day_open_date"] = today
                
                if banknifty is not None:
                    config.latest_prices["banknifty"] = banknifty
                    
                if vix is not None:
                    config.latest_prices["vix"] = vix
                
                config.latest_prices["last_update"] = config.get_ist_now().isoformat()
        except Exception as e:
            logger.error(f"price_poller error: {e}")
        time.sleep(15)

def indicator_poller() -> None:
    """Refreshes technical indicators and signals every 5 minutes."""
    while True:
        try:
            if config.get_market_status() == "OPEN":
                candles = angel_client.fetch_todays_candles()
                if candles and len(candles) >= 15:
                    with candle_lock:
                        config.candle_store.clear()
                        config.candle_store.extend(candles)
                    closes = [c["close"] for c in candles]
                    config.indicator_data["rsi"] = indicators.calculate_rsi(closes)
                    config.indicator_data["ema9"] = indicators.calculate_ema(closes, 9)
                    config.indicator_data["ema21"] = indicators.calculate_ema(closes, 21)
                    config.indicator_data["vwap_approx"] = indicators.calculate_vwap_approx(candles)
                    config.signal_data.update(compute_real_signal(candles))
        except Exception as e:
            logger.error(f"indicator_poller error: {e}")
        time.sleep(300)

_started = False
def start_background_threads() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=price_poller, daemon=True).start()
    threading.Thread(target=indicator_poller, daemon=True).start()
    logger.info("Background threads started (price_poller, indicator_poller).")
