class AngelClient:
    def __init__(self):
        pass

    def fetch_todays_candles(self, symbol, interval="5minute"):
        return [] 

    def get_price(self, symbol):
        return 0.0
    
    def get_ltp(self, symbol):
        """Fixed: added get_ltp to prevent import error"""
        return 0.0

angel_client = AngelClient()
