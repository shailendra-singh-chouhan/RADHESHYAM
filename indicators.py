"\"\"\"
indicators.py — technical-indicator helpers (RSI, EMA, VWAP, MACD, Supertrend)
All functions accept plain lists / candle dicts; safe on short inputs.
\"\"\"
import pandas as pd
from typing import List, Dict, Any, Optional


def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if not closes or len(closes) < period + 1:
        return None
    s = pd.Series(closes)
    delta = s.diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-9)
    rsi = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 2)


def calculate_ema(closes: List[float], period: int) -> Optional[float]:
    if not closes or len(closes) < period:
        return round(float(closes[-1]), 2) if closes else None
    ema = pd.Series(closes).ewm(span=period, adjust=False).mean().iloc[-1]
    return round(float(ema), 2)


def calculate_vwap_approx(candles: List[Dict[str, Any]]) -> Optional[float]:
    \"\"\"Volume-weighted where possible; falls back to typical-price mean for indices.\"\"\"
    if not candles:
        return None
    tp = [(c[\"high\"] + c[\"low\"] + c[\"close\"]) / 3 for c in candles]
    vol = [c.get(\"volume\", 0) for c in candles]
    if sum(vol) > 0:
        return round(sum(t * v for t, v in zip(tp, vol)) / sum(vol), 2)
    return round(sum(tp) / len(tp), 2)


def calculate_macd(closes: List[float]) -> Optional[Dict[str, float]]:
    if not closes or len(closes) < 26:
        return {\"macd\": 0.0, \"signal\": 0.0}
    s = pd.Series(closes)
    macd_line = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    sig_line = macd_line.ewm(span=9, adjust=False).mean()
    return {
        \"macd\": round(float(macd_line.iloc[-1]), 2),
        \"signal\": round(float(sig_line.iloc[-1]), 2),
    }


def calculate_supertrend(candles: List[Dict[str, Any]], period: int = 10,
                         multiplier: float = 3.0) -> Dict[str, Any]:
    \"\"\"Real Supertrend using ATR.\"\"\"
    if not candles or len(candles) < period + 1:
        return {\"trend\": \"WAIT\", \"value\": 0.0}
    df = pd.DataFrame(candles)
    hl2 = (df[\"high\"] + df[\"low\"]) / 2
    tr = pd.concat([
        (df[\"high\"] - df[\"low\"]).abs(),
        (df[\"high\"] - df[\"close\"].shift()).abs(),
        (df[\"low\"] - df[\"close\"].shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr

    trend = [\"BUY\"] * len(df)
    st = lower.copy()
    for i in range(1, len(df)):
        if df[\"close\"].iloc[i] > upper.iloc[i - 1]:
            trend[i] = \"BUY\"
        elif df[\"close\"].iloc[i] < lower.iloc[i - 1]:
            trend[i] = \"SELL\"
        else:
            trend[i] = trend[i - 1]
        st.iloc[i] = lower.iloc[i] if trend[i] == \"BUY\" else upper.iloc[i]
    return {\"trend\": trend[-1], \"value\": round(float(st.iloc[-1]), 2)}
"
Observation: Overwrite successful: /app/radheshyam/indicators.py
