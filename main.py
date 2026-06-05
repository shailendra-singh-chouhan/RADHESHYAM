"""
GOAT PRO — Real Angel One + Telegram Bot
Render Deploy Ready — CORS Fixed
"""

import os
import time
import threading
import pyotp
import telebot
import yfinance as yf
from flask import Flask, jsonify
from flask_cors import CORS
from SmartApi import SmartConnect

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
CLIENT_ID   = os.environ.get("ANGEL_CLIENT_ID", "")
API_KEY     = os.environ.get("ANGEL_API_KEY", "")
TOTP_SECRET = os.environ.get("ANGEL_TOTP_SECRET", "")
MPIN        = os.environ.get("ANGEL_MPIN", "")

bot = None
if BOT_TOKEN:
    try:
        bot = telebot.TeleBot(BOT_TOKEN)
        print("✅ Bot initialized")
    except Exception as e:
        print(f"⚠️ Bot error: {e}")
else:
    print("⚠️ BOT_TOKEN not set!")

# ─────────────────────────────────────────
# FLASK + CORS
# ─────────────────────────────────────────
app = Flask(__name__)
CORS(app)

@app.route('/')
def home():
    return jsonify({"status": "ok", "message": "GOAT PRO Running"}), 200

@app.route('/health')
def health():
    return jsonify({"status": "ok", "bot": "running" if bot else "no token"}), 200

@app.route('/api/market')
def market_api():
    try:
        data = get_all_market_data()
        return jsonify(data), 200
    except Exception as e:
        print(f"❌ /api/market error: {e}")
        return jsonify({"error": str(e)}), 500

# ─────────────────────────────────────────
# ANGEL ONE LOGIN
# ─────────────────────────────────────────
angel_obj = None
angel_token = None
angel_last_login = 0

def angel_login():
    global angel_obj, angel_token, angel_last_login
    try:
        totp = pyotp.TOTP(TOTP_SECRET).now()
        obj = SmartConnect(api_key=API_KEY)
        data = obj.generateSession(CLIENT_ID, MPIN, totp)
        if data['status']:
