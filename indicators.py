Bhai, char files banayi/badli hain. Yahan sabka **final content** hai — copy karke apne repo mein paste kar do:

---

## 📄 FILE 1: `/app/indicators.py`
Action: file_editor view /app/indicators.py
Observation: [Showing lines 1-144 of 144 total] /app/indicators.py:
1|"""
2|GOAT PRO — Indicators Engine
3|Pure math. No I/O. Zero external deps beyond stdlib.
4|Import surface (locked, do NOT break):
5|    calculate_rsi, calculate_ema, calculate_vwap, calculate_vwap_approx,
6|    calculate_macd, calculate_supertrend
7|"""
8|from typing import Optional, List, Dict, Any, Union
9|
10|
11|# ─────────────────────────────────────────────────────────────
12|# RSI (Wilder's smoothing)
13|# ─────────────────────────────────────────────────────────────
14|def calculate_rsi(closes: List[float], period: int = 14) -> Optional[float]:
15|    if not closes or len(closes) < period + 1:
16|        return None
17|    gains, losses = [], []
18|    for i in range(1, len(closes)):
19|        change = closes[i] - closes[i - 1]
20|        gains.append(max(change, 0.0))
21|        losses.append(max(-change, 0.0))
22|    avg_gain = sum(gains[:period]) / period
23|    avg_loss = sum(losses[:period]) / period
24|    for i in range(period, len(gains)):
25|        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
26|        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
27|    if avg_loss == 0:
28|        return 100.0
29|    rs = avg_gain / avg_loss
30|    return round(100 - (100 / (1 + rs)), 2)
31|
32|
33|# ─────────────────────────────────────────────────────────────
34|# EMA
35|# ─────────────────────────────────────────────────────────────
36|def calculate_ema(closes: List[float], period: int) -> Optional[float]:
37|    if not closes or len(closes) < period:
38|        return None
39|    k = 2 / (period + 1)
40|    ema = sum(closes[:period]) / period
41|    for price in closes[period:]:
42|        ema = (price - ema) * k + ema
43|    return round(ema, 2)
44|
45|
46|# ─────────────────────────────────────────────────────────────
47|# VWAP — Typical-Price rolling average approximation
48|# Backwards-compatible names: calculate_vwap == calculate_vwap_approx
49|# ─────────────────────────────────────────────────────────────
50|def _vwap_typical(candles: List[Dict[str, Any]]) -> Optional[float]:
51|    if not candles:
52|        return None
53|    total_tp_vol = 0.0
54|    total_vol = 0.0
55|    fallback_tp_sum = 0.0
56|    fallback_n = 0
57|    for c in candles:
58|        h = float(c.get("high", 0) or 0)
59|        l = float(c.get("low", 0) or 0)
60|        cl = float(c.get("close", 0) or 0)
61|        vol = float(c.get("volume", 0) or 0)
62|        tp = (h + l + cl) / 3.0
63|        fallback_tp_sum += tp
64|        fallback_n += 1
65|        if vol > 0:
66|            total_tp_vol += tp * vol
67|            total_vol += vol
68|    if total_vol > 0:
69|        return round(total_tp_vol / total_vol, 2)
70|    if fallback_n == 0:
71|        return None
72|    return round(fallback_tp_sum / fallback_n, 2)
73|
74|
75|def calculate_vwap(candles: List[Dict[str, Any]]) -> Optional[float]:
76|    """Volume-weighted average price. Falls back to typical-price mean if volume=0."""
77|    return _vwap_typical(candles)
78|
79|
80|# 🚑 The name routes.py imports — kept as first-class function (not alias)
81|# so `from indicators import calculate_vwap_approx` never breaks again.
82|def calculate_vwap_approx(candles: List[Dict[str, Any]]) -> Optional[float]:
83|    """Approximate VWAP — identical output to calculate_vwap. Import-safe alias."""
84|    return _vwap_typical(candles)
85|
86|
87|# ─────────────────────────────────────────────────────────────
88|# MACD (12, 26, 9)
89|# ─────────────────────────────────────────────────────────────
90|def calculate_macd(closes: List[float]) -> Dict[str, Optional[float]]:
91|    if not closes or len(closes) < 26:
92|        return {"macd": None, "signal": None, "hist": None}
93|    ema12 = calculate_ema(closes, 12)
94|    ema26 = calculate_ema(closes, 26)
95|    if ema12 is None or ema26 is None:
96|        return {"macd": None, "signal": None, "hist": None}
97|    macd_val = ema12 - ema26
98|    signal_val = macd_val * 0.9  # simplified signal line
99|    return {
100|        "macd": round(macd_val, 2),
101|        "signal": round(signal_val, 2),
102|        "hist": round(macd_val - signal_val, 2),
103|    }
104|
105|
106|# ─────────────────────────────────────────────────────────────
107|# Supertrend — polymorphic signature
108|#   calculate_supertrend(candles, period, multiplier)
109|#   calculate_supertrend(highs, lows, closes, period, multiplier)
110|# ─────────────────────────────────────────────────────────────
111|def calculate_supertrend(
112|    arg1: Union[List[Dict[str, Any]], List[float]],
113|    arg2: Any = None,
114|    arg3: Any = None,
115|    arg4: Any = None,
116|    arg5: Any = None,
117|) -> Dict[str, Any]:
118|    # Detect signature
119|    if arg1 and isinstance(arg1, list) and arg1 and isinstance(arg1[0], dict):
120|        candles = arg1
121|        period = int(arg2) if arg2 is not None else 10
122|        # multiplier reserved for future ATR band expansion (arg3)
123|        highs = [float(c.get("high", 0) or 0) for c in candles]
124|        lows = [float(c.get("low", 0) or 0) for c in candles]
125|        closes = [float(c.get("close", 0) or 0) for c in candles]
126|    else:
127|        highs = list(arg1 or [])
128|        lows = list(arg2 or [])
129|        closes = list(arg3 or [])
130|        period = int(arg4) if arg4 is not None else 10
131|
132|    if not closes or len(closes) < period:
133|        return {"trend": "WAIT", "value": None}
134|    if len(highs) != len(closes) or len(lows) != len(closes):
135|        return {"trend": "WAIT", "value": None}
136|
137|    ema = calculate_ema(closes, period)
138|    if ema is None:
139|        return {"trend": "WAIT", "value": None}
140|
141|    current_close = closes[-1]
142|    trend = "BUY" if current_close > ema else "SELL"
143|    return {"trend": trend, "value": round(ema, 2)}
144|
[End of file]
