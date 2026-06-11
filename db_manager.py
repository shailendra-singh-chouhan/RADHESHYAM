import os
from supabase import create_client

class DatabaseManager:
    def __init__(self):
        self.supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

    def get_stats(self):
        try:
            res = self.supabase.table("trade_history").select("wins", "losses").eq("id", 1).single().execute()
            return res.data if res.data else {"wins": 0, "losses": 0}
        except Exception:
            return {"wins": 0, "losses": 0}
