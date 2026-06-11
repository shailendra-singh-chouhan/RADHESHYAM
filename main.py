import os
import yfinance as yf
from flask import Flask, render_template_string, request
from logzero import logger
from db_manager import DatabaseManager
import time

app = Flask(__name__)
db = DatabaseManager()

# Simple Cache
cache = {"price": 0, "last_updated": 0}

def get_ticker_data(symbol="^NSEI"):
    if time.time() - cache["last_updated"] < 60: # 60 सेकंड तक पुराना डेटा दिखाएं
        return {"price": cache["price"]}
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1d", interval="1m")
        if not df.empty:
            price = float(df['Close'].iloc[-1])
            cache["price"] = round(price, 2)
            cache["last_updated"] = time.time()
            return {"price": cache["price"]}
    except Exception as e:
        logger.error(f"Ticker Fetch Error: {e}")
        return {"price": cache["price"]}

@app.route('/')
def index():
    data = get_ticker_data()
    stats = db.get_stats()
    
    html = f"""
    <html>
    <head><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-slate-900 text-white p-8">
        <h1 class="text-blue-500 font-bold text-2xl">GOAT PRO V17 (PRODUCTION)</h1>
        <div class="mt-4 p-4 bg-slate-800 rounded">
            <p class="text-xl">NIFTY Price: {data['price']}</p>
            <div class="mt-4 flex gap-4">
                <span class="text-green-400 font-bold">Wins: {stats.get('wins', 0)}</span>
                <span class="text-red-400 font-bold">Losses: {stats.get('losses', 0)}</span>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(html)

if __name__ == '__main__':
    app.run()
