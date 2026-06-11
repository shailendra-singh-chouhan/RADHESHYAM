import os
from supabase import create_client
import logzero
from logzero import logger

class DatabaseManager:
    def __init__(self):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        self.supabase = create_client(url, key)

    def get_stats(self):
        try:
            # maybe_single() का इस्तेमाल करें, यह एरर नहीं देगा
            res = self.supabase.table("trade_history").select("wins,losses").eq("id", 1).maybe_single().execute()
            return res.data if res.data else {"wins": 0, "losses": 0}
        except Exception as e:
            logger.error(f"Supabase DB error: {e}")
            return {"wins": 0, "losses": 0}
