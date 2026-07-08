import os
import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from logzero import logger

import config
import angel_client
import strategy
import routes

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("--- Starting GOAT PRO Institutional Services ---")
    
    # Initialize database tables and migrations
    from database import init_db
    init_db()
    logger.info("Database initialized successfully.")

    if angel_client.angel_login():
        angel_client.refresh_scrip_master()  # Load tokens on startup
    else:
        logger.warning("Initial Angel One login failed. Will retry in pollers.")

    # Start all background pollers
    import stocks
    stocks.start_stock_price_poller()
    strategy.start_background_threads()
    logger.info("All background services started successfully.")

    yield

    logger.info("--- Shutting down GOAT PRO ---")

app = FastAPI(title="GOAT PRO", lifespan=lifespan)

# Mount the main dashboard router
app.include_router(routes.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


