import os
import requests


BINANCE_URL = "https://api.binance.com/api/v3/ticker/price"

SYMBOL = os.getenv("SYMBOL", "BTCUSDT")


def get_ticker_price(symbol=None):
    symbol = (symbol or SYMBOL).upper()

    response = requests.get(
        BINANCE_URL,
        params={
            "symbol": symbol
        },
        timeout=10
    )

    response.raise_for_status()

    data = response.json()

    return {
        "symbol": data["symbol"],
        "price": float(data["price"])
    }


if __name__ == "__main__":
    ticker = get_ticker_price()

    print(
        f"{ticker['symbol']} = "
        f"{ticker['price']}"
    )
