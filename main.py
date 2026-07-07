import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from logzero import logger

# Project modules
import database
import routes
import strategy
import stocks
import auto_execute

# Initialize FastAPI App
app = FastAPI(title="GOAT PRO Institutional")

# CORS Middleware (To allow dashboard communication)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Startup Event
@app.on_event("startup")
async def startup_event():
    logger.info("--- Starting GOAT PRO Institutional Services ---")
    
    # 1. Initialize Database
    try:
        database.Base.metadata.create_all(bind=database.db_engine)
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")

    # 2. Start Strategy Background Threads (Price + Indicators)
    strategy.start_background_threads()
    
    # 3. Start Stock Price Poller
    stocks.start_stock_price_poller()
    
    logger.info("All background services (Strategy & Stocks) started successfully.")

# Include Routes
app.include_router(routes.router)

if __name__ == "__main__":
    # Run the application
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
