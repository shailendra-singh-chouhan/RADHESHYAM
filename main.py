import os
import time
import sqlite3
import pyotp
from flask import Flask, render_template_string
from SmartApi import SmartConnect

app = Flask(__name__)

# --- DATABASE LAYER ---
class Database:
    def __init__(self, db_path="quant_vault.db"):
        self.db = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS session (key TEXT PRIMARY KEY, token TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS stats (id INTEGER PRIMARY KEY, wins INTEGER, losses INTEGER)")
            # Initialize stats if empty
            conn.execute("INSERT OR IGNORE INTO stats (id, wins, losses) VALUES (1, 0, 0)")
            conn.commit()

    def save_token(self, token):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT OR REPLACE INTO session (key, token) VALUES ('angel_token', ?)", (token,))

    def get_token(self):
        with sqlite3.connect(self.db) as conn:
            res = conn.execute("SELECT token FROM session WHERE key='angel_token'").fetchone()
            return res[0] if res else None

# --- SESSION MANAGER LAYER ---
class SessionManager:
    def __init__(self, db):
        self.db = db

    def get_client(self):
        api_key = os.environ.get("ANGEL_API_KEY")
        client_id = os.environ.get("ANGEL_CLIENT_ID")
        mpin = os.environ.get("ANGEL_MPIN")
        totp = pyotp.TOTP(os.environ.get("ANGEL_TOTP_SECRET")).now()
        
        obj = SmartConnect(api_key=api_key)
        # In real production, check token validity first
        session = obj.generateSession(client_id, mpin, totp)
        return obj if session.get('status') else None

# --- QUANT DECISION ENGINE ---
class QuantEngine:
    @staticmethod
    def get_metrics(spot, vix):
        # Institutional Logic separated from UI
        velocity = 0.85 # Mock velocity logic
        status = "SETUP_READY" if vix < 18 else "SYSTEM_BLOCKED"
        return {"spot": spot, "vix": vix, "vel": velocity, "status": status}

# --- FLASK APP ---
db = Database()
session_mgr = SessionManager(db)

@app.route('/')
def index():
    # Fetch Data Institutional way
    client = session_mgr.get_client()
    if not client:
        return "<h1>AUTH ERROR</h1>"
    
    # Logic Processing
    stats = {"wins": 12, "losses": 3} # Simplified for example
    engine_data = QuantEngine.get_metrics(23165.40, 15.5)
    
    return render_template_string(HTML_TEMPLATE, m={**engine_data, **stats})

# --- UI (TAILWIND PRESERVED) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="hi">
<head><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-[#050914] text-slate-200 p-4">
    <div class="max-w-md mx-auto p-4 border border-blue-800 rounded bg-slate-900">
        <h1 class="text-blue-500 font-black">GOAT PRO V17 (PRODUCTION)</h1>
        <div class="text-sm">STATUS: {{ m.status }}</div>
        <div class="text-2xl font-bold">₹{{ m.spot }}</div>
    </div>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
@app.route('/health')
def health_check():
    return {"status": "ok"}, 200
# main.py में यह बदलाव करें
from db_manager import DatabaseManager # SQLite imports हटा दें
db = DatabaseManager() 

@app.route('/')
def dashboard():
    # अब डेटा हमेशा Supabase से आएगा
    stats = db.get_stats()
    # इंजन से डेटा लें
    data = {"spot": 23165.4, "stats": stats}
    return render_template_string(HTML_TEMPLATE, m=data)
from db_manager import DatabaseManager
db = DatabaseManager()

# अब stats ऐसे लें:
stats = db.get_stats()
