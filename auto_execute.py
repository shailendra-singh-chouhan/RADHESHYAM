"""
auto_execute.py — GOAT PRO Live Position Monitor + Kill-Switch Guardrail
"""
import threading
from datetime import date
from typing import Optional, Tuple, List

from sqlalchemy.orm import Session
from sqlalchemy import func

import config
from models import Trade
from trading import close_paper_trade

_kill_lock = threading.Lock()
_kill_state = {
    "tripped_on": None,
    "reason": "",
    "day_pnl": 0.0,
    "closed_ids": [],
}

def _today_ist() -> date:
    return config.get_ist_now().date()

def _reset_if_new_day() -> None:
    with _kill_lock:
        today = _today_ist()
        if _kill_state["tripped_on"] and _kill_state["tripped_on"] != today:
            _kill_state.update({
                "tripped_on": None, "reason": "", "day_pnl": 0.0, "closed_ids": [],
            })

def is_kill_switch_tripped() -> bool:
    _reset_if_new_day()
    with _kill_lock:
        return _kill_state["tripped_on"] == _today_ist()

def kill_switch_status() -> dict:
    _reset_if_new_day()
    with _kill_lock:
        return {
            "tripped": _kill_state["tripped_on"] == _today_ist(),
            "reason": _kill_state["reason"],
            "day_pnl": round(_kill_state["day_pnl"], 2),
        }

def enforce_risk_guardrail(db: Session) -> Tuple[bool, str]:
    _reset_if_new_day()
    if is_kill_switch_tripped():
        return False, f"KILL-SWITCH ACTIVE: {_kill_state['reason']}"
    
    today_str = _today_ist().isoformat()
    day_pnl = db.query(func.coalesce(func.sum(Trade.pnl), 0.0)).filter(Trade.trade_date == today_str).scalar() or 0.0
    day_count = db.query(Trade).filter(Trade.trade_date == today_str).count()
    
    max_loss = getattr(config, "MAX_DAILY_LOSS", -2000)
    max_trades = getattr(config, "MAX_DAILY_TRADES", 5)
    
    if day_pnl <= max_loss:
        with _kill_lock:
            _kill_state.update({"tripped_on": _today_ist(), "reason": f"Loss limit ₹{max_loss} hit", "day_pnl": day_pnl})
        return False, f"KILL-SWITCH TRIPPED: Loss limit hit"
        
    if day_count >= max_trades:
        with _kill_lock:
            _kill_state.update({"tripped_on": _today_ist(), "reason": f"Trade cap {max_trades} hit", "day_pnl": day_pnl})
        return False, f"KILL-SWITCH TRIPPED: Trade cap hit"
        
    return True, "Risk OK"
