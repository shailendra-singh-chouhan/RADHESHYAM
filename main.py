import os, random, yfinance as yf, pandas as pd
from flask import Flask, render_template_string, jsonify

app = Flask(__name__)

# पूरा HTML स्ट्रक्चर, डेटा को सही ढंग से पढ़ने के लिए फिक्स्ड
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-100 p-4">
    <div class="max-w-2xl mx-auto space-y-4">
        <div class="bg-white p-6 rounded-xl shadow-lg border-2 border-blue-500">
            <h1 class="text-2xl font-black">⚡ GOAT PRO हिंदी डेटा कोर</h1>
            <div class="grid grid-cols-2 gap-4 mt-4">
                <div class="p-4 bg-blue-50 rounded-lg">
                    <p class="text-sm font-bold">निफ्टी स्पॉट</p>
                    <p class="text-2xl font-black">₹{{ m.spot }}</p>
                </div>
                <div class="p-4 bg-orange-50 rounded-lg">
                    <p class="text-sm font-bold">क्रूड ऑयल</p>
                    <p class="text-2xl font-black text-orange-600">₹{{ m.crude }}</p>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

def get_data():
    try:
        # निफ्टी का लेटेस्ट डेटा
        nifty = yf.Ticker("^NSEI").history(period="1d", interval="1m")
        # क्रूड का लेटेस्ट डेटा
        crude = yf.Ticker("CL=F").history(period="1d", interval="1m")
        
        return {
            "spot": round(nifty['Close'].iloc[-1], 2) if not nifty.empty else 0.0,
            "crude": round(crude['Close'].iloc[-1], 2) if not crude.empty else 0.0
        }
    except:
        return {"spot": 0.0, "crude": 0.0}

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, m=get_data())

@app.route('/api/refresh')
def api_refresh():
    return jsonify(get_data())

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
