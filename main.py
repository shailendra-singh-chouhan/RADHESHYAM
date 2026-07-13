import logging
from fastapi import FastAPI
from fastapi.responses import FileResponse
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

# यहाँ रूट अपडेट किया गया है
@app.get("/")
def read_root():
    return FileResponse("index.html")

# अगर आपके पास index.html नहीं है, तो नीचे वाला कोड यूज़ करें (सिर्फ टेस्टिंग के लिए)
# @app.get("/")
# def read_root():
#     return {"message": "Dashboard UI file missing. Add index.html to your root folder."}
