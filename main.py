import os
import time
import yfinance as yf
from flask import Flask, render_template_string
from logzero import logger
from db_manager import DatabaseManager

app = Flask(__name__)
# DatabaseManager को इनिशियलाइज़ करें
db = DatabaseManager()

# Cache logic: YFinance को बार-बार पिंग करने से बचने के लिए
cache = {"price": 0, "last_updated": 0}

def get_ticker_data(symbol="^NSEI"):
    global cache
    # अगर डेटा 60 सेकंड से नया है, तो पुरानी वैल्यू का उपयोग करें
    if time.time() - cache["last_updated"] < 60:
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
def dashboard():
    try:
        data = get_ticker_data()
        stats = db.get_stats()
        
        # HTML template (Tailwind powered)
        html = f"""
        <html>
        <head><script src="https://cdn.tailwindcss.com"></script></head>
        <body class="bg-slate-900 text-white p-8">
            <h1 class="text-blue-500 font-black text-3xl mb-4">GOAT PRO V17 (PRODUCTION)</h1>
            <div class="grid grid-cols-2 gap-4">
                <div class="p-6 bg-slate-800 rounded-lg">
                    <p class="text-gray-400">NIFTY 50 Price</p>
                    <p class="text-4xl font-bold">{data['price']}</p>
                </div>
                <div class="p-6 bg-slate-800 rounded-lg">
                    <p class="text-gray-400">Trading Stats</p>
                    <p class="text-xl font-bold text-green-400">Wins: {stats.get('wins', 0)}</p>
                    <p class="text-xl font-bold text-red-400">Losses: {stats.get('losses', 0)}</p>
                </div>
            </div>
        </body>
        </html>
        """
        return render_template_string(html)
    except Exception as e:
        logger.error(f"Dashboard Load Error: {e}")
        return "System Maintenance in Progress", 500

@app.route('/health')
def health_check():
    # Render को बताने के लिए कि सर्विस ज़िंदा है
    return {"status": "ok"}, 200

if __name__ == '__main__':
    # Render के दिए गए PORT को यूज़ करें
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
