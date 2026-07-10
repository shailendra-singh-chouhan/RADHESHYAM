import pandas as pd

def calculate_rsi(cl, p=14):
    if len(cl) < p: return 50
    d = pd.Series(cl).diff()
    g = d.where(d > 0, 0).rolling(p).mean()
    l = -d.where(d < 0, 0).rolling(p).mean()
    return round(100 - (100 / (1 + g/l)).iloc[-1], 2)

def calculate_ema(cl, p):
    return round(pd.Series(cl).ewm(span=p, adjust=False).mean().iloc[-1], 2) if len(cl) >= p else cl[-1]

def calculate_vwap_approx(can):
    if not can: return 0
    tpv = sum(((c['high']+c['low']+c['close'])/3)*c['volume'] for c in can)
    tvol = sum(c['volume'] for c in can)
    return round(tpv/tvol, 2) if tvol > 0 else can[-1]['close']

def calculate_macd(cl):
    s = pd.Series(cl)
    m = s.ewm(span=12).mean() - s.ewm(span=26).mean()
    sig = m.rolling(9).mean()
    return {"macd": round(m.iloc[-1], 2), "signal": round(sig.iloc[-1], 2)} if len(cl) >= 26 else {"macd":0,"signal":0}

def calculate_supertrend(can, p=10, m=3):
    if len(can) < p: return {"trend": "WAIT", "value": 0}
    cl = can[-1]['close']
    v = can[-1]['low'] - 10 # Simple mock
    return {"trend": "BUY" if cl > v else "SELL", "value": round(v, 2)}
