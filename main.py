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

    # Initialize Angel One client — login + scrip master happen inside __init__
    try:
        client = angel_client.get_angel_client()
        if client and client.jwt_token:
            logger.info("Angel One session ready + scrip master loaded.")
        else:
            logger.warning("Angel One session not ready. Pollers will retry.")
    except Exception as e:
        logger.warning(f"Angel One init failed: {e}. Pollers will retry.")

    # Start all background pollers
    try:
        import stocks
        stocks.start_stock_price_poller()
    except Exception as e:
        logger.warning(f"Stock poller not started: {e}")

    try:
        strategy.start_background_threads()
    except Exception as e:
        logger.warning(f"Strategy threads not started: {e}")

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
