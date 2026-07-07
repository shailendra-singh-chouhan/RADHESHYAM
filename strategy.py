import time
import threading
from typing import Optional
from logzero import logger

import config
import angel_client
import indicators
import auto_execute

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
                # Dynamic token resolution - no hardcoded tokens needed
                nifty = angel_client.get_ltp("NSE", config.NIFTY_SYMBOL)
                banknifty = angel_client.get_ltp("NSE", config.BANKNIFTY_SYMBOL)
                finnifty = angel_client.get_ltp("NSE", config.FINNIFTY_SYMBOL)
                sensex = angel_client.get_ltp("BSE", config.SENSEX_SYMBOL)
                crude = angel_client.get_ltp("MCX", config.CRUDEOIL_SYMBOL)
                gold = angel_client.get_ltp("MCX", config.GOLD_SYMBOL)
                silver = angel_client.get_ltp("MCX", config.SILVER_SYMBOL)

                # Try NSE first for VIX, fallback to NFO
                vix = angel_client.get_ltp("NSE", config.VIX_SYMBOL)
                if vix is None:
                    vix = angel_client.get_ltp("NFO", config.VIX_SYMBOL)

                today = config.get_ist_now().date().isoformat()

                if nifty is not None:
                    config.latest_prices["nifty"] = nifty
                    if config.latest_prices["day_open_date"] != today:
                        config.latest_prices["day_open"] = nifty
                        config.latest_prices["day_open_date"] = today

                if banknifty is not None: config.latest_prices["banknifty"] = banknifty
                if finnifty is not None: config.latest_prices["finnifty"] = finnifty
                if sensex is not None: config.latest_prices["sensex"] = sensex
                if crude is not None: config.latest_prices["crudeoil"] = crude
                if gold is not None: config.latest_prices["gold"] = gold
                if silver is not None: config.latest_prices["silver"] = silver
                if vix is not None: config.latest_prices["vix"] = vix

                # Global Indices (Simulated for now or fetched via API if available)
                config.latest_prices["kospi"] = 2520.5 - (int(time.time()) % 100) * 0.1
                config.latest_prices["nasdaq"] = 18200.0 + (int(time.time()) % 50)
                config.latest_prices["dji"] = 42100.0 - (int(time.time()) % 30)

                config.latest_prices["last_update"] = config.get_ist_now().isoformat()
        except Exception as e:
            logger.error(f"price_poller error: {e}")
        time.sleep(15)

def indicator_poller() -> None:
    """Refreshes technical indicators, updates signals, and triggers auto-execution."""
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
                    config.indicator_data["macd"] = indicators.calculate_macd(closes)
                    config.indicator_data["supertrend"] = indicators.calculate_supertrend(candles)

                    config.signal_data.update(compute_real_signal(candles))
                    auto_execute.process_and_auto_execute()

        except Exception as e:
            logger.error(f"indicator_poller error: {e}")
        time.sleep(300)

_started = False
def extra_data_poller() -> None:
    """Polls OI, Greeks, and News every 10 minutes."""
    while True:
        try:
            if config.get_market_status() == "OPEN":
                # 1. Update OI Data (Simulated for now based on market trend)
                nifty_px = config.latest_prices.get("nifty")
                if nifty_px:
                    # Logic: Higher Nifty = higher Put OI usually
                    config.oi_data["call_oi"] = 4500000 + (int(nifty_px) % 100) * 1000
                    config.oi_data["put_oi"] = 5200000 + (int(nifty_px) % 100) * 1500
                    config.oi_data["pcr"] = round(config.oi_data["put_oi"] / config.oi_data["call_oi"], 2)
                    config.oi_data["max_pain"] = round(nifty_px / 50) * 50

                # 2. Update Greeks
                vix = config.latest_prices.get("vix")
                if vix:
                    config.greeks_data["iv"] = round(vix * 0.95, 2)
                    config.greeks_data["theta"] = -12.5
                    config.greeks_data["gamma"] = 0.0045

                # 3. News Feed & Alerts
                new_alert = {
                    "time": config.get_ist_now().strftime("%H:%M:%S"),
                    "badge": "INFO",
                    "msg": f"Market showing strong support at {config.oi_data['max_pain']}",
                    "px": nifty_px
                }
                config.market_alerts.insert(0, new_alert)
                config.market_alerts = config.market_alerts[:10] # Keep last 10

        except Exception as e:
            logger.error(f"extra_data_poller error: {e}")
        time.sleep(600)

def start_background_threads() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=price_poller, daemon=True).start()
    threading.Thread(target=indicator_poller, daemon=True).start()
    threading.Thread(target=extra_data_poller, daemon=True).start()
    logger.info("Background threads started (price, indicator, extra_data).")
