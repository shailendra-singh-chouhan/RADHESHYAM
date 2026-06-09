import os, random, yfinance as yf, pandas as pd
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

# HTML Template में क्रूड का ब्लॉक पहले से जोड़ा हुआ है
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GOAT PRO Command Center</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-50 p-6">
    <div class="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-4xl mx-auto">
        <div class="bg-white p-6 rounded-xl shadow-md border">
            <h2 class="text-xl font-bold">निफ्टी: ₹{{ m.spot }}</h2>
        </div>
        <div class="bg-white p-6 rounded-xl shadow-md border">
            <h2 class="text-xl font-bold text-orange-600">क्रूड ऑयल: ₹{{ m.crude }}</h2>
        </div>
    </div>
</body>
</html>
"""

def fetch_live_market_data():
    try:
        # निफ्टी और क्रूड का डेटा फेच करना
        nifty = yf.Ticker("^NSEI").history(period="1d", interval="1m")
        crude = yf.Ticker("CL=F").history(period="1d", interval="1m") 
        
        spot = round(nifty['Close'].iloc[-1], 2) if not nifty.empty else 23200.0
        crude_val = round(crude['Close'].iloc[-1], 2) if not crude.empty else 8400.0
        
        return {"spot": spot, "crude": crude_val}
    except:
        return {"spot": 23200.0, "crude": 8400.0}

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, m=fetch_live_market_data())

@app.route('/api/refresh')
def api_refresh():
    return jsonify(fetch_live_market_data())

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)), debug=False)
