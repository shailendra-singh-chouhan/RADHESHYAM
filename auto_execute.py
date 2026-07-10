## 📄 FILE 2: `/app/auto_execute.py` (NEW file)
Action: file_editor view /app/auto_execute.py
Observation: [Showing lines 1-207 of 207 total] /app/auto_execute.py:
1|"""
2|auto_execute.py — GOAT PRO Live Position Monitor + Kill-Switch Guardrail
3|========================================================================
4|Responsibilities:
5|  1. Watch every ACTIVE trade on every tick, evaluate Target / Stop-Loss
6|     against the live spot, and hand off closure to trading.close_paper_trade.
7|  2. Enforce a hard daily-loss Kill-Switch that:
8|        - Force-closes every open ACTIVE position for the calendar day.
9|        - Rejects any further signal for the rest of the trading day.
10|     The switch auto-resets on the next calendar date (IST).
11|
12|Design notes:
13|  - Zero blocking I/O. Callable from any background poller / strategy tick.
14|  - Thread-safe (module-level lock).
15|  - Pure Python, no new deps. Uses existing trading.close_paper_trade helper.
16|"""
17|from __future__ import annotations
18|
19|import threading
20|from datetime import date
21|from typing import Optional, Tuple, List
22|
23|from logzero import logger
24|from sqlalchemy.orm import Session
25|from sqlalchemy import func
26|
27|import config
28|from models import Trade
29|from trading import close_paper_trade
30|
31|
32|# ─────────────────────────────────────────────────────────────
33|# Kill-Switch state (per calendar day, IST)
34|# ─────────────────────────────────────────────────────────────
35|_kill_lock = threading.Lock()
36|_kill_state = {
37|    "tripped_on": None,     # type: Optional[date]
38|    "reason": "",           # human-readable
39|    "day_pnl": 0.0,
40|    "closed_ids": [],       # trade ids force-closed by the switch
41|}
42|
43|
44|def _today_ist() -> date:
45|    return config.get_ist_now().date()
46|
47|
48|def _reset_if_new_day() -> None:
49|    with _kill_lock:
50|        today = _today_ist()
51|        if _kill_state["tripped_on"] and _kill_state["tripped_on"] != today:
52|            _kill_state.update({
53|                "tripped_on": None, "reason": "", "day_pnl": 0.0, "closed_ids": [],
54|            })
55|
56|
57|def is_kill_switch_tripped() -> bool:
58|    _reset_if_new_day()
59|    with _kill_lock:
60|        return _kill_state["tripped_on"] == _today_ist()
61|
62|
63|def kill_switch_status() -> dict:
64|    _reset_if_new_day()
65|    with _kill_lock:
66|        return {
67|            "tripped": _kill_state["tripped_on"] == _today_ist(),
68|            "tripped_on": _kill_state["tripped_on"].isoformat() if _kill_state["tripped_on"] else None,
69|            "reason": _kill_state["reason"],
70|            "day_pnl": round(_kill_state["day_pnl"], 2),
71|            "closed_ids": list(_kill_state["closed_ids"]),
72|            "max_daily_loss": getattr(config, "MAX_DAILY_LOSS", -2000),
73|            "max_daily_trades": getattr(config, "MAX_DAILY_TRADES", 5),
74|        }
75|
76|
77|def _get_day_pnl(db: Session) -> Tuple[float, int]:
78|    today = _today_ist().isoformat()
79|    day_pnl = db.query(func.coalesce(func.sum(Trade.pnl), 0.0)) \
80|                .filter(Trade.status == "CLOSED", Trade.trade_date == today) \
81|                .scalar() or 0.0
82|    day_count = db.query(Trade) \
83|                  .filter(Trade.trade_date == today) \
84|                  .count()
85|    return float(day_pnl), int(day_count)
86|
87|
88|def _trip_kill_switch(db: Session, reason: str, day_pnl: float) -> List[int]:
89|    """Force-close every ACTIVE trade and mark the day as blocked."""
90|    closed_ids: List[int] = []
91|    active_trades = db.query(Trade).filter(Trade.status == "ACTIVE").all()
92|    for t in active_trades:
93|        try:
94|            spot = config.state_manager.latest_prices.get("nifty") or t.entry
95|            pnl = (spot - t.entry) if t.direction == "LONG" else (t.entry - spot)
96|            ok, msg = close_paper_trade(db, reason=f"KILL-SWITCH: {reason}", pnl=pnl)
97|            if ok:
98|                closed_ids.append(t.id)
99|                logger.warning(f"🛑 KILL-SWITCH closed trade #{t.id}: {msg}")
100|        except Exception as e:  # pragma: no cover
101|            logger.error(f"KILL-SWITCH failed to close trade #{t.id}: {e}")
102|
103|    with _kill_lock:
104|        _kill_state.update({
105|            "tripped_on": _today_ist(),
106|            "reason": reason,
107|            "day_pnl": day_pnl,
108|            "closed_ids": closed_ids,
109|        })
110|    return closed_ids
111|
112|
113|# ─────────────────────────────────────────────────────────────
114|# Public API: guardrail (call BEFORE opening any new position)
115|# ─────────────────────────────────────────────────────────────
116|def enforce_risk_guardrail(db: Session) -> Tuple[bool, str]:
117|    """
118|    Returns (allow_new_signals, human_message).
119|    Side-effect: trips kill-switch + force-closes open positions if breached.
120|    """
121|    _reset_if_new_day()
122|    if db is None:
123|        return True, "Risk OK (no DB)"
124|
125|    if is_kill_switch_tripped():
126|        st = kill_switch_status()
127|        return False, f"🛑 KILL-SWITCH ACTIVE — {st['reason']} (Day PnL ₹{st['day_pnl']})"
128|
129|    try:
130|        day_pnl, day_count = _get_day_pnl(db)
131|    except Exception as e:  # pragma: no cover
132|        logger.error(f"enforce_risk_guardrail DB read failed: {e}")
133|        return True, f"Risk check skipped: {e}"
134|
135|    max_loss = float(getattr(config, "MAX_DAILY_LOSS", -2000))
136|    max_trades = int(getattr(config, "MAX_DAILY_TRADES", 5))
137|
138|    if day_pnl <= max_loss:
139|        closed = _trip_kill_switch(db, f"Daily loss ≤ ₹{max_loss}", day_pnl)
140|        return False, f"🛑 KILL-SWITCH TRIPPED — Day PnL ₹{day_pnl:.2f} | Closed {len(closed)} open position(s)"
141|
142|    if day_count >= max_trades:
143|        closed = _trip_kill_switch(db, f"Daily trade cap {max_trades} reached", day_pnl)
144|        return False, f"🛑 KILL-SWITCH TRIPPED — {day_count} trades taken today"
145|
146|    return True, f"Risk OK (Day PnL ₹{day_pnl:.2f} | Trades {day_count}/{max_trades})"
147|
148|
149|# ─────────────────────────────────────────────────────────────
150|# Public API: position monitor (call on every price tick)
151|# ─────────────────────────────────────────────────────────────
152|def monitor_open_positions(db: Session, spot: Optional[float] = None) -> dict:
153|    """
154|    Evaluate every ACTIVE trade against live spot. Close on Target / SL hit.
155|    Also enforces the daily kill-switch after each close.
156|    """
157|    if db is None:
158|        return {"action": "skipped", "reason": "No DB"}
159|
160|    if spot is None:
161|        spot = config.state_manager.latest_prices.get("nifty")
162|    if not spot or spot <= 0:
163|        return {"action": "skipped", "reason": "No live spot"}
164|
165|    # Fast-path: block everything if switch already tripped today
166|    allow, msg = enforce_risk_guardrail(db)
167|    if not allow:
168|        return {"action": "blocked", "reason": msg}
169|
170|    active_trades = db.query(Trade).filter(Trade.status == "ACTIVE").all()
171|    if not active_trades:
172|        return {"action": "idle", "reason": "No active positions"}
173|
174|    results = []
175|    for t in active_trades:
176|        pnl = (spot - t.entry) if t.direction == "LONG" else (t.entry - spot)
177|
178|        exit_reason = None
179|        if t.direction == "LONG":
180|            if t.target and spot >= t.target:
181|                exit_reason = "Target Hit ✅"
182|            elif t.sl and spot <= t.sl:
183|                exit_reason = "Stop-Loss Hit ❌"
184|        elif t.direction == "SHORT":
185|            if t.target and spot <= t.target:
186|                exit_reason = "Target Hit ✅"
187|            elif t.sl and spot >= t.sl:
188|                exit_reason = "Stop-Loss Hit ❌"
189|
190|        if exit_reason:
191|            ok, close_msg = close_paper_trade(db, reason=exit_reason, pnl=pnl)
192|            results.append({
193|                "id": t.id, "direction": t.direction, "exit_reason": exit_reason,
194|                "pnl": round(pnl, 2), "ok": ok, "msg": close_msg,
195|            })
196|            # Re-check guardrail immediately after each close
197|            allow, msg = enforce_risk_guardrail(db)
198|            if not allow:
199|                return {"action": "kill_switch_tripped", "reason": msg, "closes": results}
200|        else:
201|            results.append({
202|                "id": t.id, "direction": t.direction, "live_pnl": round(pnl, 2),
203|                "action": "hold",
204|            })
205|
206|    return {"action": "monitored", "positions": results, "kill_switch": kill_switch_status()}
207|
[End of file]
