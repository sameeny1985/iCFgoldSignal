import os
import time

import pandas as pd
import requests


DATA_URL = os.getenv(
    "DATA_URL",
    "https://api.binance.com/api/v3/klines"
)

SYMBOL = os.getenv(
    "SYMBOL",
    "BTCUSDT"
)

INTERVAL = os.getenv(
    "INTERVAL",
    "1m"
)

LIMIT = int(
    os.getenv(
        "CANDLE_LIMIT",
        "200"
    )
)


def fetch_klines():
    params = {
        "symbol": SYMBOL.upper(),
        "interval": INTERVAL,
        "limit": LIMIT,
    }

    response = requests.get(
        DATA_URL,
        params=params,
        timeout=15
    )

    response.raise_for_status()

    data = response.json()

    if not data:
        raise RuntimeError("No candle data received")

    rows = []

    for x in data:
        rows.append({
            "time": pd.to_datetime(
                int(x[0]),
                unit="ms",
                utc=True
            ),
            "open": float(x[1]),
            "high": float(x[2]),
            "low": float(x[3]),
            "close": float(x[4]),
            "volume": float(x[5]),
        })

    df = pd.DataFrame(rows)

    df = df.sort_values("time")
    df = df.drop_duplicates("time")
    df = df.reset_index(drop=True)

    return df


def fetch_closed_candles():
    """
    کندل در حال تشکیل را حذف می‌کنیم.

    برای جلوگیری از اختلاف سیگنال ناشی از تغییرات
    داخل کندل M1.
    """

    df = fetch_klines()

    if len(df) < 3:
        return df

    # آخرین کندل Binance ممکن است هنوز باز باشد.
    # آن را حذف می‌کنیم.
    return df.iloc[:-1].copy()


if __name__ == "__main__":
    df = fetch_closed_candles()

    print(df.tail())
