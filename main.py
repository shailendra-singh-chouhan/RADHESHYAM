import os, yfinance as yf
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

# Fixed Template - Sab kuch yahan clear dikhega
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-900 text-white p-6">
    <div class="max-w-3xl mx-auto">
        <h1 class="text-3xl font-black mb-6 text-blue-400">⚡ GOAT PRO DUAL CORE</h1>
        <div class="grid grid-cols-2 gap-4">
            <div class="bg-blue-900 p-6 rounded-xl border border-blue-500">
                <p class="text-gray-400 font-bold">NIFTY 50</p>
                <p class="text-4xl font-black">₹{{ m.spot }}</p>
            </div>
            <div class="bg-orange-900 p-6 rounded-xl border border-orange-500">
                <p class="text-gray-400 font-bold">CRUDE OIL</p>
                <p class="text-4xl font-black">₹{{ m.crude }}</p>
            </div>
        </div>
        <p class="mt-6 text-sm text-gray-500">System is stable. All indicators re-connected.</p>
    </div>
</body>
</html>
"""

def fetch_data():
    try:
        nifty = yf.Ticker("^NSEI").history(period="1d")
        crude = yf.Ticker("CL=F").history(period="1d")
        return {
            "spot": round(nifty['Close'].iloc[-1], 2),
            "crude": round(crude['Close'].iloc[-1], 2)
        }
    except:
        return {"spot": 0.0, "crude": 0.0}

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, m=fetch_data())

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
