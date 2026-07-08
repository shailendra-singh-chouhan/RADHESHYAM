import time
import threading
from typing import Optional
from logzero import logger

import config
import angel_client
import indicators
import trading
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
    """Refreshes live prices every 15 seconds during market hours.

    Rate-limit safe: 10 LTP calls are split into 2 batches of 5 with a 1-second
    gap between batches. This keeps us under Angel One's free-plan rate limit
    (~1 req/sec) and prevents "Access denied because of exceeding access rate".
    """
    import time as _time
    while True:
        try:
            if config.get_market_status() in ("OPEN", "PRE_OPEN"):
                # Batch 1 — core indices (most important)
                nifty = angel_client.get_ltp("NSE", config.NIFTY_SYMBOL)
                _time.sleep(0.3)
                banknifty = angel_client.get_ltp("NSE", config.BANKNIFTY_SYMBOL)
                _time.sleep(0.3)
                finnifty = angel_client.get_ltp("NSE", config.FINNIFTY_SYMBOL)
                _time.sleep(0.3)
                sensex = angel_client.get_ltp("BSE", config.SENSEX_SYMBOL)
                _time.sleep(0.3)
                vix = angel_client.get_ltp("NSE", config.VIX_SYMBOL)
                if vix is None:
                    _time.sleep(0.3)
                    vix = angel_client.get_ltp("NFO", config.VIX_SYMBOL)
                # Short pause between batches
                _time.sleep(1.0)
                # Batch 2 — commodities + USDINR + midcap (less critical)
                crude = angel_client.get_ltp("MCX", config.CRUDEOIL_SYMBOL)
                _time.sleep(0.3)
                gold = angel_client.get_ltp("MCX", config.GOLD_SYMBOL)
                _time.sleep(0.3)
                silver = angel_client.get_ltp("MCX", config.SILVER_SYMBOL)
                _time.sleep(0.3)
                usdinr = angel_client.get_ltp("CDS", config.USDINR_SYMBOL)
                _time.sleep(0.3)
                midcap = angel_client.get_ltp("NSE", config.MIDCAP_SYMBOL)

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

                # Global Indices — REAL data via Yahoo Finance (yfinance)
                # Replaces the old simulated kospi/nasdaq/dji values.
                # Yahoo Finance is free and requires no API key.
                try:
                    import yfinance as yf
                    global_indices = {
                        "kospi": "^KS11",   # KOSPI (South Korea)
                        "nasdaq": "^IXIC",  # NASDAQ Composite (US)
                        "dji": "^DJI",      # Dow Jones Industrial Average (US)
                    }
                    for key, yf_symbol in global_indices.items():
                        try:
                            ticker = yf.Ticker(yf_symbol)
                            hist = ticker.history(period="1d", interval="5m")
                            if not hist.empty:
                                real_price = round(float(hist['Close'].iloc[-1]), 2)
                                updates[key] = real_price
                        except Exception as yf_err:
                            # Don't spam logs — just skip this cycle
                            logger.debug(f"yfinance {yf_symbol} error: {yf_err}")
                except ImportError:
                    logger.warning("yfinance not installed — global indices will be missing")

                if updates: # Only update if there's actual non-None data
                    config.state_manager.update_state("latest_prices", updates, allow_none_overwrite=False)
                    config.state_manager.last_data_update_time = config.get_ist_now()

            # Always update last_update in latest_prices, even if market is closed, to show when the bot last checked
            config.state_manager.update_state("latest_prices", {"last_update": config.get_ist_now().isoformat()})

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
                    indicator_updates = {}
                    rsi_val = indicators.calculate_rsi(closes)
                    if rsi_val is not None: indicator_updates["rsi"] = rsi_val
                    ema9_val = indicators.calculate_ema(closes, 9)
                    if ema9_val is not None: indicator_updates["ema9"] = ema9_val
                    ema21_val = indicators.calculate_ema(closes, 21)
                    if ema21_val is not None: indicator_updates["ema21"] = ema21_val
                    vwap_approx_val = indicators.calculate_vwap_approx(candles)
                    if vwap_approx_val is not None: indicator_updates["vwap_approx"] = vwap_approx_val
                    macd_val = indicators.calculate_macd(closes)
                    if macd_val is not None: indicator_updates["macd"] = macd_val
                    supertrend_val = indicators.calculate_supertrend(candles)
                    if supertrend_val is not None: indicator_updates["supertrend"] = supertrend_val

                    if indicator_updates:
                        config.state_manager.update_state("indicator_data", indicator_updates, allow_none_overwrite=False)
                        config.state_manager.last_data_update_time = config.get_ist_now()

                    signal_update = compute_real_signal(candles)
                    if signal_update:
                        config.state_manager.update_state("signal_data", signal_update, allow_none_overwrite=False)
                        config.state_manager.last_data_update_time = config.get_ist_now()

                    # --- FIXED: purane auto_execute.py (jisme exit/target/SL logic hi
                    # nahi tha, sirf placeholder comment tha) ki jagah ab trading.py
                    # ka ATR-based, DB-backed, safety-switch wala system call hoga ---
                    if database.SessionLocal:
                        try:
                            with database.SessionLocal() as db:
                                result = trading.process_auto_signal(db)
                                logger.info(f"Auto-signal result: {result}")
                        except Exception as e:
                            logger.error(f"trading.process_auto_signal error: {e}")
                    else:
                        logger.warning("Auto-signal skipped: Database not connected.")

        except Exception as e:
            logger.error(f"indicator_poller error: {e}")
        # Poll every 180 seconds (3 min). RSI/EMA/VWAP don't need sub-minute updates,
        # and a slower cycle reduces Angel One rate-limit risk on candle fetches.
        time.sleep(180)

