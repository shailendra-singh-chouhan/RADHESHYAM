import pandas as pd
import numpy as np

def calculate_rsi(series, period=14):
    if len(series) < period: return 50
    delta = pd.Series(series).diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return round(100 - (100 / (1 + rs)).iloc[-1], 2)

def calculate_ema(series, period):
    if len(series) < period: return series[-1]
    return round(pd.Series(series).ewm(span=period, adjust=False).mean().iloc[-1], 2)

def calculate_vwap_approx(candles):
    if not candles: return 0.0
    tpv = sum(((c['high'] + c['low'] + c['close']) / 3) * c['volume'] for c in candles)
    tvol = sum(c['volume'] for c in candles)
    return round(tpv / tvol, 2) if tvol > 0 else candles[-1]['close']

def calculate_macd(series, fast=12, slow=26, signal=9):
    if len(series) < slow: return {"macd": 0, "signal": 0}
    s = pd.Series(series)
    exp1 = s.ewm(span=fast, adjust=False).mean()
    exp2 = s.ewm(span=slow, adjust=False).mean()
    macd = exp1 - exp2
    sig = macd.rolling(window=signal).mean()
    return {"macd": round(macd.iloc[-1], 2), "signal": round(sig.iloc[-1], 2)}

def calculate_supertrend(high, low, close, period=10, multiplier=3):
    if len(high) < period: return {"trend": "WAIT", "value": close[-1]}
    # Simplified ATR based Supertrend
    atr = pd.Series(high).rolling(period).mean() - pd.Series(low).rolling(period).mean()
    hl2 = (pd.Series(high) + pd.Series(low)) / 2
    upper = hl2 + (multiplier * atr)
    lower = hl2 - (multiplier * atr)
    curr_close = close[-1]
    trend = "BUY" if curr_close > lower.iloc[-1] else "SELL"
    return {"trend": trend, "value": round(lower.iloc[-1] if trend == "BUY" else upper.iloc[-1], 2)}
