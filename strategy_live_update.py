

# ─────────────────────────────────────────────────────────────────
# LIVE DATA INTEGRATION — Replace fallback with real data
# ─────────────────────────────────────────────────────────────────

from live_data_fetcher import (
    fetch_nifty_spot,
    fetch_banknifty_spot,
    fetch_nse_option_chain,
    fetch_india_vix,
    fetch_global_markets,
    fetch_fii_dii,
    fetch_stock_price,
    fetch_candles,
    fetch_all_live_data,
)


def _update_live_data() -> None:
    """
    Fetch ALL real data and update shared_state + state_manager.
    Called by background poller every 15 seconds.
    """
    try:
        # 1. Spot prices
        nifty = fetch_nifty_spot()
        banknifty = fetch_banknifty_spot()

        spot = nifty.get("value", 0)
        if not spot:
            logger.warning("No NIFTY spot available")
            return

        # 2. Option Chain / OI
        oi_data = fetch_nse_option_chain("NIFTY")

        # 3. VIX
        vix_data = fetch_india_vix()

        # 4. Global markets
        global_data = fetch_global_markets()

        # 5. FII/DII
        fii_dii = fetch_fii_dii()

        # 6. Candles for signal generation
        candles = fetch_candles("NIFTY 50", "NSE", "FIFTEEN_MINUTE", 5)

        # 7. Generate signal
        signal_data = generate_signal(candles, spot) if candles and len(candles) >= 21 else {
            "signal": "WAIT",
            "confidence": 0,
            "checklist": {},
            "note": "Not enough candles" if not candles else "Insufficient data",
        }

        # 8. Greeks for ATM
        greeks_data = get_atm_greeks(spot, "NIFTY")

        # 9. Update shared_state
        shared_state.update({
            "spot": spot,
            "banknifty_spot": banknifty.get("value", 0),
            "signal": signal_data.get("signal", "WAIT"),
            "confidence": signal_data.get("confidence", 0),
            "checklist": signal_data.get("checklist", {}),
            "orb_high": signal_data.get("orb_high", 0),
            "orb_low": signal_data.get("orb_low", 0),
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
            },
            "last_updated": datetime.now().isoformat(),
        })

        # 10. Update state_manager
        config.state_manager.set_state("latest_prices", {
            "nifty": spot,
            "banknifty": banknifty.get("value", 0),
        })
        config.state_manager.set_state("signal_data", signal_data)
        config.state_manager.set_state("oi_data", oi_data)
        config.state_manager.set_state("greeks_data", greeks_data)
        config.state_manager.set_state("vix_data", vix_data)
        config.state_manager.set_state("global_data", global_data)
        config.state_manager.set_state("fii_dii", fii_dii)
        config.state_manager.set_state("candle_store", candles)
        config.state_manager.last_data_update_time = datetime.now()

        logger.info(
            f"LIVE DATA | Spot: {spot} | Signal: {signal_data['signal']} | "
            f"OI Source: {oi_data.get('source')} | VIX: {vix_data.get('value')} | "
            f"Global: {global_data.get('source')} | FII/DII: {fii_dii.get('source')}"
        )

    except Exception as e:
        logger.error(f"Live data update error: {e}")


# Replace the old _live_data_poller with new one
def _live_data_poller() -> None:
    """Background thread: fetch all real data every 15 seconds."""
    global _poller_running
    logger.info("Live data poller started — ALL REAL DATA")

    while _poller_running:
        _update_live_data()
        time.sleep(15)

    logger.info("Live data poller stopped")
