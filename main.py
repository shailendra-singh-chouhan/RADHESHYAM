# main.py

import os
import logging
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException # Using FastAPI for this example
from sqlalchemy.orm import Session
from typing import List, Dict, Any

# Import models and database utilities
from models import Trade # Import our Trade model
from database import get_db, init_db, db_engine # Import connection logic

# --- Basic Logger Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- FastAPI App Instance ---
app = FastAPI()

# --- Database Initialization ---
# Function to initialize the database tables when the app starts.
# This is a common way to do it with FastAPI.
# For other frameworks like Flask, you'd use @app.before_first_request or similar.
@app.on_event("startup")
async def startup_event():
    logger.info("Application startup event triggered.")
    if db_engine:
        init_db(db_engine)
    else:
        logger.warning("Database engine is not available. Startup initialization skipped.")

# --- Utility function to get a DB session ---
# This function will be used in our API endpoints.
async def get_db_session():
    """Provides a database session using the get_db dependency."""
    if db_engine is None:
        raise HTTPException(status_code=500, detail="Database is not connected.")
    # get_db() is a generator, so we need to iterate over it
    # In FastAPI, Depends handles this automatically if it's a generator
    # However, for clarity or if you get issues, you can manually manage:
    db_gen = get_db()
    try:
        db = next(db_gen) # Get the session from the generator
        if db is None:
            raise HTTPException(status_code=500, detail="Failed to get database session.")
        yield db # Yield the session for use in the endpoint
    finally:
        # The 'yield' part is managed by FastAPI's dependency injection system.
        # The 'finally' block ensures db.close() is called.
        logger.debug("Ensuring DB session is closed after request.")
        if db:
            db.close()


# --- Placeholder for other functionalities (e.g., fetching LTP, candles) ---
# These functions are assumed to exist and would fetch data from APIs, not the DB.
# You need to implement the actual API calls for these.

async def get_ltp(symbol: str) -> float:
    """
    Placeholder function to fetch Latest Traded Price (LTP) for a symbol.
    Replace with actual API call or data source.
    """
    logger.info(f"Fetching LTP for {symbol} (placeholder)")
    # In a real scenario, you'd call an external API here.
    # For now, returning a dummy value.
    if symbol == "NIFTY": return 10000.50
    if symbol == "VIX": return 20.75
    return 0.0 # Default if symbol not found

async def fetch_todays_candles(symbol: str) -> List[Dict[str, Any]]:
    """
    Placeholder function to fetch today's candle data for a symbol.
    Replace with actual API call or data source.
    """
    logger.info(f"Fetching today's candles for {symbol} (placeholder)")
    # In a real scenario, you'd call an external API.
    # Returning dummy data.
    return [
        {"timestamp": datetime.utcnow(), "open": 10000, "high": 10010, "low": 9990, "close": 10005},
        {"timestamp": datetime.utcnow(), "open": 10005, "high": 10015, "low": 10000, "close": 10012}
    ]

# --- Database Interaction Functions (Refactored) ---

async def open_paper_trade_db(
    trade_data: Dict[str, Any], # Expects {'direction', 'entry', 'target', 'sl'}
    db: Session = Depends(get_db_session) # Inject DB session
) -> Dict[str, Any]:
    """
    Opens a new paper trade and saves it to the database.
    """
    if db is None:
        raise HTTPException(status_code=500, detail="Database session is not available.")
    try:
        logger.info(f"Attempting to open trade with data: {trade_data}")
        new_trade = Trade(
            direction=trade_data.get("direction"),
            entry=trade_data.get("entry"),
            target=trade_data.get("target"),
            sl=trade_data.get("sl"),
            opened_at=datetime.utcnow(),
            status='ACTIVE' # New trades are always active
        )
        db.add(new_trade)
        db.commit()
        db.refresh(new_trade) # Refresh to get the auto-generated ID and defaults
        logger.info(f"Successfully opened trade: {new_trade}")
        return {"message": "Paper trade opened successfully", "trade_id": new_trade.id}
    except Exception as e:
        db.rollback() # Important: rollback changes if an error occurs
        logger.error(f"Error opening paper trade: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to open trade: {e}")

