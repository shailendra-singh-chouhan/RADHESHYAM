import os
import yfinance as yf
from flask import Flask, render_template_string, request
from logzero import logger
from db_manager import DatabaseManager

app = Flask(__name__)

# Initialize DB globally but safely
try:
    db = DatabaseManager()
    logger.info("Supabase DB Initialized")
except Exception as e:
    logger.error(f"DB Init Failed: {e}")
    db = None

def get_ticker_data(symbol="^NSEI"):
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1d", interval="1m")
        if df.empty: return None
        price = float(df['Close'].iloc[-1])
        return {"price": round(price, 2)}
    except Exception as e:
        logger.error(f"Ticker Fetch Error: {e}")
        return None

@app.route('/', methods=['GET', 'POST'])
def index():
    user_asset = request.form.get('asset', '^NSEI').strip().upper()
    data = get_ticker_data(user_asset)
    
    # Fetch stats from Supabase
    stats = db.get_stats() if db else {"wins": 0, "losses": 0}
    
    html = f"""
    <html>
    <head><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-slate-900 text-white p-8">
        <h1 class="text-blue-500 font-bold text-2xl">GOAT PRO V17 (PRODUCTION)</h1>
        <div class="mt-4 p-4 bg-slate-800 rounded">
            <p>Asset: {user_asset}</p>
            <p>Price: {data['price'] if data else 'N/A'}</p>
            <hr class="my-4 border-slate-700">
            <p class="text-green-400 font-bold">Total Wins: {stats['wins']}</p>
            <p class="text-red-400 font-bold">Total Losses: {stats['losses']}</p>
        </div>
    </body>
    </html>
    """
    return render_template_string(html)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
