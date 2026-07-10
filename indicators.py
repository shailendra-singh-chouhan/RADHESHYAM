"""
indicators.py — technical-indicator helpers (RSI, EMA, VWAP, MACD, Supertrend)
All functions accept plain lists / candle dicts; safe on short inputs.
"""

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


def calculate_ema(closes: List[float], period: int = 9) -> Optional[float]:
    if not closes or len(closes) < period:
        return None
    s = pd.Series(closes)
    ema = s.ewm(span=period, adjust=False).mean()
    return round(float(ema.iloc[-1]), 2)


def calculate_vwap(candles: List[Dict[str, Any]]) -> Optional[float]:
    if not candles or len(candles) < 1:
        return None
    total_vol = 0.0
    total_pv = 0.0
    for c in candles:
        if c.get("volume", 0) > 0:
            typical = (c.get("high", 0) + c.get("low", 0) + c.get("close", 0)) / 3.0
            total_pv += typical * c["volume"]
            total_vol += c["volume"]
    if total_vol == 0:
        return None
    return round(total_pv / total_vol, 2)


def calculate_macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[Dict[str, Any]]:
    if not closes or len(closes) < slow + signal:
        return None
    s = pd.Series(closes)
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return {
        "macd": round(float(macd_line.iloc[-1]), 4),
        "signal": round(float(signal_line.iloc[-1]), 4),
        "histogram": round(float(histogram.iloc[-1]), 4),
    }


def calculate_supertrend(highs: List[float], lows: List[float], closes: List[float], period: int = 10, multiplier: float = 3.0) -> Optional[Dict[str, Any]]:
    if not closes or len(closes) < period + 1:
        return None
    df = pd.DataFrame({"high": highs, "low": lows, "close": closes})
    df["atr"] = pd.concat([
        df["high"] - df["low"],
        abs(df["high"] - df["close"].shift(1)),
        abs(df["low"] - df["close"].shift(1))
    ], axis=1).max(axis=1).rolling(window=period).mean()

    df["upper_band"] = ((df["high"] + df["low"]) / 2) + (multiplier * df["atr"])
    df["lower_band"] = ((df["high"] + df["low"]) / 2) - (multiplier * df["atr"])

    st = []
    trend = []
    for i in range(len(df)):
        if i == 0:
            st.append(df["upper_band"].iloc[i])
            trend.append("BUY")
        else:
            if trend[-1] == "BUY":
                st.append(max(df["lower_band"].iloc[i], st[-1]))
                if df["close"].iloc[i] < st[-1]:
                    trend.append("SELL")
                else:
                    trend.append("BUY")
            else:
                st.append(min(df["upper_band"].iloc[i], st[-1]))
                if df["close"].iloc[i] > st[-1]:
                    trend.append("BUY")
                else:
                    trend.append("SELL")

    return {
        "supertrend": round(float(st[-1]), 2),
        "trend": trend[-1],
    }
