import os
import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from logzero import logger

import config
import angel_client
import strategy

# ============================================
# Lifespan - Startup & Shutdown
# ============================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("--- Starting GOAT PRO Institutional Services ---")
    
    # Initialize database
    # (your existing db init code here)
    logger.info("Database initialized successfully.")
    
    # Angel One Login
    if not angel_client.angel_login():
        logger.warning("Initial Angel One login failed. Will retry in pollers.")
    
    # Start background threads
    strategy.start_background_threads()
    logger.info("All background services started successfully.")
    
    yield
    
    logger.info("--- Shutting down GOAT PRO ---")

# ============================================
# FastAPI App
# ============================================
app = FastAPI(
    title="GOAT PRO",
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# Health Check Endpoints (Render Fix)
# ============================================
@app.head("/")
async def root_head():
    return {}

@app.get("/")
async def root_get():
    return {
        "status": "GOAT PRO Institutional Services",
        "market_status": config.get_market_status(),
        "time": config.get_ist_now().isoformat()
    }

# ============================================
# API Routes
# ============================================
@app.get("/api/data")
async def api_data():
    return {
        "latest_prices": config.latest_prices,
        "indicator_data": config.indicator_data,
        "signal_data": config.signal_data,
        "market_status": config.get_market_status(),
    }

@app.get("/api/candles")
async def api_candles():
    return {"candles": config.candle_store}

# ============================================
# Your other existing routes below...
# ============================================
