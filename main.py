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

    # UI इंजन को लाइव करने के लिए नया HTML स्ट्रिंग ब्लॉक
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>GOAT PRO V17</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-white p-4 md:p-8 font-sans">
        <div class="max-w-7xl mx-auto">
            <h1 class="text-blue-500 font-black text-3xl md:text-4xl tracking-tight mb-6">GOAT PRO V17 <span class="text-sm font-mono text-gray-500 bg-slate-950 px-2 py-1 rounded">PRODUCTION</span></h1>
            
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
                <div class="p-6 bg-slate-800 rounded-xl border border-slate-700 shadow-lg">
                    <p class="text-gray-400 text-xs font-bold uppercase tracking-wider">NIFTY 50 (Live Ticker)</p>
                    <p id="price-display" class="text-4xl font-black text-yellow-400 mt-2 tracking-mono">Fetching...</p>
                </div>
                <div class="p-6 bg-slate-800 rounded-xl border border-slate-700 shadow-lg grid grid-cols-2 gap-4">
                    <div class="border-r border-slate-700">
                        <p class="text-gray-400 text-xs font-bold uppercase tracking-wider">Bot Wins</p>
                        <p class="text-3xl font-black text-green-400 mt-2">{{ stats.get('wins', 0) }}</p>
                    </div>
                    <div class="pl-2">
                        <p class="text-gray-400 text-xs font-bold uppercase tracking-wider">Bot Losses</p>
                        <p class="text-3xl font-black text-red-400 mt-2">{{ stats.get('losses', 0) }}</p>
                    </div>
                </div>
            </div>

            <div class="bg-slate-950 border border-slate-800 rounded-2xl p-6 shadow-2xl">
                <div class="flex justify-between items-center mb-6 border-b border-slate-800 pb-4">
                    <h2 class="text-xl font-bold text-white flex items-center">
                        <span class="mr-2 text-green-400 animate-pulse">⚡</span> GOAT AI Alpha Picks
                    </h2>
                    <span class="bg-green-950 text-green-400 text-xs font-bold px-2.5 py-1 rounded-full border border-green-800">DB SYNCED</span>
                </div>

                <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    {% for pick in alpha_picks %}
                    <div class="bg-slate-900 p-5 rounded-xl border border-slate-800 relative overflow-hidden group hover:border-slate-700 transition-all">
                        <div class="flex justify-between items-start mb-3">
                            <span class="text-xl font-black text-white tracking-wide">{{ pick.get('symbol', 'UNKNOWN') }}</span>
                            <span class="bg-slate-800 text-gray-300 text-xs font-mono px-2 py-0.5 rounded">Conf: {{ pick.get('confidence', 0) }}%</span>
                        </div>
                        <div class="space-y-1 text-sm border-t border-slate-800/50 pt-3">
                            <div class="flex justify-between"><span class="text-gray-500">Entry Price:</span> <span class="text-white font-mono font-bold">{{ pick.get('entry_price', 0) }}</span></div>
                            <div class="flex justify-between"><span class="text-gray-500">Target Price:</span> <span class="text-emerald-400 font-mono font-bold">{{ pick.get('target_price', 0) }}</span></div>
                        </div>
                        <button class="mt-4 w-full bg-blue-600 hover:bg-blue-500 text-white py-2 rounded-lg text-xs font-bold tracking-wider transition-all uppercase">Analyze Chart</button>
                    </div>
                    {% else %}
                    <div class="col-span-1 md:col-span-3 text-center py-12 bg-slate-900/50 rounded-xl border border-dashed border-slate-800">
                        <p class="text-gray-500 text-sm">No active alpha picks found in Supabase table.</p>
                    </div>
                    {% endfor %}
                </div>
            </div>
        </div>

        <script>
            async function updatePrice() {
                try {
                    const response = await fetch('/api/price');
                    const json_data = await response.json();
                    if (json_data && json_data.price) {
                        document.getElementById('price-display').innerText = '₹ ' + json_data.price;
                    }
                } catch (err) { 
                    console.error('Ticker sync failed'); 
                }
            }
            updatePrice();
            setInterval(updatePrice, 5000);
        </script>
    </body>
    </html>
    """
    return render_template_string(html, data=data, stats=stats, alpha_picks=alpha_picks)

@app.route('/api/price')
def api_price():
    return jsonify(get_ticker_data())

@app.route('/health')
def health_check():
    return {"status": "ok"}, 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
