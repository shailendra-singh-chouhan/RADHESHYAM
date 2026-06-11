import os
from supabase import create_client
import logzero
from logzero import logger

class DatabaseManager:
    def __init__(self):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise ValueError("SUPABASE_URL or SUPABASE_KEY missing in environment variables")
        self.supabase = create_client(url, key)

    def get_stats(self):
        try:
            # Supabase से 'wins' और 'losses' निकालें
            res = self.supabase.table("trade_history").select("wins,losses").eq("id", 1).single().execute()
            return res.data if res.data else {"wins": 0, "losses": 0}
        except Exception as e:
            logger.error(f"Supabase get_stats error: {e}")
            return {"wins": 0, "losses": 0}
def get_stats(self):
        try:
            # .maybe_single() इस्तेमाल करें, यह एरर नहीं देगा अगर डेटा नहीं होगा
            res = self.supabase.table("trade_history").select("wins,losses").eq("id", 1).maybe_single().execute()
            return res.data if res.data else {"wins": 0, "losses": 0}
        except Exception as e:
            logger.error(f"Supabase error: {e}")
            return {"wins": 0, "losses": 0}
