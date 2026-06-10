import os
import yfinance as yf
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

# Ultra-Lightweight Sniper UI
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="hi">
<head>
    <meta charset="UTF-8">
    <title>BRAHMASTRA V9</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-black text-white p-4">
    <div class="max-w-sm mx-auto">
        <h1 class="text-blue-500 font-bold">GOAT PRO QUANT</h1>
        <div class="mt-4 p-4 bg-slate-900 rounded-lg">
            <p>NIFTY: {{ m.spot }}</p>
            <p class="text-orange-400">CRUDE: {{ m.crude }}</p>
        </div>
        <p class="mt-4 text-xs">{{ m.signal }}</p>
    </div>
</body>
</html>
"""

def get_data():
    try:
        # केवल सबसे जरूरी डेटा, बाकी भारी कैलकुलेशन हटा दी
        c_ticker = yf.Ticker("CL=F")
        c_hist = c_ticker.history(period="1d")
        price = round(c_hist['Close'].iloc[-1] * 96.50, 2) if not c_hist.empty else 8258.0
        return {"spot": "23286", "crude": price, "signal": "BUY ABOVE CRUDE"}
    except:
        return {"spot": "ERR", "crude": "0", "signal": "RETRY..."}

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, m=get_data())

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
