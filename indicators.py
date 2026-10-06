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


# ─── Aliases used by strategy.py ────────────────────────────────────
# strategy.py calls calc_* names and unpacks tuples, while the functions
# above are named calculate_* and return dicts or None. These thin wrappers
# bridge both gaps. Without them compute_real_signal() raises AttributeError
# on every run and no signal is ever produced.
#
# Every wrapper returns a safe numeric default instead of None, because
# strategy.py does arithmetic and comparisons on the result immediately.

def _last_close(closes: List[float]) -> float:
    """Last close, or 0.0 if the list is empty."""
    return float(closes[-1]) if closes else 0.0


def calc_rsi(closes: List[float], period: int = 14) -> float:
    """RSI, defaulting to the neutral 50.0 when it cannot be computed."""
    val = calculate_rsi(closes, period)
    return 50.0 if val is None else float(val)


def calc_ema(closes: List[float], period: int = 9) -> float:
    """EMA, defaulting to the last close when it cannot be computed."""
    val = calculate_ema(closes, period)
    return _last_close(closes) if val is None else float(val)


def calc_vwap_approx(candles) -> float:
    """Approximate VWAP for volume-less index candles.

    strategy.py passes 5-tuples (seconds, open, high, low, close) from
    Yahoo Finance, which carry no volume, so a true volume-weighted VWAP
    is not computable here. This returns the mean typical price
    (high + low + close) / 3 across the candles — an approximation, not a
    real VWAP. A true VWAP needs a volume-bearing instrument such as
    NIFTY futures.
    """
    if not candles:
        return 0.0

    typicals = []
    for c in candles:
        try:
            if isinstance(c, dict):
                high = float(c.get("high", 0.0))
                low = float(c.get("low", 0.0))
                close = float(c.get("close", 0.0))
            else:
                high = float(c[2])
                low = float(c[3])
                close = float(c[4])
        except (IndexError, KeyError, TypeError, ValueError):
            continue
        typicals.append((high + low + close) / 3.0)

    if not typicals:
        return 0.0
    return round(sum(typicals) / len(typicals), 2)


def calc_macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD as a (macd_line, signal_line) tuple; (0.0, 0.0) when unavailable."""
    val = calculate_macd(closes, fast, slow, signal)
    if not val:
        return 0.0, 0.0
    return float(val.get("macd", 0.0)), float(val.get("signal", 0.0))


def calc_supertrend(highs: List[float], lows: List[float], closes: List[float],
                    period: int = 10, multiplier: float = 3.0):
    """Supertrend as a (trend, value) tuple; ("NEUTRAL", 0.0) when unavailable."""
    val = calculate_supertrend(highs, lows, closes, period, multiplier)
    if not val:
        return "NEUTRAL", 0.0
    return str(val.get("trend", "NEUTRAL")), float(val.get("supertrend", 0.0))
