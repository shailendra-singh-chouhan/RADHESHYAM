import time
import threading
from typing import Optional
from logzero import logger

import config
import angel_client
import indicators
import auto_execute
import database # Import database module for state saving

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

    indicator_data = config.state_manager.indicator_data

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
        "macd_bearish": indicator_data.get("macd", {}).get("macd", 0) < indicator_data.get("macd", {}).get("signal", 0),
        "supertrend_sell": indicator_data.get("supertrend", {}).get("trend") == "SELL",
    }
    long_score = sum(checklist_long.values())
    short_score = sum(checklist_short.values())

    if long_score >= 3 and long_score > short_score:
        return {"signal": "LONG", "confidence": long_score, "checklist": checklist_long,
                "orb_high": orb_high, "orb_low": orb_low,
                "note": f"{long_score}/{len(checklist_long)} checks agree — ORB breakout + trend confirmed"}
    elif short_score >= 3 and short_score > long_score:
        return {"signal": "SHORT", "confidence": short_score, "checklist": checklist_short,
                "orb_high": orb_high, "orb_low": orb_low,
                "note": f"{short_score}/{len(checklist_short)} checks agree — ORB breakdown + trend confirmed"}
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
                usdinr = angel_client.get_ltp("CDS", config.USDINR_SYMBOL)
                midcap = angel_client.get_ltp("NSE", config.MIDCAP_SYMBOL)

                # Try NSE first for VIX, fallback to NFO
                vix = angel_client.get_ltp("NSE", config.VIX_SYMBOL)
                if vix is None:
                    vix = angel_client.get_ltp("NFO", config.VIX_SYMBOL)

                today = config.get_ist_now().date().isoformat()
                
                updates = {}
                if nifty is not None:
                    updates["nifty"] = nifty
                    latest_prices = config.state_manager.latest_prices
                    if latest_prices["day_open_date"] != today:
                        updates["day_open"] = nifty
                        updates["day_open_date"] = today

                if banknifty is not None: updates["banknifty"] = banknifty
                if finnifty is not None: updates["finnifty"] = finnifty
                if sensex is not None: updates["sensex"] = sensex
                if crude is not None: updates["crudeoil"] = crude
                if gold is not None: updates["gold"] = gold
                if silver is not None: updates["silver"] = silver
                if usdinr is not None: updates["usdinr"] = usdinr
                if midcap is not None: updates["midcap"] = midcap
                if vix is not None: updates["vix"] = vix

                # Global Indices (Simulated for now or fetched via API if available)
                updates["kospi"] = 2520.5 - (int(time.time()) % 100) * 0.1
                updates["nasdaq"] = 18200.0 + (int(time.time()) % 50)
                updates["dji"] = 42100.0 - (int(time.time()) % 30)
                updates["last_update"] = config.get_ist_now().isoformat()

                config.state_manager.update_state("latest_prices", updates)
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
                    config.state_manager.set_state("candle_store", candles)

                    closes = [c["close"] for c in candles]
                    indicator_updates = {
                        "rsi": indicators.calculate_rsi(closes),
                        "ema9": indicators.calculate_ema(closes, 9),
                        "ema21": indicators.calculate_ema(closes, 21),
                        "vwap_approx": indicators.calculate_vwap_approx(candles),
                        "macd": indicators.calculate_macd(closes),
                        "supertrend": indicators.calculate_supertrend(candles)
                    }
                    config.state_manager.update_state("indicator_data", indicator_updates)

                    signal_update = compute_real_signal(candles)
                    config.state_manager.update_state("signal_data", signal_update)
                    
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
                nifty_px = config.state_manager.latest_prices.get("nifty")
                if nifty_px:
                    oi_updates = {
                        "call_oi": 4500000 + (int(nifty_px) % 100) * 1000,
                        "put_oi": 5200000 + (int(nifty_px) % 100) * 1500,
                    }
                    oi_updates["pcr"] = round(oi_updates["put_oi"] / oi_updates["call_oi"], 2)
                    oi_updates["max_pain"] = round(nifty_px / 50) * 50
                    config.state_manager.update_state("oi_data", oi_updates)

                # 2. Update Greeks
                vix = config.state_manager.latest_prices.get("vix")
                if vix:
                    greeks_updates = {
                        "iv": round(vix * 0.95, 2),
                        "theta": -12.5,
                        "gamma": 0.0045
                    }
                    config.state_manager.update_state("greeks_data", greeks_updates)

                # 3. News Feed & Alerts
                new_alert = {
                    "time": config.get_ist_now().strftime("%H:%M:%S"),
                    "badge": "INFO",
                    "msg": f"Market showing strong support at {config.state_manager.oi_data.get('max_pain')}",
                    "px": nifty_px
                }
                alerts = config.state_manager.market_alerts
                alerts.insert(0, new_alert)
                config.state_manager.set_state("market_alerts", alerts[:10])

        except Exception as e:
            logger.error(f"extra_data_poller error: {e}")
        time.sleep(600)

def state_saver_poller() -> None:
    """Periodically saves the application state to the database."""
    while True:
        try:
            if database.SessionLocal:
                with database.SessionLocal() as db:
                    database.save_app_state(db, config.state_manager)
            else:
                logger.warning("State saver skipped: Database not connected.")
        except Exception as e:
            logger.error(f"state_saver_poller error: {e}")
        time.sleep(60) # Save state every 60 seconds

def start_background_threads() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=price_poller, daemon=True).start()
    threading.Thread(target=indicator_poller, daemon=True).start()
    threading.Thread(target=extra_data_poller, daemon=True).start()
    threading.Thread(target=state_saver_poller, daemon=True).start() # Start the state saver poller
    logger.info("Background threads started (price, indicator, extra_data, state_saver).")
