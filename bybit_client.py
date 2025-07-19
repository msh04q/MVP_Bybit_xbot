import os
from pybit.unified_trading import HTTP
from dotenv import load_dotenv
import logging

load_dotenv()

api_key = os.getenv("BYBIT_API_KEY")
api_secret = os.getenv("BYBIT_API_SECRET")

# Используем основной рынок (не тестнет)
session = HTTP(
    api_key=api_key,
    api_secret=api_secret,
    testnet=False
)

def get_spot_tickers():
    """
    Получаем список активных спотовых пар с USDT,
    фильтруем по объему торгов за 24 часа.
    Возвращаем только символы с обычными символами.
    """
    try:
        response = session.get_tickers(category="spot")
        tickers = response.get("result", {}).get("list", [])
        
        # Фильтрация: только USDT пары с объемом > 1000 USDT
        symbols = []
        for item in tickers:
            symbol = item.get("symbol", "")
            if "USDT" in symbol and float(item.get("turnover24h", 0)) > 1000:
                # Фильтруем символы с необычными символами
                if all(ord(c) < 128 for c in symbol):
                    symbols.append(symbol)
                else:
                    logging.warning(f"Skipping symbol with non-ASCII characters: {symbol}")
        
        return symbols
        
    except Exception as e:
        logging.error(f"Error getting tickers: {str(e)}")
        # Возвращаем пустой список в случае ошибки
        return []