"""
Signal logging for real-time strategy analysis.
Tracks all generated signals (LONG/SHORT/WAIT) with metadata.
"""

import json
from datetime import datetime
from typing import List, Dict
from logzero import logger


class SignalLog:
    """Logs all generated signals for later analysis."""
    
    def __init__(self, filename: str = "signal_log.jsonl"):
        self.filename = filename
        self.signals = []
    
    def log_signal(self, signal: str, confidence: int, orb_high: float, orb_low: float,
                   ema9: float, ema21: float, rsi: float, vwap: float,
                   current_price: float, checklist: Dict[str, bool]):
        """
        Log a single signal generation.
        
        Args:
            signal: "LONG", "SHORT", or "WAIT"
            confidence: 0-4 (number of checks passed)
            orb_high, orb_low: Opening range bounds
            ema9, ema21: Exponential moving averages
            rsi: Relative strength index (0-100)
            vwap: Volume-weighted average price
            current_price: Current market price
            checklist: Dict of condition names → True/False
        """
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "signal": signal,
            "confidence": confidence,
            "price": current_price,
            "orb_high": orb_high,
            "orb_low": orb_low,
            "ema9": ema9,
            "ema21": ema21,
            "rsi": rsi,
            "vwap": vwap,
            "checklist": checklist,
        }
        
        self.signals.append(entry)
        
        # Append to file (JSONL format)
        try:
            with open(self.filename, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"Failed to write signal log: {e}")
    
    def load_all(self) -> List[Dict]:
        """Load all signals from log file."""
        signals = []
        try:
            with open(self.filename, "r") as f:
                for line in f:
                    if line.strip():
                        signals.append(json.loads(line))
        except FileNotFoundError:
            pass
        return signals
    
    def get_recent_signals(self, n: int = 50) -> List[Dict]:
        """Get last N signals."""
        all_signals = self.load_all()
        return all_signals[-n:] if all_signals else []
    
    def get_signals_by_type(self, signal_type: str) -> List[Dict]:
        """Get all signals of a specific type (LONG/SHORT/WAIT)."""
        all_signals = self.load_all()
        return [s for s in all_signals if s["signal"] == signal_type]
    
    def summary_stats(self) -> Dict:
        """Calculate summary statistics from all logged signals."""
        all_signals = self.load_all()
        
        if not all_signals:
            return {"total_signals": 0}
        
        long_count = sum(1 for s in all_signals if s["signal"] == "LONG")
        short_count = sum(1 for s in all_signals if s["signal"] == "SHORT")
        wait_count = sum(1 for s in all_signals if s["signal"] == "WAIT")
        
        avg_confidence_on_signal = {
            "LONG": sum(s["confidence"] for s in all_signals if s["signal"] == "LONG") / long_count if long_count > 0 else 0,
            "SHORT": sum(s["confidence"] for s in all_signals if s["signal"] == "SHORT") / short_count if short_count > 0 else 0,
        }
        
        return {
            "total_signals": len(all_signals),
            "long_signals": long_count,
            "short_signals": short_count,
            "wait_signals": wait_count,
            "long_rate_pct": round((long_count / len(all_signals)) * 100, 2),
            "short_rate_pct": round((short_count / len(all_signals)) * 100, 2),
            "avg_confidence_long": round(avg_confidence_on_signal["LONG"], 2),
            "avg_confidence_short": round(avg_confidence_on_signal["SHORT"], 2),
        }
