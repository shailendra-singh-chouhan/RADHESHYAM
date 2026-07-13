import logging
import time
import threading
from angel_client import angel_client
from database import save_app_state

logger = logging.getLogger(__name__)
_threads_running = True

def stop_background_threads():
    global _threads_running
    _threads_running = False
    logger.info("Background threads stopped.")

def state_saver_poller():
    global _threads_running
    while _threads_running:
        try:
            state = {"status": "active", "timestamp": time.time()}
            save_app_state(state) 
        except Exception as e:
            logger.error(f"state_saver_poller error: {e}")
        time.sleep(60)

def indicator_poller():
    global _threads_running
    while _threads_running:
        try:
            angel_client.fetch_todays_candles("NIFTY")
        except Exception as e:
            logger.error(f"indicator_poller error: {e}")
        time.sleep(60)

def start_background_threads():
    global _threads_running
    _threads_running = True
    threading.Thread(target=state_saver_poller, daemon=True).start()
    threading.Thread(target=indicator_poller, daemon=True).start()