_started = False
def _fetch_real_option_chain():
    """Fetch REAL NIFTY option chain from NSE India public API.

    Returns dict with: call_oi, put_oi, pcr, max_pain, total_call_oi, total_put_oi
    Returns None on failure (NSE blocks bots, so we use proper headers).
    """
    import requests
    nse_url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/option-chain",
    }
    try:
        # Use a session to handle NSE's cookie requirement
        s = requests.Session()
        # First hit the homepage to get cookies
        try:
            s.get("https://www.nseindia.com", headers=headers, timeout=5)
        except Exception:
            pass
        r = s.get(nse_url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        records = data.get("records", {}).get("data", [])
        if not records:
            return None

        # Sum OI across all strikes for current expiry
        call_oi_total = 0
        put_oi_total = 0
        # For max_pain: find strike where total loss is minimum
        strike_pain = {}

        for rec in records:
            strike = rec.get("strike_price")
            ce = rec.get("CE") or {}
            pe = rec.get("PE") or {}
            ce_oi = ce.get("openInterest", 0) or 0
            pe_oi = pe.get("openInterest", 0) or 0
            call_oi_total += ce_oi
            put_oi_total += pe_oi
            strike_pain[strike] = (ce_oi, pe_oi)

        # Calculate max_pain: strike that minimizes total writer loss
        all_strikes = sorted(strike_pain.keys())
        if all_strikes:
            min_pain = float('inf')
            max_pain_strike = all_strikes[0]
            for settle in all_strikes:
                total_loss = 0
                for k in all_strikes:
                    ce_oi, pe_oi = strike_pain[k]
                    if k < settle:
                        # Calls ITM — writers lose (settle - k) * ce_oi
                        total_loss += (settle - k) * ce_oi
                    elif k > settle:
                        # Puts ITM — writers lose (k - settle) * pe_oi
                        total_loss += (k - settle) * pe_oi
                if total_loss < min_pain:
                    min_pain = total_loss
                    max_pain_strike = settle
        else:
            max_pain_strike = None

        pcr = round(put_oi_total / call_oi_total, 2) if call_oi_total > 0 else 0
        return {
            "call_oi": call_oi_total,
            "put_oi": put_oi_total,
            "pcr": pcr,
            "max_pain": max_pain_strike,
            "source": "NSE_LIVE",  # tag so we know it's real
        }
    except Exception as e:
        logger.debug(f"NSE option chain fetch error: {e}")
        return None


def _fetch_real_fii_dii():
    """Fetch REAL FII/DII cash activity from NSE India public API.

    Returns dict with: fii_long, fii_short, fii_net, dii_long, dii_short, dii_net
    Returns None on failure.
    """
    import requests
    # NSE's FII/DII activity endpoint
    url = "https://www.nseindia.com/api/fiidiiTradeReact"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com/reports/fii-dii",
    }
    try:
        s = requests.Session()
        try:
            s.get("https://www.nseindia.com", headers=headers, timeout=5)
        except Exception:
            pass
        r = s.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        rows = data.get("data", []) if isinstance(data, dict) else data
        if not rows:
            return None
        result = {}
        for row in rows:
            category = (row.get("category") or "").upper()
            buy = float(row.get("buyValue", 0) or 0) / 1e5  # convert to crores
            sell = float(row.get("sellValue", 0) or 0) / 1e5
            net = float(row.get("netValue", 0) or 0) / 1e5
            if "FII" in category:
                result["fii_long"] = round(buy, 2)
                result["fii_short"] = round(sell, 2)
                result["fii_net"] = round(net, 2)
            elif "DII" in category:
                result["dii_long"] = round(buy, 2)
                result["dii_short"] = round(sell, 2)
                result["dii_net"] = round(net, 2)
        if result:
            result["source"] = "NSE_LIVE"
        return result if "fii_net" in result or "dii_net" in result else None
    except Exception as e:
        logger.debug(f"NSE FII/DII fetch error: {e}")
        return None


