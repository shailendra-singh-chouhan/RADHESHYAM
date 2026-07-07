import config
import trading
from logzero import logger

def process_and_auto_execute():
    """
    Reads the latest signal, checks if a trade is already active, 
    and automatically executes a new trade if conditions are met.
    """
    try:
        # 1. Get Live Data
        signal_data = config.signal_data
        current_signal = signal_data.get("signal", "WAIT")
        spot_price = config.latest_prices.get("nifty")
        
        if current_signal == "WAIT" or spot_price is None:
            return

        # 2. Setup Active Trade variable if it doesn't exist
        if not hasattr(config, 'active_trade'):
            config.active_trade = None

        # 3. Manage Existing Active Trade (Exit Logic Placeholder)
        if config.active_trade is not None and config.active_trade.get("status") == "OPEN":
            # Here we will add Target and SL hit logic later
            return

        # 4. No active trade? Execute new trade based on signal!
        if current_signal in ["LONG", "SHORT"]:
            logger.info(f"🔔 NEW {current_signal} SIGNAL CONFIRMED | Spot: {spot_price}")
            
            # Select Strike and Generate Trade Details
            new_trade = trading.execute_trade(current_signal, spot_price)
            
            if new_trade:
                # Save trade to config so Dashboard can display it
                config.active_trade = new_trade
                logger.info(f"✅ TRADE PLACED SUCESSFULLY: {new_trade['contract']['symbol']} | Entry: {new_trade['entry']}")
                
    except Exception as e:
        logger.error(f"Auto-Execute Error: {e}")
