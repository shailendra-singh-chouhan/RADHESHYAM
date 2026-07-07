import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from logzero import logger

from database import get_db, db_engine
from models import Trade
import config
import trading
import angel_client
import strategy
import stocks

router = APIRouter()

DASHBOARD_FILE = Path(__file__).parent / "dashboard.html"

def load_dashboard_html() -> str:
    """Loads the dashboard HTML from disk."""
    return DASHBOARD_FILE.read_text(encoding="utf-8")

@router.get("/", response_class=HTMLResponse)
async def read_root() -> HTMLResponse:
    """The main dashboard UI."""
    return HTMLResponse(content=load_dashboard_html())

@router.api_route("/health", methods=["GET", "HEAD"])
async def health() -> dict:
    return {
        "status": "healthy",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "service": "GOAT PRO Institutional",
        "version": "3.2",
        "db_connected": db_engine is not None,
    }

@router.get("/ping")
async def ping() -> dict:
    return {"status": "alive"}

# NOTE: The /token login route and all `Depends(get_current_user)` auth checks
# were removed here (per user request) - they made the dashboard's own API
# calls return 401 Unauthorized because dashboard.html never sends a Bearer
# token. auth.py is left in the repo but unused; nothing imports it now.

@router.get("/api/data")
async def api_data(db: Session = Depends(get_db)) -> JSONResponse:
    """Main data endpoint that the dashboard polls every few seconds."""
    market_status = config.get_market_status()
    risk_ok, risk_message = trading.check_risk_limits(db) if db else (True, "Risk OK (no DB)")
    stats = trading.get_institutional_stats(db)
    
    # PRO Auto-Trade Logic
    auto_status = trading.process_auto_signal(db) if db else {"action": "skipped", "reason": "No DB"}
    
    # Access state via state_manager
    state = config.state_manager
    latest_prices = state.latest_prices
    signal_data = state.signal_data
    
    # Phase 4: Calculate Options Contract dynamically
    selected_contract = None
    spot = latest_prices.get("nifty")
    if signal_data and signal_data.get("signal") in ["LONG", "SHORT"] and spot:
        selected_contract = trading.get_options_contract(spot, signal_data["signal"])

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
                if latest_prices.get("nifty") is not None:
                    live_pnl = round(latest_prices["nifty"] - active_row.entry, 2)
            
            # Today's closed trades PnL
            today_closed = db.query(Trade).filter(
                Trade.status == "CLOSED", Trade.trade_date == today,
            ).all()
            session_pnl = sum(t.pnl or 0 for t in today_closed)
        except Exception as e:
            logger.error(f"api_data query error: {e}")

    return JSONResponse({
        "spot": latest_prices.get("nifty"),
        "banknifty": latest_prices.get("banknifty"),
        "finnifty": latest_prices.get("finnifty"),
        "sensex": latest_prices.get("sensex"),
        "crudeoil": latest_prices.get("crudeoil"),
        "gold": latest_prices.get("gold"),
        "silver": latest_prices.get("silver"),
        "usdinr": latest_prices.get("usdinr"),
        "midcap": latest_prices.get("midcap"),
        "vix": latest_prices.get("vix"),
        "day_open": latest_prices.get("day_open"),
        "market_status": market_status,
        "risk_ok": risk_ok,
        "risk_message": risk_message,
        "auto_trade": auto_status,
        "session_pnl_rs": session_pnl,
        "win_rate": stats["win_rate"],
        "total_trades": stats["total_trades"],
        "institutional_stats": stats,
        "options_contract": selected_contract,
        "oi_data": state.oi_data,
        "greeks": state.greeks_data,
        "alerts": list(state.market_alerts)[-10:], # last 10 alerts to save bandwidth
        "stocks": stocks.get_all_stock_data(),
        "indicators": state.indicator_data,
        "global": {
            "kospi": latest_prices.get("kospi"),
            "nasdaq": latest_prices.get("nasdaq"),
            "dji": latest_prices.get("dji"),
        },
        "active_trade": {
            "direction": current_active["direction"],
            "entry": current_active["entry"],
            "target": current_active["target"],
            "sl": current_active["sl"],
            "live_pnl": live_pnl,
        } if current_active else None,
        "real_signal": signal_data,
        "last_update": state.last_data_update_time.isoformat() if state.last_data_update_time else "N/A", # Use last_data_update_time
    })

@router.get("/api/trades")
async def api_trades(db: Session = Depends(get_db)) -> dict:
    if db is None:
        return {"open": None, "closed": [], "stats": trading.get_institutional_stats(None)}
    try:
        open_trade = db.query(Trade).filter(Trade.status == "ACTIVE").first()
        closed_trades = db.query(Trade).filter(Trade.status == "CLOSED")\
                                       .order_by(Trade.closed_at.desc()).limit(100).all()
        return {
            "open": open_trade.to_dict() if open_trade else None,
            "closed": [t.to_dict() for t in closed_trades],
            "stats": trading.get_institutional_stats(db),
        }
    except Exception as e:
        logger.error(f"api_trades error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/execute_trade")
async def api_execute_trade(db: Session = Depends(get_db)) -> dict:
    success, message = trading.open_paper_trade(db)
    return {"success": success, "message": message}

@router.post("/api/close_trade")
async def api_close_trade(db: Session = Depends(get_db)) -> dict:
    success, message = trading.close_paper_trade(db)
    return {"success": success, "message": message}

@router.get("/api/candles")
async def api_candles() -> dict:
    candles = config.state_manager.candle_store
    return {"candles": candles}

@router.get("/get_market_data")
async def get_market_data_endpoint(symbol: str) -> dict:
    symbol_map = {
        "NIFTY": ("NSE", config.NIFTY_SYMBOL),
        "BANKNIFTY": ("NSE", config.BANKNIFTY_SYMBOL),
        "VIX": ("NSE", config.VIX_SYMBOL),
    }
    key = symbol.upper().strip()
    if key not in symbol_map:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")
    
    exch, sym = symbol_map[key]
    ltp = angel_client.get_ltp(exch, sym)
    
    if key == "VIX" and ltp is None:
        ltp = angel_client.get_ltp("NFO", sym)
        
    candles = angel_client.fetch_todays_candles() if key == "NIFTY" else []
    return {"symbol": sym, "ltp": ltp, "todays_candles": candles or []}

@router.get("/institutional_stats")
async def get_stats_endpoint(db: Session = Depends(get_db)) -> dict:
    if db is None:
        raise HTTPException(status_code=500, detail="Database is not connected.")
    try:
        active_count = db.query(Trade).filter(Trade.status == "ACTIVE").count()
        closed_count = db.query(Trade).filter(Trade.status == "CLOSED").count()
        total_pnl = db.query(func.coalesce(func.sum(Trade.pnl), 0.0))\
                       .filter(Trade.status == "CLOSED").scalar()
        stats = {
            "total_active_trades": active_count,
            "total_closed_trades": closed_count,
            "total_pnl_realized": round(total_pnl, 2) if total_pnl is not None else 0.0,
        }
        return stats
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch stats: {e}")