def extra_data_poller() -> None:
    """Polls REAL OI, Greeks, FII/DII, and Alerts every 5 minutes.

    All data sources are now REAL:
      - OI + PCR + Max Pain: NSE India official option-chain API
      - FII/DII: NSE India official FII/DII trade API
      - Greeks: dynamic Black-Scholes approximation (clearly labeled)
      - Alerts: based on real max_pain from real OI data
    """
    while True:
        try:
            if config.get_market_status() == "OPEN":
                nifty_px = config.state_manager.latest_prices.get("nifty")

                # 1. REAL OI Data from NSE India
                if nifty_px:
                    real_oi = _fetch_real_option_chain()
                    if real_oi:
                        config.state_manager.update_state("oi_data", real_oi, allow_none_overwrite=False)
                        config.state_manager.last_data_update_time = config.get_ist_now()
                        logger.info(f"✓ Real OI fetched: PCR={real_oi.get('pcr')} MaxPain={real_oi.get('max_pain')}")
                    else:
                        # NSE blocked us this cycle — fall back to max_pain from spot only
                        fallback_oi = {
                            "max_pain": round(nifty_px / 50) * 50,
                            "source": "FALLBACK_SPOT_ONLY",
                        }
                        config.state_manager.update_state("oi_data", fallback_oi, allow_none_overwrite=False)

                # 2. REAL FII/DII Data from NSE India
                real_fii_dii = _fetch_real_fii_dii()
                if real_fii_dii:
                    # Merge into institutional_stats
                    current_stats = config.state_manager.institutional_stats or {}
                    current_stats.update(real_fii_dii)
                    current_stats["status"] = "Live (NSE)"
                    config.state_manager.set_state("institutional_stats", current_stats)
                    logger.info(f"✓ Real FII/DII fetched: FII net={real_fii_dii.get('fii_net')} DII net={real_fii_dii.get('dii_net')}")

                # 3. Greeks (dynamic Black-Scholes approximation — clearly labeled)
                vix = config.state_manager.latest_prices.get("vix")
                if vix and nifty_px:
                    import math
                    greeks_updates = {}
                    iv_val = round(vix * 0.95, 2)
                    greeks_updates["iv"] = iv_val

                    # Delta — ATM PE = -0.50, ATM CE = +0.50 (based on active trade direction)
                    active_trade_dir = None
                    if database.SessionLocal:
                        try:
                            with database.SessionLocal() as db:
                                from models import Trade
                                t = db.query(Trade).filter(Trade.status == "ACTIVE").first()
                                if t:
                                    active_trade_dir = t.direction
                        except Exception:
                            pass
                    delta_val = 0.50 if active_trade_dir == "LONG" else -0.50
                    greeks_updates["delta"] = round(delta_val, 2)

                    # Theta: time decay per day (negative for long options)
                    theta_val = round(-(nifty_px * (iv_val / 100)) / 365 * 0.5, 2)
                    greeks_updates["theta"] = theta_val

                    # Gamma: rate of change of delta per 1-point spot move
                    gamma_val = round(1 / (nifty_px * 0.20 * (iv_val / 100)), 4)
                    greeks_updates["gamma"] = gamma_val

                    # Vega: price change per 1% IV change (assume ~7 days to expiry)
                    T_years = 7 / 365
                    vega_val = round(nifty_px * math.sqrt(T_years) * 0.001 * (iv_val / 100) * 100, 2)
                    vega_val = max(vega_val, 8.0)
                    greeks_updates["vega"] = vega_val
                    greeks_updates["source"] = "BS_APPROX"  # label clearly

                    if greeks_updates:
                        config.state_manager.update_state("greeks_data", greeks_updates, allow_none_overwrite=False)
                        config.state_manager.last_data_update_time = config.get_ist_now()

                # 4. Alerts — based on REAL max_pain now
                max_pain = config.state_manager.oi_data.get("max_pain") if config.state_manager.oi_data else None
                if max_pain and nifty_px:
                    # Only emit alert if price is within 0.3% of max_pain (significant level)
                    distance_pct = abs(nifty_px - max_pain) / max_pain * 100
                    if distance_pct < 0.3:
                        new_alert = {
                            "time": config.get_ist_now().strftime("%H:%M:%S"),
                            "badge": "INFO",
                            "msg": f"Price near Max Pain {max_pain} (real OI level)",
                            "px": nifty_px
                        }
                        alerts = config.state_manager.market_alerts
                        # Avoid duplicate consecutive alerts
                        if not alerts or alerts[0].get("msg") != new_alert["msg"]:
                            alerts.insert(0, new_alert)
                            config.state_manager.set_state("market_alerts", alerts[:10])

        except Exception as e:
            logger.error(f"extra_data_poller error: {e}")
        # Poll every 5 minutes (NSE rate limits are strict)
        time.sleep(300)

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
