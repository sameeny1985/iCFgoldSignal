import os
import threading
import time

import requests
from flask import Flask, jsonify

app = Flask(__name__)

SYMBOL = os.getenv("SYMBOL", "BTCUSDT").upper()
TICKER_INTERVAL = float(os.getenv("TICKER_INTERVAL", "1"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BINANCE_URL = "https://api.binance.com/api/v3/ticker/price"

last_price = None
last_update = None


@app.get("/")
def home():
    return jsonify({
        "status": "running",
        "symbol": SYMBOL,
        "price": last_price,
        "last_update": last_update
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "ok"
    })


@app.get("/price")
def price():
    return jsonify({
        "symbol": SYMBOL,
        "price": last_price,
        "last_update": last_update
    })


def get_ticker():
    response = requests.get(
        BINANCE_URL,
        params={"symbol": SYMBOL},
        timeout=10
    )

    response.raise_for_status()

    data = response.json()

    return float(data["price"])


def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials are missing")
        return

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        },
        timeout=10
    )


def ticker_loop():
    global last_price
    global last_update

    while True:
        try:
            price = get_ticker()

            last_price = price
            last_update = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.gmtime()
            )

            print(
                f"{SYMBOL} TICKER = {price}"
            )

        except Exception as e:
            print(
                f"Ticker error: {repr(e)}"
            )

        time.sleep(TICKER_INTERVAL)


if __name__ == "__main__":
    ticker_thread = threading.Thread(
        target=ticker_loop,
        daemon=True
    )

    ticker_thread.start()

    port = int(
        os.getenv("PORT", "10000")
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
