"""
Backtesting engine for GOAT PRO strategy.
Tests ORB+VWAP+EMA+RSI signal on historical candle data.
"""

import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from logzero import logger
import statistics

import indicators
import config


class BacktestResult:
    """Holds backtesting performance metrics."""
    def __init__(self):
        self.trades = []  # list of {entry, exit, direction, pnl, signal_date}
        self.signals = []  # all signals generated (LONG/SHORT/WAIT)
        self.start_date = None
        self.end_date = None
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.win_rate = 0.0
        self.total_pnl = 0.0
        self.avg_win = 0.0
        self.avg_loss = 0.0
        self.profit_factor = 0.0
        self.max_drawdown = 0.0
        self.sharpe_ratio = 0.0

    def calculate_metrics(self):
        """Calculate all performance metrics."""
        if not self.trades:
            return

        self.total_trades = len(self.trades)
        self.winning_trades = sum(1 for t in self.trades if t["pnl"] > 0)
        self.losing_trades = sum(1 for t in self.trades if t["pnl"] < 0)
        
        if self.total_trades > 0:
            self.win_rate = round((self.winning_trades / self.total_trades) * 100, 2)
        
        self.total_pnl = sum(t["pnl"] for t in self.trades)
        
        wins = [t["pnl"] for t in self.trades if t["pnl"] > 0]
        losses = [t["pnl"] for t in self.trades if t["pnl"] < 0]
        
        self.avg_win = round(statistics.mean(wins), 2) if wins else 0.0
        self.avg_loss = round(statistics.mean(losses), 2) if losses else 0.0
        
        if abs(self.avg_loss) > 0:
            self.profit_factor = round(self.avg_win / abs(self.avg_loss), 2)
        
        # Calculate max drawdown
        cumulative_pnl = 0
        peak = 0
        max_dd = 0
        for t in self.trades:
            cumulative_pnl += t["pnl"]
            if cumulative_pnl > peak:
                peak = cumulative_pnl
            dd = peak - cumulative_pnl
            if dd > max_dd:
                max_dd = dd
        self.max_drawdown = round(max_dd, 2)
        
        # Sharpe ratio (simplified: assume 252 trading days, 0% risk-free rate)
        if len(self.trades) > 1:
            daily_returns = [t["pnl"] for t in self.trades]
            if statistics.stdev(daily_returns) > 0:
                annual_return = sum(daily_returns) * (252 / len(daily_returns))
                annual_volatility = statistics.stdev(daily_returns) * (252 ** 0.5)
                self.sharpe_ratio = round(annual_return / annual_volatility, 2) if annual_volatility > 0 else 0.0

    def to_dict(self):
        """Convert to dictionary for JSON serialization."""
        return {
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.win_rate,
            "total_pnl": self.total_pnl,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "profit_factor": self.profit_factor,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "start_date": str(self.start_date),
            "end_date": str(self.end_date),
            "trades": self.trades,
        }


def compute_signal_on_candles(candles: List[Dict]) -> Dict:
    """Same as strategy.compute_real_signal() but for backtest."""
    if not candles or len(candles) < 16:
        return {"signal": "WAIT", "confidence": 0}

    closes = [c["close"] for c in candles]
    current_price = closes[-1]
    
    # ORB
    orb_candles = [c for c in candles if "09:15" <= c["time"][11:16] <= "09:30"]
    if orb_candles:
        orb_high = max(c["high"] for c in orb_candles)
        orb_low = min(c["low"] for c in orb_candles)
    else:
        orb_high, orb_low = None, None
    
    ema9 = indicators.calculate_ema(closes, 9)
    ema21 = indicators.calculate_ema(closes, 21)
    rsi = indicators.calculate_rsi(closes)
    vwap = indicators.calculate_vwap_approx(candles)

    if orb_high is None or ema9 is None or ema21 is None or rsi is None or vwap is None:
        return {"signal": "WAIT", "confidence": 0}

    checklist_long = {
        "orb_breakout": current_price > orb_high,
        "above_vwap": current_price > vwap,
        "ema_bullish": ema9 > ema21,
        "rsi_not_overbought": rsi < 70,
    }
    checklist_short = {
        "orb_breakdown": current_price < orb_low,
        "below_vwap": current_price < vwap,
        "ema_bearish": ema9 < ema21,
        "rsi_not_oversold": rsi > 30,
    }
    
    long_score = sum(checklist_long.values())
    short_score = sum(checklist_short.values())

    if long_score >= 3 and long_score > short_score:
        return {"signal": "LONG", "confidence": long_score}
    elif short_score >= 3 and short_score > long_score:
        return {"signal": "SHORT", "confidence": short_score}
    else:
        return {"signal": "WAIT", "confidence": max(long_score, short_score)}


