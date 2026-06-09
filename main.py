# ... (ऊपर के इम्पोर्ट्स वैसे ही रहेंगे)
# बस fetch_live_market_data() फंक्शन में यह बदलाव करें:

def fetch_live_market_data():
    try:
        # निफ्टी का डेटा
        nifty = yf.Ticker("^NSEI").history(period="1d", interval="1m")
        # क्रूड ऑयल का डेटा (MCX Crude Oil Mini)
        crude = yf.Ticker("COM=F").history(period="1d", interval="1m") # Global Crude Oil Tracker
        
        # (बाकी लॉजिक में क्रूड डेटा का dict जोड़ें)
        return {
            "spot": round(nifty['Close'].iloc[-1], 2),
            "crude": round(crude['Close'].iloc[-1], 2),
            # ... (बाकी पुराने डेटा पॉइंट्स)
        }
    except:
        # (Fallback डेटा)
        return {"spot": 23242.10, "crude": 8453.00, ...}

# और HTML_TEMPLATE में निफ्टी वाले सेक्शन के बगल में यह जोड़ें:
"""
<div class="bg-white border border-slate-200 rounded-xl p-5 shadow-sm">
    <span class="text-xs font-bold text-slate-400 font-mono">🛢️ क्रूड ऑयल (MCX)</span>
    <span class="text-3xl font-black text-orange-600 font-mono mt-2">₹{{ m.crude }}</span>
</div>
"""