async def close_paper_trade_db(
    trade_id: int,
    exit_price: float,
    db: Session = Depends(get_db_session) # Inject DB session
) -> Dict[str, Any]:
    """
    Closes an existing open trade by updating its details and status.
    """
    if db is None:
        raise HTTPException(status_code=500, detail="Database session is not available.")
    try:
        logger.info(f"Attempting to close trade ID: {trade_id} with exit price: {exit_price}")

        # Find the trade by its ID
        trade = db.query(Trade).filter(Trade.id == trade_id).first()

        if not trade:
            logger.warning(f"Trade ID {trade_id} not found.")
            raise HTTPException(status_code=404, detail=f"Trade with ID {trade_id} not found.")
        if trade.status == 'CLOSED':
            logger.warning(f"Trade ID {trade_id} is already closed.")
            raise HTTPException(status_code=400, detail=f"Trade with ID {trade_id} is already closed.")

        # Update trade details
        trade.exit_price = exit_price
        trade.closed_at = datetime.utcnow()
        trade.status = 'CLOSED'

        # Calculate PNL based on direction
        pnl = None
        if trade.direction == 'BUY':
            pnl = exit_price - trade.entry
        elif trade.direction == 'SELL':
            pnl = trade.entry - exit_price
        trade.pnl = pnl

        db.commit()
        db.refresh(trade) # Refresh to get updated values like PNL

        logger.info(f"Successfully closed trade {trade_id}. PNL: {pnl}")
        return {"message": f"Trade {trade_id} closed successfully", "pnl": pnl}

    except HTTPException as http_exc:
        db.rollback() # Rollback if a specific HTTP error was raised
        logger.error(f"HTTP Exception during trade closing {trade_id}: {http_exc.detail}")
        raise http_exc # Re-raise the HTTPException
    except Exception as e:
        db.rollback() # Rollback on any other error
        logger.error(f"Unexpected error closing trade {trade_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to close trade {trade_id}: {e}")

async def get_institutional_stats_db(
    db: Session = Depends(get_db_session) # Inject DB session
) -> Dict[str, Any]:
    """
    Fetches aggregated trading statistics from the database.
    """
    if db is None:
        raise HTTPException(status_code=500, detail="Database session is not available.")
    try:
        logger.info("Fetching institutional statistics from database.")

        # Example: Count of active trades
        active_trades_count = db.query(Trade).filter(Trade.status == 'ACTIVE').count()

        # Example: Count of closed trades
        closed_trades_count = db.query(Trade).filter(Trade.status == 'CLOSED').count()

        # Example: Total PNL for closed trades (handle NULL PNL values)
        total_pnl = db.query(db.func.coalesce(db.func.sum(Trade.pnl), 0.0)).filter(Trade.status == 'CLOSED').scalar()

        stats = {
            "total_active_trades": active_trades_count,
            "total_closed_trades": closed_trades_count,
            "total_pnl_realized": round(total_pnl, 2) if total_pnl is not None else 0.0,
            # Add more stats as needed
        }
        logger.info(f"Institutional Stats: {stats}")
        return stats

    except Exception as e:
        logger.error(f"Error fetching institutional stats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch stats: {e}")

# --- Example API Endpoints (using the database interaction functions) ---

@app.post("/open_trade")
async def open_trade_endpoint(
    trade_info: Dict[str, Any], # Expect POST body like: {"direction": "BUY", "entry": 100.5, "target": 102.0, "sl": 99.0}
    db: Session = Depends(get_db_session)
):
    """
    API endpoint to open a new paper trade.
    """
    return await open_paper_trade_db(trade_data=trade_info, db=db)

@app.post("/close_trade/{trade_id}")
async def close_trade_endpoint(
    trade_id: int,
    exit_price_info: Dict[str, float], # Expect POST body like: {"exit_price": 101.5}
    db: Session = Depends(get_db_session)
):
    """
    API endpoint to close a paper trade.
    """
    exit_price = exit_price_info.get("exit_price")
    if exit_price is None:
        raise HTTPException(status_code=400, detail="Missing 'exit_price' in request body.")
    return await close_paper_trade_db(trade_id=trade_id, exit_price=exit_price, db=db)

@app.get("/institutional_stats")
async def get_stats_endpoint(
    db: Session = Depends(get_db_session)
):
    """
    API endpoint to get overall trading statistics.
    """
    return await get_institutional_stats_db(db=db)

# --- Example of how to use helper functions like get_ltp ---
# This would typically be called from within an API endpoint or a background task.
# For demonstration, let's add a simple endpoint.
@app.get("/get_market_data")
async def get_market_data_endpoint(symbol: str):
    """
    Endpoint to fetch market data (LTP and candles) for a given symbol.
    """
    ltp = await get_ltp(symbol)
    candles = await fetch_todays_candles(symbol)
    return {"symbol": symbol, "ltp": ltp, "todays_candles": candles}

# --- Root endpoint for health check or basic info ---
@app.get("/")
async def read_root():
    return {"message": "Trading Bot API is running!"}
