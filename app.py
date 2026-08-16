import os
import time
import threading
import logging
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("ATR3")


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# CONFIG
# ============================================================

SYMBOL = os.getenv("SYMBOL", "BTCUSDT").upper()

TIMEFRAME = "1m"

POLL_SECONDS = float(
    os.getenv("POLL_SECONDS", "5")
)

COOLDOWN_MINUTES = int(
    os.getenv("COOLDOWN_MINUTES", "3")
)


# ============================================================
# STRATEGY SETTINGS
# ============================================================

RSI_PERIOD = int(
    os.getenv("RSI_PERIOD", "8")
)

RSI_OVERBUY = float(
    os.getenv("RSI_OVERBUY", "70")
)

RSI_OVERSELL = float(
    os.getenv("RSI_OVERSELL", "30")
)

STO_OVERBUY_CRS = float(
    os.getenv("STO_OVERBUY_CRS", "70")
)

STO_OVERSELL_CRS = float(
    os.getenv("STO_OVERSELL_CRS", "30")
)

STO_OVERBUY_EXT = float(
    os.getenv("STO_OVERBUY_EXT", "80")
)

STO_OVERSELL_EXT = float(
    os.getenv("STO_OVERSELL_EXT", "20")
)

BB_PERIOD = int(
    os.getenv("BB_PERIOD", "20")
)

BB_DEV = float(
    os.getenv("BB_DEV", "2.0")
)

MA1_PERIOD = int(
    os.getenv("MA1_PERIOD", "10")
)

MA2_PERIOD = int(
    os.getenv("MA2_PERIOD", "21")
)


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)


# ============================================================
# BINANCE
# ============================================================

BINANCE_KLINES_URL = (
    "https://api.binance.com/api/v3/klines"
)


# ============================================================
# GLOBAL STATE
# ============================================================

last_signal_time = 0.0

last_price = None

last_update = None

last_processed_candle = None

last_signal = None

last_diagnostics = {}

state_lock = threading.Lock()


# ============================================================
# TIME
# ============================================================

def now_utc_string():

    return datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


# ============================================================
# BINANCE KLINES
# ============================================================

def get_klines():

    response = requests.get(
        BINANCE_KLINES_URL,
        params={
            "symbol": SYMBOL,
            "interval": TIMEFRAME,
            "limit": 100
        },
        timeout=10
    )

    response.raise_for_status()

    raw = response.json()

    candles = []

    for row in raw:

        candles.append({
            "time_ms": int(row[0]),

            "time": datetime.fromtimestamp(
                int(row[0]) / 1000,
                tz=timezone.utc
            ),

            "open": float(row[1]),

            "high": float(row[2]),

            "low": float(row[3]),

            "close": float(row[4]),

            "volume": float(row[5])
        })

    return candles


# ============================================================
# SMA
# ============================================================

def sma(values, period):

    result = [None] * len(values)

    if period <= 0:
        return result

    running_sum = 0.0

    for i, value in enumerate(values):

        running_sum += value

        if i >= period:
            running_sum -= values[i - period]

        if i >= period - 1:

            result[i] = (
                running_sum / period
            )

    return result


# ============================================================
# STANDARD DEVIATION
# ============================================================

def rolling_std(values, period):

    result = [None] * len(values)

    if period <= 0:
        return result

    for i in range(
        period - 1,
        len(values)
    ):

        window = values[
            i - period + 1:i + 1
        ]

        mean = (
            sum(window) / period
        )

        variance = (
            sum(
                (x - mean) ** 2
                for x in window
            )
            / period
        )

        result[i] = (
            variance ** 0.5
        )

    return result


# ============================================================
# BOLLINGER
# ============================================================

def bollinger_bands(
    closes,
    period,
    deviation
):

    middle = sma(
        closes,
        period
    )

    std = rolling_std(
        closes,
        period
    )

    upper = [None] * len(closes)

    lower = [None] * len(closes)

    for i in range(len(closes)):

        if (
            middle[i] is not None
            and std[i] is not None
        ):

            upper[i] = (
                middle[i]
                + deviation * std[i]
            )

            lower[i] = (
                middle[i]
                - deviation * std[i]
            )

    return (
        middle,
        upper,
        lower
    )


# ============================================================
# STOCHASTIC 5,3,3
# ============================================================

