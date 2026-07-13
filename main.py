import logging
from fastapi import FastAPI
from contextlib import asynccontextmanager
from routes import router
import strategy

# Setup logging
logging.basicConfig(level=logging.INFO)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logging.info("GOAT PRO starting up...")
    strategy.start_background_threads()
    logging.info("All background threads started")
    yield
    # Shutdown
    logging.info("GOAT PRO shutting down...")
    strategy.stop_background_threads()

app = FastAPI(lifespan=lifespan)
app.include_router(router)

@app.get("/")
def read_root():
    return {"status": "GOAT PRO Live"}
