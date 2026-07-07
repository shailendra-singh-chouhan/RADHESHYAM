"""
Auto-execute module: Automatically execute trades based on generated signals.
Integrates signal generation with paper trading engine.
"""

from typing import Optional, Dict, Tuple
from logzero import logger
from datetime import datetime

import config
import strategy
import trading
from database import SessionLocal


class AutoTradeExecutor:
    """Handles automatic trade execution based on signals."""
    
    def __init__(self):
        self.last_signal = None
        self.last_signal_time = None
        self.min_signal_interval_sec = 60  # Don't execute the same signal twice within 60 sec
        self.active_position = None
    
    def should_execute(self, signal: Dict) -> bool:
        """
        Determine if a signal should trigger a trade.
        Rules:
        - Signal must be LONG or SHORT (not WAIT)
        - Confidence must be >= 3
        - Don't re-execute the same signal within min_signal_interval_sec
        - No active position (one trade at a time)
        """
        if signal.get("signal") == "WAIT":
            return False
        
        if signal.get("confidence", 0) < 3:
            return False
        
        if self.active_position is not None:
            return False
        
        now = datetime.utcnow()
        if self.last_signal is not None:
            elapsed = (now - self.last_signal_time).total_seconds()
            if elapsed < self.min_signal_interval_sec and self.last_signal == signal.get("signal"):
                return False
        
        return True
    
    def execute_signal(self, signal: Dict, db_session) -> Tuple[bool, str]:
        """
        Execute a signal as a paper trade.
        
        Args:
            signal: Signal dict with 'signal', 'confidence', 'note'
            db_session: Database session
        
        Returns:
            (success: bool, message: str)
        """
        if not self.should_execute(signal):
            return False, "Signal not executable (same signal, active position, or low confidence)"
        
        try:
            sig_type = signal.get("signal")
            confidence = signal.get("confidence", 0)
            note = signal.get("note", "")
            
            logger.info(f"AUTO-EXECUTE: {sig_type} signal (confidence {confidence}/4) - {note}")
            
            # Call the paper trading engine
            success, message = trading.open_paper_trade(db_session, direction=sig_type)
            
            if success:
                self.last_signal = sig_type
                self.last_signal_time = datetime.utcnow()
                self.active_position = sig_type
                logger.info(f"Trade executed: {message}")
            else:
                logger.warning(f"Trade execution failed: {message}")
            
            return success, message
        
        except Exception as e:
            logger.error(f"Auto-execute error: {e}")
            return False, f"Exception: {str(e)}"
    
    def check_exit_signal(self, signal: Dict, db_session) -> Tuple[bool, str]:
        """
        Check if current signal indicates exit condition and close position.
        """
        if self.active_position is None:
            return False, "No active position"
        
        # Exit if opposite signal detected
        if self.active_position == "LONG" and signal.get("signal") == "SHORT":
            logger.info("EXIT SIGNAL: LONG position closed by SHORT signal")
            return trading.close_paper_trade(db_session)
        
        elif self.active_position == "SHORT" and signal.get("signal") == "LONG":
            logger.info("EXIT SIGNAL: SHORT position closed by LONG signal")
            return trading.close_paper_trade(db_session)
        
        return False, "No exit signal"
    
    def process_signal(self, signal: Dict, db_session) -> Tuple[bool, str]:
        """
        Process a generated signal: try exit, then try entry.
        """
        # First, check if we should exit
        exit_success, exit_msg = self.check_exit_signal(signal, db_session)
        if exit_success:
            self.active_position = None
            return True, f"Position closed. {exit_msg}"
        
        # Then, try to execute entry
        entry_success, entry_msg = self.execute_signal(signal, db_session)
        if entry_success:
            return True, entry_msg
        
        return False, entry_msg or exit_msg


# Global auto-executor instance
auto_executor = AutoTradeExecutor()


def process_and_auto_execute() -> Dict:
    """
    Main function: Get latest signal and auto-execute if conditions met.
    Called periodically (every 5 minutes from strategy.indicator_poller).
    """
    try:
        signal = config.signal_data
        
        if not signal or signal.get("signal") == "WAIT":
            return {"status": "waiting", "message": "No actionable signal"}
        
        db_session = SessionLocal()
        try:
            success, message = auto_executor.process_signal(signal, db_session)
            return {
                "status": "executed" if success else "skipped",
                "message": message,
                "signal": signal.get("signal"),
                "confidence": signal.get("confidence"),
            }
        finally:
            db_session.close()
    
    except Exception as e:
        logger.error(f"Auto-execute error: {e}")
        return {"status": "error", "message": str(e)}