def backtest_strategy(candles_by_day: Dict[str, List[Dict]]) -> BacktestResult:
    """
    Backtest strategy on historical candle data.
    
    Args:
        candles_by_day: dict of {date_str: [candles]}
    
    Returns:
        BacktestResult with performance metrics
    """
    result = BacktestResult()
    
    if not candles_by_day:
        return result
    
    sorted_dates = sorted(candles_by_day.keys())
    result.start_date = sorted_dates[0]
    result.end_date = sorted_dates[-1]
    
    active_position = None
    
    for date in sorted_dates:
        day_candles = candles_by_day[date]
        
        # Generate signal at end of each 5-min bar (or daily close)
        for i in range(len(day_candles)):
            current_candles = day_candles[:i+1]
            sig = compute_signal_on_candles(current_candles)
            
            result.signals.append({
                "date": date,
                "candle_idx": i,
                "signal": sig["signal"],
                "confidence": sig["confidence"],
            })
            
            # Entry logic: go long/short only on 3+ confidence
            if sig["signal"] == "LONG" and sig["confidence"] >= 3 and active_position is None:
                active_position = {
                    "direction": "LONG",
                    "entry": current_candles[-1]["close"],
                    "entry_date": date,
                }
            elif sig["signal"] == "SHORT" and sig["confidence"] >= 3 and active_position is None:
                active_position = {
                    "direction": "SHORT",
                    "entry": current_candles[-1]["close"],
                    "entry_date": date,
                }
            
            # Exit logic: opposite signal or max hold time
            if active_position is not None:
                exit_price = current_candles[-1]["close"]
                
                should_exit = False
                if active_position["direction"] == "LONG" and sig["signal"] == "SHORT":
                    should_exit = True
                elif active_position["direction"] == "SHORT" and sig["signal"] == "LONG":
                    should_exit = True
                
                if should_exit:
                    pnl = 0
                    if active_position["direction"] == "LONG":
                        pnl = exit_price - active_position["entry"]
                    else:
                        pnl = active_position["entry"] - exit_price
                    
                    result.trades.append({
                        "direction": active_position["direction"],
                        "entry": active_position["entry"],
                        "exit": exit_price,
                        "pnl": round(pnl, 2),
                        "entry_date": active_position["entry_date"],
                        "exit_date": date,
                    })
                    active_position = None
    
    result.calculate_metrics()
    return result


def save_backtest_report(result: BacktestResult, filename: str = "backtest_report.json"):
    """Save backtest results to file."""
    with open(filename, "w") as f:
        json.dump(result.to_dict(), f, indent=2)
    logger.info(f"Backtest report saved to {filename}")


def print_backtest_summary(result: BacktestResult):
    """Print summary to console."""
    print("\n" + "="*60)
    print("BACKTEST SUMMARY")
    print("="*60)
    print(f"Date Range: {result.start_date} to {result.end_date}")
    print(f"Total Trades: {result.total_trades}")
    print(f"Win Rate: {result.win_rate}% ({result.winning_trades}W / {result.losing_trades}L)")
    print(f"Total PnL: ₹{result.total_pnl}")
    print(f"Avg Win: ₹{result.avg_win} | Avg Loss: ₹{result.avg_loss}")
    print(f"Profit Factor: {result.profit_factor}")
    print(f"Max Drawdown: ₹{result.max_drawdown}")
    print(f"Sharpe Ratio: {result.sharpe_ratio}")
    print("="*60 + "\n")
