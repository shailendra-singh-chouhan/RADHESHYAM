def process():
    try:
        # MCX Crude Oil Real-time approximation
        c_ticker = yf.Ticker("CL=F")
        c_data = c_ticker.history(period="1d", interval="1m")
        
        # MCX और Global Brent/WTI के बीच का डायनेमिक गैप
        # यहाँ 96.50 का फैक्टर मार्केट ओपनिंग के साथ एडजस्ट करें
        live_price = c_data['Close'].iloc[-1] * 96.50 
        
        # ऑटो-कैलकुलेटेड लेवल्स (लाइव प्राइस के आधार पर)
        vwap = round(live_price * 0.999, 2)
        pivot = round(live_price * 1.001, 2)
        
        return {
            "market_status": "लाइव मार्केट",
            "spot": "23286.00", 
            "vwap": f"{vwap}",
            "jadui_spot": f"{pivot}",
            "crude": round(live_price, 2),
            "crude_vwap": vwap,
            "crude_jadui": pivot,
            "signal": f"BUY ABOVE {round(live_price + 8, 2)}",
            "target": "T1: +40pts | SL: -20pts"
        }
    except:
        return {"market_status": "SYNC ERROR", "spot": "0", "vwap": "0", "crude": "0", "signal": "RETRYING..."}
