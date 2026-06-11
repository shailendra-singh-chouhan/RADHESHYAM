import os
import time
import yfinance as yf
from flask import Flask, render_template_string, jsonify
from logzero import logger
from db_manager import DatabaseManager

app = Flask(__name__)
db = DatabaseManager()

# In-memory cache window to strictly avoid yfinance 429 throttling
cache = {"price": 0, "last_updated": 0}

def get_ticker_data(symbol="^NSEI"):
    global cache
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
    data = get_ticker_data()
    stats = db.get_stats()
    
    try:
        alpha_picks = db.get_alpha_picks()
    except Exception as e:
        logger.error(f"Alpha Picks UI Engine Fallback Triggered: {e}")
        alpha_picks = []

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>GOAT PRO V17</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-white p-8">
        <h1 class="text-blue-500 font-black text-3xl mb-6">GOAT PRO V17 (PRODUCTION)</h1>
        
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
            <div class="p-6 bg-slate-800 rounded-lg border border-slate-700 shadow-md">
                <p class="text-gray-400 text-sm font-semibold tracking-wider">NIFTY 50 (Live)</p>
                <p id="price-display" class="text-4xl font-bold text-yellow-400 mt-2">{{ data['price'] }}</p>
            </div>
            <div class="p-6 bg-slate-800 rounded-lg border border-slate-700 shadow-md grid grid-cols-2 gap-4">
                <div>
                    <p class="text-gray-400 text-sm font-semibold tracking-wider">Trading Stats</p>
                    <p class="text-2xl font-bold text-green-400 mt-2">Wins: {{ stats.get('wins', 0) }}</p>
                </div>
                <div class="flex items-end">
                    <p class="text-2xl font-bold text-red-400">Losses: {{ stats.get('losses', 0) }}</p>
                </div>
            </div>
        </div>

        <div class="bg-gray-900 border border-gray-700 rounded-xl p-6 shadow-2xl">
            <div class="flex justify-between items-center mb-6">
                <h2 class="text-xl font-bold text-white flex items-center">
                    <span class="mr-2 text-green-400">⚡</span> GOAT AI Alpha Picks
                </h2>
                <span class="bg-green-900 text-green-300 text-xs font-bold px-2 py-1 rounded">DB SYNCED</span>
            </div>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                {% for pick in picks %}
                <div class="bg-gray-800 p-4 rounded-lg border-l-4 
                    {% if pick.confidence >= 90 %} border-green-500
                    {% elif pick.confidence >= 80 %} border-blue-500
                    {% else %} border-yellow-500 {% endif %}">
                    <div class="flex justify-between">
                        <span class="text-lg font-bold text-white">{{ pick.symbol }}</span>
                        <span class="text-xs text-gray-400">Conf: {{ pick.confidence }}%</span>
                    </div>
                    <div class="mt-2 text-sm text-gray-300">Entry: <span class="text-white font-mono">{{ pick.entry_price }}</span></div>
                    <div class="text-sm text-gray-300">Target: <span class="text-white font-mono">{{ pick.target_price }}</span></div>
                    <button class="mt-4 w-full bg-slate-700 hover:bg-slate-600 text-white py-2 rounded text-sm font-bold transition-all">ANALYZE</button>
                </div>
                {% else %}
                <p class="text-gray-500 col-span-3 py-4 text-center">No active alpha picks currently available in database.</p>
                {% endfor %}
            </div>
        </div>

        <script>
            setInterval(async () => {
                try {
                    const response = await fetch('/api/price');
                    const json_data = await response.json();
                    if (json_data && json_data.price) {
                        document.getElementById('price-display').innerText = json_data.price;
                    }
                } catch (err) { 
                    console.error('Live ticker auto-sync failed'); 
                }
            }, 5000);
        </script>
    </body>
    </html>
    """
    return render_template_string(html, data=data, stats=stats, picks=alpha_picks)

@app.route('/api/price')
def api_price():
    return jsonify(get_ticker_data())

@app.route('/health')
def health_check():
    return {"status": "ok"}, 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
