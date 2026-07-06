import os
from fastapi import FastAPI
from logzero import logger

import angel_client
import strategy
from routes import router
from database import init_db, db_engine

app = FastAPI(title="GOAT PRO Institutional", version="3.0")

# Mounting our modular routes
app.include_router(router)

@app.on_event("startup")
async def startup_event() -> None:
    """Initialize DB tables, run Angel One Login, and start background execution tasks."""
    logger.info("Application startup event triggered.")
    
    if db_engine:
        init_db(db_engine)
    else:
        logger.warning("Database engine is not available. Trades will not be persisted.")
        
    # Fire up Angel One authorization
    angel_client.angel_login()
    
    # Launch asynchronous pricing and indicator engine loops
    strategy.start_background_threads()

# Render command compatibility or local deployment execution
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 5000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
