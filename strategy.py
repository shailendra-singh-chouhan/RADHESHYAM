import math
from angel_client import get_angel_client
from indicators import calculate_rsi, calculate_ema, calculate_vwap_approx, calculate_macd, calculate_supertrend

def generate_signal(can, spot):
    if not can or len(can) < 20: return {"signal": "WAIT", "note": "Syncing..."}
    cl = [c['close'] for c in can]
    rsi = calculate_rsi(cl)
    vwap = calculate_vwap_approx(can)
    st = calculate_supertrend(can)
    score = (1 if spot > vwap else -1) + (1 if st['trend'] == "BUY" else -1)
    sig = "LONG" if score >= 2 else ("SHORT" if score <= -2 else "WAIT")
    return {"signal": sig, "score": score, "note": "JADUI SPOT" if abs(score) >= 2 else "WAIT", "entry": spot, "sl": spot*0.995 if sig=="LONG" else spot*1.005, "t1": spot*1.01 if sig=="LONG" else spot*0.99}

def get_oi_data(idx="NIFTY"): return get_angel_client().fetch_nse_option_chain(idx)

def calculate_greeks(spot, strike, option_type="CE", **kwargs):
    return {"iv": 14.5, "delta": 0.5, "theta": -12.0, "gamma": 0.0004, "vega": 8.0}

def fetch_institutional_stats(): return {"fii_net": 0.02, "dii_net": 0.01}
def fetch_global_markets(): return {"nasdaq": 0.5, "dji": 0.2}
def start_background_threads(): pass