def stochastic_5_3_3(
    highs,
    lows,
    closes
):

    length = len(closes)

    raw_k = [None] * length

    # --------------------------------------------------------
    # RAW %K
    # --------------------------------------------------------

    for i in range(
        4,
        length
    ):

        lowest = min(
            lows[i - 4:i + 1]
        )

        highest = max(
            highs[i - 4:i + 1]
        )

        denominator = (
            highest - lowest
        )

        if denominator == 0:

            raw_k[i] = 50.0

        else:

            raw_k[i] = (
                100.0
                * (
                    closes[i]
                    - lowest
                )
                / denominator
            )

    # --------------------------------------------------------
    # SLOWING 3
    # --------------------------------------------------------

    main = [None] * length

    for i in range(
        6,
        length
    ):

        values = raw_k[
            i - 2:i + 1
        ]

        if all(
            value is not None
            for value in values
        ):

            main[i] = (
                sum(values) / 3.0
            )

    # --------------------------------------------------------
    # SIGNAL 3
    # --------------------------------------------------------

    signal = [None] * length

    for i in range(
        8,
        length
    ):

        values = main[
            i - 2:i + 1
        ]

        if all(
            value is not None
            for value in values
        ):

            signal[i] = (
                sum(values) / 3.0
            )

    return (
        main,
        signal
    )


# ============================================================
# RSI WILDER
# ============================================================

def rsi_wilder(
    closes,
    period
):

    result = [None] * len(closes)

    if len(closes) <= period:
        return result

    gains = [0.0] * len(closes)

    losses = [0.0] * len(closes)

    for i in range(
        1,
        len(closes)
    ):

        change = (
            closes[i]
            - closes[i - 1]
        )

        if change > 0:

            gains[i] = change

        elif change < 0:

            losses[i] = -change

    avg_gain = (
        sum(
            gains[1:period + 1]
        )
        / period
    )

    avg_loss = (
        sum(
            losses[1:period + 1]
        )
        / period
    )

    if avg_loss == 0:

        result[period] = 100.0

    else:

        rs = (
            avg_gain
            / avg_loss
        )

        result[period] = (
            100.0
            - (
                100.0
                / (1.0 + rs)
            )
        )

    for i in range(
        period + 1,
        len(closes)
    ):

        avg_gain = (
            (
                avg_gain
                * (period - 1)
            )
            + gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss
                * (period - 1)
            )
            + losses[i]
        ) / period

        if avg_loss == 0:

            result[i] = 100.0

        else:

            rs = (
                avg_gain
                / avg_loss
            )

            result[i] = (
                100.0
                - (
                    100.0
                    / (1.0 + rs)
                )
            )

    return result


# ============================================================
# SIGNAL CALCULATION
# ============================================================

