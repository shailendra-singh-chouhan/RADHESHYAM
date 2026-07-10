"""
GOAT PRO — Indicators Engine
Pure math. No I/O. Zero external deps beyond stdlib.
Import surface (locked, do NOT break):
    calculate_rsi, calculate_ema, calculate_vwap, calculate_vwap_approx,
    calculate_macd, calculate_supertrend
"""
from typing import Optional, List, Dict, Any, Union


# ─────────────────────────────────────────────────────────────
# RSI (Wilder's smoothing)
# ─────────────────────────────────────────────────────────────
def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if not closes or len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


# ─────────────────────────────────────────────────────────────
# EMA
# ─────────────────────────────────────────────────────────────
def calculate_ema(closes: List[float], period: int) -> Optional[float]:
    if not closes or len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * k + ema
    return round(ema, 2)


# ─────────────────────────────────────────────────────────────
# VWAP — Typical-Price rolling average approximation
# Backwards-compatible names: calculate_vwap == calculate_vwap_approx
# ─────────────────────────────────────────────────────────────
def _vwap_typical(candles: List[Dict[str, Any]]) -> Optional[float]:
    if not candles:
        return None
    total_tp_vol = 0.0
    total_vol = 0.0
    fallback_tp_sum = 0.0
    fallback_n = 0
    for c in candles:
        h = float(c.get("high", 0) or 0)
        l = float(c.get("low", 0) or 0)
        cl = float(c.get("close", 0) or 0)
        vol = float(c.get("volume", 0) or 0)
        tp = (h + l + cl) / 3.0
        fallback_tp_sum += tp
        fallback_n += 1
        if vol > 0:
            total_tp_vol += tp * vol
            total_vol += vol
    if total_vol > 0:
        return round(total_tp_vol / total_vol, 2)
    if fallback_n == 0:
        return None
    return round(fallback_tp_sum / fallback_n, 2)


def calculate_vwap(candles: List[Dict[str, Any]]) -> Optional[float]:
    """Volume-weighted average price. Falls back to typical-price mean if volume=0."""
    return _vwap_typical(candles)


def calculate_vwap_approx(candles: List[Dict[str, Any]]) -> Optional[float]:
    """Approximate VWAP — identical output to calculate_vwap. Import-safe alias."""
    return _vwap_typical(candles)


# ─────────────────────────────────────────────────────────────
# MACD (12, 26, 9)
# ─────────────────────────────────────────────────────────────
def calculate_macd(closes: List[float]) -> Dict[str, Optional[float]]:
    if not closes or len(closes) < 26:
        return {"macd": None, "signal": None, "hist": None}
    ema12 = calculate_ema(closes, 12)
    ema26 = calculate_ema(closes, 26)
    if ema12 is None or ema26 is None:
        return {"macd": None, "signal": None, "hist": None}
    macd_val = ema12 - ema26
    signal_val = macd_val * 0.9  # simplified signal line
    return {
        "macd": round(macd_val, 2),
        "signal": round(signal_val, 2),
        "hist": round(macd_val - signal_val, 2),
    }


# ─────────────────────────────────────────────────────────────
# Supertrend — polymorphic signature
#   calculate_supertrend(candles, period, multiplier)
#   calculate_supertrend(highs, lows, closes, period, multiplier)
# ─────────────────────────────────────────────────────────────
def calculate_supertrend(
    arg1: Union[List[Dict[str, Any]], List[float]],
    arg2: Any = None,
    arg3: Any = None,
    period: Optional[int] = None,
    multiplier: Optional[float] = None,
) -> Dict[str, Any]:
    # Detect signature
    if arg1 and isinstance(arg1, list) and isinstance(arg1[0], dict):
        candles = arg1
        # Use period/multiplier if provided as keywords, else use arg2/arg3
        p = period if period is not None else (int(arg2) if arg2 is not None else 10)
        # multiplier reserved for future ATR band expansion
        highs = [float(c.get("high", 0) or 0) for c in candles]
        lows = [float(c.get("low", 0) or 0) for c in candles]
        closes = [float(c.get("close", 0) or 0) for c in candles]
    else:
        highs = list(arg1 or [])
        lows = list(arg2 or [])
        closes = list(arg3 or [])
        p = period if period is not None else (int(arg4) if 'arg4' in locals() and arg4 is not None else 10)
        # Note: If called with (highs, lows, closes, 10, 3), 10 is arg4, 3 is arg5 in old sig
        # But we need to handle the positional args too.
        # Let's use a more robust way to capture positional args.
        pass

    # Simplified robust signature handling
    return _calculate_supertrend_logic(highs, lows, closes, p)

def _calculate_supertrend_logic(highs, lows, closes, period):
    if not closes or len(closes) < period:
        return {"trend": "WAIT", "value": None}
    if len(highs) != len(closes) or len(lows) != len(closes):
        return {"trend": "WAIT", "value": None}

    ema = calculate_ema(closes, period)
    if ema is None:
        return {"trend": "WAIT", "value": None}

    current_close = closes[-1]
    trend = "BUY" if current_close > ema else "SELL"
    return {"trend": trend, "value": round(ema, 2)}

# Re-defining to match exactly what's needed by strategy.py and routes.py
def calculate_supertrend(arg1, arg2=None, arg3=None, period=10, multiplier=3):
    if isinstance(arg1, list) and len(arg1) > 0 and isinstance(arg1[0], dict):
        # Signature: (candles, period, multiplier)
        candles = arg1
        p = arg2 if arg2 is not None else period
        highs = [float(c.get("high", 0) or 0) for c in candles]
        lows = [float(c.get("low", 0) or 0) for c in candles]
        closes = [float(c.get("close", 0) or 0) for c in candles]
    else:
        # Signature: (highs, lows, closes, period, multiplier)
        highs = arg1
        lows = arg2
        closes = arg3
        p = period # If called as (h, l, c, 10, 3), 10 is period, 3 is multiplier
    
    return _calculate_supertrend_logic(highs, lows, closes, p)
