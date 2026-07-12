"""
GOAT PRO — FastAPI Entry Point
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import engine, SessionLocal
from models import Base
import routes
import strategy
import stocks

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables, start background threads. Shutdown: stop threads."""
    logger.info("GOAT PRO starting up...")

    # Create DB tables
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready")

    # Start background pollers
    strategy.start_background_threads(db_session_factory=SessionLocal)

    # Start stock poller
    stocks.start_stock_poller()

    logger.info("All background threads started")

    yield

    # Shutdown
    strategy.stop_background_threads()
    stocks.stop_stock_poller()
    logger.info("GOAT PRO shut down")


app = FastAPI(title="GOAT PRO", version="3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes.router)