def calculate_signal(
    candles
):

    # --------------------------------------------------------
    # We need enough history
    # --------------------------------------------------------

    if len(candles) < 40:

        return None

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # candles[-1] = CURRENT / FORMING candle
    #
    # candles[-2] = LAST CLOSED candle
    #
    # We completely ignore the forming candle.
    # --------------------------------------------------------

    closed = candles[:-1]

    if len(closed) < 30:

        return None

    opens = [
        c["open"]
        for c in closed
    ]

    highs = [
        c["high"]
        for c in closed
    ]

    lows = [
        c["low"]
        for c in closed
    ]

    closes = [
        c["close"]
        for c in closed
    ]

    # ========================================================
    # STOCHASTIC
    # ========================================================

    stoch_main_values, stoch_signal_values = (
        stochastic_5_3_3(
            highs,
            lows,
            closes
        )
    )

    current_index = len(closed) - 1

    previous_index = (
        current_index - 1
    )

    stoch_main_current = (
        stoch_main_values[
            current_index
        ]
    )

    stoch_main_previous = (
        stoch_main_values[
            previous_index
        ]
    )

    stoch_signal_current = (
        stoch_signal_values[
            current_index
        ]
    )

    stoch_signal_previous = (
        stoch_signal_values[
            previous_index
        ]
    )

    if any(
        value is None
        for value in [
            stoch_main_current,
            stoch_main_previous,
            stoch_signal_current,
            stoch_signal_previous
        ]
    ):

        return None

    # ========================================================
    # BOLLINGER
    # ========================================================

    (
        bb_middle_values,
        bb_upper_values,
        bb_lower_values
    ) = bollinger_bands(
        closes,
        BB_PERIOD,
        BB_DEV
    )

    bb_upper = (
        bb_upper_values[
            current_index
        ]
    )

    bb_lower = (
        bb_lower_values[
            current_index
        ]
    )

    if (
        bb_upper is None
        or bb_lower is None
    ):

        return None

    # ========================================================
    # MOVING AVERAGES
    # ========================================================

    ma_fast_values = sma(
        closes,
        MA1_PERIOD
    )

    ma_slow_values = sma(
        closes,
        MA2_PERIOD
    )

    ma_fast = (
        ma_fast_values[
            current_index
        ]
    )

    ma_slow = (
        ma_slow_values[
            current_index
        ]
    )

    if (
        ma_fast is None
        or ma_slow is None
    ):

        return None

    # ========================================================
    # RSI
    # ========================================================

    rsi_values = rsi_wilder(
        closes,
        RSI_PERIOD
    )

    rsi = (
        rsi_values[
            current_index
        ]
    )

    if rsi is None:

        return None

    # ========================================================
    # CURRENT CLOSED CANDLE
    # ========================================================

    candle = closed[
        current_index
    ]

    # ========================================================
    # STOCHASTIC CROSS
    # ========================================================

    bullish_cross = (
        stoch_main_previous
        <= stoch_signal_previous
        and
        stoch_main_current
        > stoch_signal_current
    )

    bearish_cross = (
        stoch_main_previous
        >= stoch_signal_previous
        and
        stoch_main_current
        < stoch_signal_current
    )

    # ========================================================
    # BUY CONDITIONS
    # ========================================================

    buy_cross = (
        bullish_cross
        and
        stoch_main_current
        <= STO_OVERSELL_CRS
    )

    buy_bb = (
        candle["low"]
        <= bb_lower
    )

    buy_ma = (
        ma_fast
        > ma_slow
    )

    buy_rsi = (
        rsi
        < RSI_OVERBUY
    )

    buy_stoch_final = (
        stoch_main_current
        < STO_OVERSELL_EXT
    )

    buy_signal = (
        buy_cross
        and
        buy_bb
        and
        buy_ma
        and
        buy_rsi
        and
        buy_stoch_final
    )

    # ========================================================
    # SELL CONDITIONS
    # ========================================================

    sell_cross = (
        bearish_cross
        and
        stoch_main_current
        >= STO_OVERBUY_CRS
    )

    sell_bb = (
        candle["high"]
        >= bb_upper
    )

    sell_ma = (
        ma_fast
        < ma_slow
    )

    sell_rsi = (
        rsi
        > RSI_OVERSELL
    )

    sell_stoch_final = (
        stoch_main_current
        > STO_OVERBUY_EXT
    )

    sell_signal = (
        sell_cross
        and
        sell_bb
        and
        sell_ma
        and
        sell_rsi
        and
        sell_stoch_final
    )

    # ========================================================
    # DIAGNOSTICS
    # ========================================================

    diagnostics = {

        "buy_cross": buy_cross,

        "buy_bb": buy_bb,

        "buy_ma": buy_ma,

        "buy_rsi": buy_rsi,

        "buy_stoch_final":
            buy_stoch_final,

        "sell_cross":
            sell_cross,

        "sell_bb":
            sell_bb,

        "sell_ma":
            sell_ma,

        "sell_rsi":
            sell_rsi,

        "sell_stoch_final":
            sell_stoch_final
    }

    executions = []

    if buy_signal:

        executions.append(
            "BUY"
        )

    if sell_signal:

        executions.append(
            "SELL"
        )

    return {

        "executions":
            executions,

        "buy_signal":
            buy_signal,

        "sell_signal":
            sell_signal,

        "diagnostics":
            diagnostics,

        "stoch_main":
            stoch_main_current,

        "stoch_main_previous":
            stoch_main_previous,

        "stoch_signal":
            stoch_signal_current,

        "stoch_signal_previous":
            stoch_signal_previous,

        "bb_lower":
            bb_lower,

        "bb_upper":
            bb_upper,

        "ma_fast":
            ma_fast,

        "ma_slow":
            ma_slow,

        "rsi":
            rsi,

        "price":
            candle["close"],

        "candle_time":
            candle["time"].isoformat()
    }


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    signal_type,
    candle,
    result
):

    if not TELEGRAM_TOKEN:

        log.error(
            "TELEGRAM_TOKEN is empty"
        )

        return False

    if not TELEGRAM_CHAT_ID:

        log.error(
            "TELEGRAM_CHAT_ID is empty"
        )

        return False

    emoji = (
        "🟢"
        if signal_type == "BUY"
        else "🔴"
    )

    message = (
        f"{emoji} ATR3 {signal_type}\n"
        f"Symbol: {SYMBOL}\n"
        f"Price: {candle['close']:.2f}\n"
        f"Stoch: {result['stoch_main']:.2f}\n"
        f"RSI: {result['rsi']:.2f}\n"
        f"MA10: {result['ma_fast']:.2f}\n"
        f"MA21: {result['ma_slow']:.2f}\n"
        f"Candle: {result['candle_time']}\n"
        f"UTC: {now_utc_string()}"
    )

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    try:

        response = requests.post(
            url,
            data={
                "chat_id":
                    TELEGRAM_CHAT_ID,

                "text":
                    message
            },
            timeout=10
        )

        data = response.json()

        if not response.ok:

            log.error(
                "Telegram HTTP error: %s",
                data
            )

            return False

        if not data.get("ok"):

            log.error(
                "Telegram API rejected message: %s",
                data
            )

            return False

        log.info(
            "TELEGRAM SENT SUCCESSFULLY | %s | %s",
            SYMBOL,
            signal_type
        )

        return True

    except Exception as exc:

        log.exception(
            "Telegram error: %r",
            exc
        )

        return False


