@app.get("/api/data")
async def api_data(db: Session = Depends(get_db)) -> JSONResponse:
    """Main data endpoint that the dashboard polls every few seconds."""
    market_status = config.get_market_status()
    risk_ok, risk_message = trading.check_risk_limits(db) if db else (True, "Risk OK (no DB)")
    stats = trading.get_institutional_stats(db)

    # Active trade + today's session PnL
    current_active = None
    session_pnl = 0.0
    live_pnl = None
    today = config.get_ist_now().date().isoformat()
    if db:
        try:
            active_row = db.query(Trade).filter(Trade.status == "ACTIVE").first()
            if active_row:
                current_active = {
                    "direction": active_row.direction,
                    "entry": active_row.entry,
                    "target": active_row.target,
                    "sl": active_row.sl,
                }
                if config.latest_prices["nifty"] is not None:
                    live_pnl = round(config.latest_prices["nifty"] - active_row.entry, 2)
            # Today's closed trades PnL
            today_closed = db.query(Trade).filter(
                Trade.status == "CLOSED", Trade.trade_date == today,
            ).all()
            session_pnl = sum(t.pnl or 0 for t in today_closed)
        except Exception as e:
            logger.error(f"api_data query error: {e}")

    return JSONResponse({
        "spot": config.latest_prices["nifty"],
        "banknifty": config.latest_prices["banknifty"],
        "vix": config.latest_prices["vix"],
        "day_open": config.latest_prices["day_open"],
        "market_status": market_status,
        "risk_ok": risk_ok,
        "risk_message": risk_message,
        "session_pnl_rs": session_pnl,
        "win_rate": stats["win_rate"],
        "total_trades": stats["total_trades"],
        "institutional_stats": stats,
        "active_trade": {
            "direction": current_active["direction"],
            "entry": current_active["entry"],
            "target": current_active["target"],
            "sl": current_active["sl"],
            "live_pnl": live_pnl,
        } if current_active else None,
        "indicators": config.indicator_data,
        "real_signal": config.signal_data,
        "last_update": config.latest_prices["last_update"],
    })
