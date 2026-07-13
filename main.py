import logging
from fastapi import FastAPI
from routes import router
from strategy import start_background_threads, stop_background_threads

logging.basicConfig(level=logging.INFO)
app = FastAPI()

app.include_router(router)

@app.on_event("startup")
def startup_event():
    logging.info("GOAT PRO starting up...")
    start_background_threads()
    logging.info("All background threads started")

@app.on_event("shutdown")
def shutdown_event():
    stop_background_threads()
    logging.info("GOAT PRO shutting down...")

@app.get("/")
def read_root():
    return {"status": "GOAT PRO Live"}
