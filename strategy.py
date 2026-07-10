import logging
from typing import List
from angel_client import get_angel_client
from indicators import calculate_rsi, calculate_ema, calculate_vwap_approx, calculate_macd, calculate_supertrend

logger = logging.getLogger(__name__)

def detect_patterns(candles: List[dict]) -> List[dict]:
    if len(candles) < 2: return []
    c1, c2 = candles[-1], candles[-2]
    body = abs(c1['close'] - c1['open'])
    range_ = c1['high'] - c1['low']
    if range_ == 0: return []
    p = []
    if (c1['low'] < min(c1['open'], c1['close'])) and (min(c1['open'], c1['close']) - c1['low']) > 2*body:
        p.append({"name": "Hammer", "icon": "🔨", "signal": "BULLISH"})
    if (c1['high'] > max(c1['open'], c1['close'])) and (c1['high'] - max(c1['open'], c1['close'])) > 2*body:
        p.append({"name": "Shooting Star", "icon": "🌠", "signal": "BEARISH"})
    return p

def generate_signal(candles: List[dict], spot: float) -> dict:
    if not candles or len(candles) < 21: return {"signal": "WAIT", "note": "Syncing..."}
    cl = [c['close'] for c in candles]
    hi = [c['high'] for c in candles]
    lo = [c['low'] for c in candles]
    
    rsi = calculate_rsi(cl)
    vwap = calculate_vwap_approx(candles)
    st = calculate_supertrend(hi, lo, cl)
    pats = detect_patterns(candles)
    
    score = 0
    if spot > vwap: score += 1
    if st['trend'] == "BUY": score += 1
    if any(p['signal'] == "BULLISH" for p in pats): score += 2
    
    if spot < vwap: score -= 1
    if st['trend'] == "SELL": score -= 1
    if any(p['signal'] == "BEARISH" for p in pats): score -= 2
    
    sig = "WAIT"
    if score >= 2: sig = "LONG"
    elif score <= -2: sig = "SHORT"
    
    return {
        "signal": sig, "score": score, "patterns": pats,
        "note": "JADUI SPOT!" if abs(score) >= 3 else "Trend following",
        "entry": spot, "sl": spot * 0.995 if sig == "LONG" else spot * 1.005,
        "t1": spot * 1.01 if sig == "LONG" else spot * 0.99
    }

def get_oi_data(index="NIFTY"):
    return get_angel_client().fetch_nse_option_chain(index)

def calculate_greeks(spot, strike, option_type="CE"):
    return {"iv": 14.5, "delta": 0.5, "theta": -12, "gamma": 0.004, "vega": 8}

def fetch_institutional_stats():
    return {"fii_net": "Bullish", "dii_net": "Supportive"}

def fetch_global_markets():
    return {"nasdaq": "Green", "dji": "Flat"}

def start_background_threads(): pass
