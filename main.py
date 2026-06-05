from flask import Flask, send_file, jsonify
import yfinance as yf
import os

app = Flask(__name__)

@app.route('/')
def home():
    return send_file('dashboard.html')

@app.route('/api/data')
def get_market():
    try:
        ticker = yf.Ticker("^NSEI")
        data = ticker.history(period="1d")
        price = data['Close'].iloc[-1]
        return jsonify({"price": round(price, 2)})
    except:
        return jsonify({"price": 24408.5})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
