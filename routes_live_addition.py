

# ═══════════════════════════════════════════════════════════════════
# LIVE DATA ENDPOINTS — Real-time market data
# ═══════════════════════════════════════════════════════════════════

from live_data_fetcher import (
    fetch_nifty_spot, fetch_banknifty_spot, fetch_sensex_spot,
    fetch_nse_option_chain, fetch_india_vix, fetch_global_markets,
    fetch_fii_dii, fetch_stock_price, fetch_all_live_data,
)


@router.get("/api/live/spot")
def get_live_spot():
    """Get live spot prices for NIFTY, BANKNIFTY, SENSEX"""
    return {
        "nifty": fetch_nifty_spot(),
        "banknifty": fetch_banknifty_spot(),
        "sensex": fetch_sensex_spot(),
        "time": datetime.now().isoformat(),
    }


@router.get("/api/live/oi")
def get_live_oi(index: str = "NIFTY"):
    """Get live option chain OI data"""
    return fetch_nse_option_chain(index)


@router.get("/api/live/vix")
def get_live_vix():
    """Get live India VIX"""
    return fetch_india_vix()


@router.get("/api/live/global")
def get_live_global():
    """Get live global market indices"""
    return fetch_global_markets()


@router.get("/api/live/fii-dii")
def get_live_fii_dii():
    """Get live FII/DII trading data"""
    return fetch_fii_dii()


@router.get("/api/live/stock/{stock_name}")
def get_live_stock(stock_name: str):
    """Get live price for a stock (HDFC, SBI, PNB, YES, INFY)"""
    return fetch_stock_price(stock_name.upper())


@router.get("/api/live/all")
def get_all_live():
    """Get complete live market snapshot"""
    return fetch_all_live_data()