# ============================================================
# EXECUTE TRADE / SIGNAL
# ============================================================

def execute_trade(
    signal_type,
    candles,
    result
):

    if len(candles) < 2:

        return False

    # Last CLOSED candle
    candle = candles[-2]

    return send_telegram(
        signal_type,
        candle,
        result
    )


# ============================================================
# STRATEGY LOOP
# ============================================================

def strategy_loop():

    global last_signal_time
    global last_price
    global last_update
    global last_processed_candle
    global last_signal
    global last_diagnostics

    log.info(
        "========================================"
    )

    log.info(
        "ATR3 STARTED"
    )

    log.info(
        "SYMBOL=%s",
        SYMBOL
    )

    log.info(
        "TIMEFRAME=%s",
        TIMEFRAME
    )

    log.info(
        "POLL=%s seconds",
        POLL_SECONDS
    )

    log.info(
        "COOLDOWN=%s minutes",
        COOLDOWN_MINUTES
    )

    log.info(
        "========================================"
    )

    while True:

        try:

            candles = get_klines()

            if len(candles) < 40:

                log.warning(
                    "Not enough candles: %d",
                    len(candles)
                )

                time.sleep(
                    POLL_SECONDS
                )

                continue

            # ------------------------------------------------
            # Current forming candle price
            # ------------------------------------------------

            last_price = (
                candles[-1]["close"]
            )

            last_update = (
                now_utc_string()
            )

            # ------------------------------------------------
            # LAST CLOSED CANDLE
            # ------------------------------------------------

            closed_candle = (
                candles[-2]
            )

            closed_candle_id = (
                closed_candle["time_ms"]
            )

            closed_time = (
                closed_candle["time"]
                .isoformat()
            )

            log.info(
                "Market | %s | "
                "Current=%.2f | "
                "Last CLOSED=%.2f | "
                "Candle=%s",
                SYMBOL,
                candles[-1]["close"],
                closed_candle["close"],
                closed_time
            )

            # ------------------------------------------------
            # Process each CLOSED candle only once
            # ------------------------------------------------

            if (
                last_processed_candle
                == closed_candle_id
            ):

                time.sleep(
                    POLL_SECONDS
                )

                continue

            last_processed_candle = (
                closed_candle_id
            )

            log.info(
                "========================================"
            )

            log.info(
                "NEW CLOSED CANDLE"
            )

            log.info(
                "Candle time: %s",
                closed_time
            )

            # ------------------------------------------------
            # Calculate
            # ------------------------------------------------

            result = calculate_signal(
                candles
            )

            if result is None:

                log.warning(
                    "Signal calculation unavailable"
                )

                time.sleep(
                    POLL_SECONDS
                )

                continue

            last_diagnostics = (
                result["diagnostics"]
            )

            # ------------------------------------------------
            # Print indicator values
            # ------------------------------------------------

            log.info(
                "INDICATORS | "
                "STO=%.2f | "
                "STO_PREV=%.2f | "
                "SIG=%.2f | "
                "SIG_PREV=%.2f | "
                "RSI=%.2f | "
                "MA10=%.2f | "
                "MA21=%.2f",
                result["stoch_main"],
                result["stoch_main_previous"],
                result["stoch_signal"],
                result["stoch_signal_previous"],
                result["rsi"],
                result["ma_fast"],
                result["ma_slow"]
            )

            # ------------------------------------------------
            # BUY diagnostics
            # ------------------------------------------------

            log.info(
                "BUY CHECK | "
                "CROSS=%s | "
                "BB=%s | "
                "MA=%s | "
                "RSI=%s | "
                "STO=%s",
                result["diagnostics"]["buy_cross"],
                result["diagnostics"]["buy_bb"],
                result["diagnostics"]["buy_ma"],
                result["diagnostics"]["buy_rsi"],
                result["diagnostics"]["buy_stoch_final"]
            )

            # ------------------------------------------------
            # SELL diagnostics
            # ------------------------------------------------

            log.info(
                "SELL CHECK | "
                "CROSS=%s | "
                "BB=%s | "
                "MA=%s | "
                "RSI=%s | "
                "STO=%s",
                result["diagnostics"]["sell_cross"],
                result["diagnostics"]["sell_bb"],
                result["diagnostics"]["sell_ma"],
                result["diagnostics"]["sell_rsi"],
                result["diagnostics"]["sell_stoch_final"]
            )

            executions = (
                result["executions"]
            )

            # ------------------------------------------------
            # COOLDOWN
            # ------------------------------------------------

            cooldown_active = (
                time.time()
                - last_signal_time
                <
                COOLDOWN_MINUTES * 60
            )

            if cooldown_active:

                remaining = (
                    COOLDOWN_MINUTES * 60
                    -
                    (
                        time.time()
                        -
                        last_signal_time
                    )
                )

                log.info(
                    "COOLDOWN ACTIVE | "
                    "Remaining %.0f seconds",
                    max(0, remaining)
                )

                time.sleep(
                    POLL_SECONDS
                )

                continue

            # ------------------------------------------------
            # SEND SIGNAL
            # ------------------------------------------------

            if executions:

                log.warning(
                    "🚨 SIGNAL FOUND: %s",
                    executions
                )

                for signal_type in executions:

                    success = execute_trade(
                        signal_type,
                        candles,
                        result
                    )

                    if success:

                        last_signal_time = (
                            time.time()
                        )

                        last_signal = (
                            signal_type
                        )

                        log.warning(
                            "✅ %s SIGNAL SENT TO TELEGRAM",
                            signal_type
                        )

                    else:

                        log.error(
                            "❌ %s SIGNAL FAILED TO SEND",
                            signal_type
                        )

            else:

                log.info(
                    "NO SIGNAL ON THIS CLOSED CANDLE"
                )

            log.info(
                "========================================"
            )

        except Exception as exc:

            log.exception(
                "Strategy loop error: %r",
                exc
            )

        time.sleep(
            POLL_SECONDS
        )


