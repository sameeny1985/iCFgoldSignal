import os
import time
import traceback

from market_data import fetch_closed_candles
from strategy import ATR3Strategy, StrategyConfig
from telegram import send_telegram, format_signal


SYMBOL = os.getenv(
    "SYMBOL",
    "BTCUSDT"
)

POLL_SECONDS = int(
    os.getenv(
        "POLL_SECONDS",
        "5"
    )
)


strategy = ATR3Strategy(
    StrategyConfig()
)


last_processed_candle = None


def main():
    global last_processed_candle

    print(
        f"ATR3 started | "
        f"symbol={SYMBOL} | "
        f"interval=1m"
    )

    while True:
        try:
            df = fetch_closed_candles()

            if len(df) < 100:
                print("Waiting for enough candles...")
                time.sleep(POLL_SECONDS)
                continue

            latest_candle = df.iloc[-1]
            candle_time = latest_candle["time"]

            # فقط یک بار برای هر کندل سیگنال بررسی شود.
            if (
                last_processed_candle is not None
                and candle_time == last_processed_candle
            ):
                time.sleep(POLL_SECONDS)
                continue

            last_processed_candle = candle_time

            signals = strategy.calculate(df)

            if signals:
                print(
                    f"{candle_time} -> "
                    f"{signals}"
                )

                for signal in signals:
                    message = format_signal(
                        signal,
                        SYMBOL,
                        latest_candle
                    )

                    send_telegram(message)

                    print(
                        f"Telegram sent: {signal}"
                    )

            time.sleep(POLL_SECONDS)

        except Exception as exc:
            print(
                "ERROR:",
                repr(exc)
            )

            traceback.print_exc()

            time.sleep(
                max(POLL_SECONDS, 10)
            )


if __name__ == "__main__":
    main()
