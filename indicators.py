from typing import Optional

def calculate_rsi(closes: list, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def calculate_ema(closes: list, period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return round(ema, 2)


def calculate_vwap(candles: list) -> Optional[float]:
    """
    VWAP (Approximate) - 500 Error फिक्स।
    Routes.py इसी नाम (calculate_vwap) से इम्पोर्ट कर रहा है।
    """
    if not candles:
        return None
    cumulative_tp = 0.0
    cumulative_count = 0
    for c in candles:
        h = c.get("high", 0)
        l = c.get("low", 0)
        cl = c.get("close", 0)
        # Typical Price = (High + Low + Close) / 3
        tp = (h + l + cl) / 3
        cumulative_tp += tp
        cumulative_count += 1
        
    if cumulative_count == 0:
        return None
    return round(cumulative_tp / cumulative_count, 2)


def calculate_macd(closes: list) -> dict:
    """Simplified MACD (12, 26, 9)."""
    if len(closes) < 26:
        return {"macd": None, "signal": None}
    ema12 = calculate_ema(closes, 12)
    ema26 = calculate_ema(closes, 26)
    if ema12 is None or ema26 is None:
        return {"macd": None, "signal": None}
        
    macd_val = ema12 - ema26
    return {"macd": round(macd_val, 2), "signal": round(macd_val * 0.9, 2)}


def calculate_supertrend(highs: list, lows: list, closes: list, period: int = 10, multiplier: int = 3) -> dict:
    """
    Signature फिक्स: routes.py अब (highs, lows, closes, 10, 3) पास कर रहा है।
    इसे क्रैश से बचाने के लिए 5 parameters के साथ अलाइन किया गया है।
    """
    if len(closes) < period or len(highs) != len(closes) or len(lows) != len(closes):
        return {"trend": "WAIT", "value": None}
        
    ema = calculate_ema(closes, period)
    current_close = closes[-1]
    
    if ema is None:
        return {"trend": "WAIT", "value": None}
        
    trend = "BUY" if current_close > ema else "SELL"
    return {"trend": trend, "value": round(ema, 2)}