# ============================================================
# WEB ROUTES
# ============================================================

@app.get("/")
def home():

    return jsonify({

        "status":
            "running",

        "strategy":
            "ATR3",

        "symbol":
            SYMBOL,

        "timeframe":
            TIMEFRAME,

        "price":
            last_price,

        "last_update":
            last_update,

        "last_signal":
            last_signal
    })


@app.get("/health")
def health():

    return jsonify({

        "status":
            "ok",

        "strategy":
            "ATR3",

        "symbol":
            SYMBOL
    })


@app.get("/price")
def price():

    return jsonify({

        "symbol":
            SYMBOL,

        "price":
            last_price,

        "last_update":
            last_update
    })


@app.get("/status")
def status():

    cooldown_remaining = max(
        0,
        (
            COOLDOWN_MINUTES * 60
            -
            (
                time.time()
                -
                last_signal_time
            )
        )
    )

    return jsonify({

        "status":
            "running",

        "symbol":
            SYMBOL,

        "timeframe":
            TIMEFRAME,

        "last_price":
            last_price,

        "last_update":
            last_update,

        "last_processed_candle":
            last_processed_candle,

        "last_signal":
            last_signal,

        "cooldown_minutes":
            COOLDOWN_MINUTES,

        "cooldown_remaining_seconds":
            round(
                cooldown_remaining,
                1
            ),

        "diagnostics":
            last_diagnostics
    })


# ============================================================
# START
# ============================================================

def start_strategy():

    thread = threading.Thread(
        target=strategy_loop,
        daemon=True
    )

    thread.start()


if __name__ == "__main__":

    start_strategy()

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    log.info(
        "Starting Web Service on port %d",
        port
    )

    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )
