import os
import time
import requests


BINANCE_URL = "https://api.binance.com/api/v3/ticker/price"

SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
INTERVAL = float(os.getenv("TICKER_INTERVAL", "1"))


def get_ticker_price():
    response = requests.get(
        BINANCE_URL,
        params={"symbol": SYMBOL.upper()},
        timeout=10
    )

    response.raise_for_status()

    return float(response.json()["price"])


while True:
    try:
        price = get_ticker_price()

        print(
            f"{SYMBOL} | "
            f"PRICE: {price}"
        )

    except Exception as e:
        print("ERROR:", e)

    time.sleep(INTERVAL)
