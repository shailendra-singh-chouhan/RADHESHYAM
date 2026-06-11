import os
from supabase import create_client
from logzero import logger

class DatabaseManager:
    def __init__(self):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        if not url or not key:
            raise ValueError("SUPABASE_URL or SUPABASE_KEY missing")
        self.supabase = create_client(url, key)

    def get_stats(self):
        try:
            # .single() की जगह .maybe_single() होना अनिवार्य है
            res = self.supabase.table("trade_history").select("wins,losses").eq("id", 1).maybe_single().execute()
            return res.data if res.data else {"wins": 0, "losses": 0}
        except Exception as e:
            logger.error(f"Supabase get_stats error: {e}")
            return {"wins": 0, "losses": 0}

    def get_alpha_picks(self):
        try:
            res = self.supabase.table("alpha_picks").select("*").eq("status", "ACTIVE").order("confidence", desc=True).limit(3).execute()
            return res.data if res.data else []
        except Exception as e:
            logger.error(f"Supabase Alpha Picks error: {e}")
            return []
