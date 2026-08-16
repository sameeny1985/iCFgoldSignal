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

SYMBOL = os.getenv(
    "SYMBOL",
    "BTCUSDT"
).upper()

TIMEFRAME = "1m"

POLL_SECONDS = float(
    os.getenv(
        "POLL_SECONDS",
        "5"
    )
)

COOLDOWN_MINUTES = int(
    os.getenv(
        "COOLDOWN_MINUTES",
        "3"
    )
)


# ============================================================
# STRATEGY INPUTS
# ============================================================

RISK_PERCENT = float(
    os.getenv(
        "RISK_PERCENT",
        "3.0"
    )
)

STOP_LOSS_PERCENT = float(
    os.getenv(
        "STOP_LOSS_PERCENT",
        "3.0"
    )
)

TAKE_PROFIT_PERCENT = float(
    os.getenv(
        "TAKE_PROFIT_PERCENT",
        "6.0"
    )
)

RSI_PERIOD = int(
    os.getenv(
        "RSI_PERIOD",
        "8"
    )
)

RSI_OVERBUY = float(
    os.getenv(
        "RSI_OVERBUY",
        "70"
    )
)

RSI_OVERSELL = float(
    os.getenv(
        "RSI_OVERSELL",
        "30"
    )
)

STO_OVERBUY_CRS = float(
    os.getenv(
        "STO_OVERBUY_CRS",
        "70"
    )
)

STO_OVERSELL_CRS = float(
    os.getenv(
        "STO_OVERSELL_CRS",
        "30"
    )
)

STO_OVERBUY_EXT = float(
    os.getenv(
        "STO_OVERBUY_EXT",
        "80"
    )
)

STO_OVERSELL_EXT = float(
    os.getenv(
        "STO_OVERSELL_EXT",
        "20"
    )
)

BB_PERIOD = int(
    os.getenv(
        "BB_PERIOD",
        "20"
    )
)

BB_DEV = float(
    os.getenv(
        "BB_DEV",
        "2.0"
    )
)

MA1_PERIOD = int(
    os.getenv(
        "MA1_PERIOD",
        "10"
    )
)

MA2_PERIOD = int(
    os.getenv(
        "MA2_PERIOD",
        "21"
    )
)

MA3_PERIOD = int(
    os.getenv(
        "MA3_PERIOD",
        "30"
    )
)

MA4_PERIOD = int(
    os.getenv(
        "MA4_PERIOD",
        "50"
    )
)

ADX_PERIOD = int(
    os.getenv(
        "ADX_PERIOD",
        "8"
    )
)

ADX_TREND_LEVEL = float(
    os.getenv(
        "ADX_TREND_LEVEL",
        "20"
    )
)

FLAT_CANDLE_LOOKBACK = int(
    os.getenv(
        "FLAT_CANDLE_LOOKBACK",
        "22"
    )
)

MIN_TREND_CANDLES = int(
    os.getenv(
        "MIN_TREND_CANDLES",
        "17"
    )
)


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()


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

last_signal_error = None

state_lock = threading.Lock()


# ============================================================
# UTILITY
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

    if not isinstance(raw, list):
        raise RuntimeError(
            f"Unexpected Binance response: {raw}"
        )

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

    if len(values) < period:
        return result

    running_sum = 0.0

    for i, value in enumerate(values):

        running_sum += value

        if i >= period:

            running_sum -= (
                values[i - period]
            )

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
            sum(window)
            / period
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
# BOLLINGER BANDS
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

    for i in range(
        len(closes)
    ):

        if (
            middle[i] is not None
            and
            std[i] is not None
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

    main = [None] * length

    signal = [None] * length


    # --------------------------------------------------------
    # RAW %K
    # --------------------------------------------------------

    for i in range(
        4,
        length
    ):

        lowest = min(
            lows[
                i - 4:i + 1
            ]
        )

        highest = max(
            highs[
                i - 4:i + 1
            ]
        )

        denominator = (
            highest - lowest
        )

        if denominator == 0:

            raw_k[i] = 0.0

        else:

            raw_k[i] = (
                100.0
                *
                (
                    closes[i]
                    - lowest
                )
                /
                denominator
            )


    # --------------------------------------------------------
    # SLOWING 3
    # --------------------------------------------------------

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
                sum(values)
                / 3.0
            )


    # --------------------------------------------------------
    # SIGNAL 3
    # --------------------------------------------------------

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
                sum(values)
                / 3.0
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


    # --------------------------------------------------------
    # PRICE CHANGES
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # INITIAL AVERAGE
    # --------------------------------------------------------

    avg_gain = (
        sum(
            gains[
                1:period + 1
            ]
        )
        /
        period
    )

    avg_loss = (
        sum(
            losses[
                1:period + 1
            ]
        )
        /
        period
    )


    # --------------------------------------------------------
    # FIRST RSI
    # --------------------------------------------------------

    if avg_loss == 0:

        result[period] = 100.0

    else:

        rs = (
            avg_gain
            /
            avg_loss
        )

        result[period] = (
            100.0
            -
            (
                100.0
                /
                (1.0 + rs)
            )
        )


    # --------------------------------------------------------
    # WILDER SMOOTHING
    # --------------------------------------------------------

    for i in range(
        period + 1,
        len(closes)
    ):

        avg_gain = (
            (
                avg_gain
                * (period - 1)
            )
            +
            gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss
                * (period - 1)
            )
            +
            losses[i]
        ) / period


        if avg_loss == 0:

            result[i] = 100.0

        else:

            rs = (
                avg_gain
                /
                avg_loss
            )

            result[i] = (
                100.0
                -
                (
                    100.0
                    /
                    (1.0 + rs)
                )
            )

    return result


# ============================================================
# CALCULATE SIGNAL
#
# IMPORTANT:
#
# We use the LAST CLOSED candle.
#
# candles[-1] = current/forming candle
# candles[-2] = last closed candle
# candles[-3] = previous closed candle
# ============================================================

def calculate_signal(candles):

    if len(candles) < 60:
        return None


    # ========================================================
    # OHLC
    # ========================================================

    opens = [
        candle["open"]
        for candle in candles
    ]

    highs = [
        candle["high"]
        for candle in candles
    ]

    lows = [
        candle["low"]
        for candle in candles
    ]

    closes = [
        candle["close"]
        for candle in candles
    ]


    # ========================================================
    # SIGNAL INDEX
    #
    # -2 = last CLOSED candle
    # -3 = candle before it
    # ========================================================

    signal_index = len(candles) - 2

    previous_index = len(candles) - 3

    if previous_index < 0:
        return None


    # ========================================================
    # CURRENT CLOSED CANDLE
    # ========================================================

    current = candles[
        signal_index
    ]

    previous = candles[
        previous_index
    ]


    # ========================================================
    # STOCHASTIC
    # ========================================================

    (
        stoch_main_values,
        stoch_signal_values
    ) = stochastic_5_3_3(
        highs,
        lows,
        closes
    )

    stoch_main_current = (
        stoch_main_values[
            signal_index
        ]
    )

    stoch_main_previous = (
        stoch_main_values[
            previous_index
        ]
    )

    stoch_signal_current = (
        stoch_signal_values[
            signal_index
        ]
    )

    stoch_signal_previous = (
        stoch_signal_values[
            previous_index
        ]
    )


    if (
        stoch_main_current is None
        or
        stoch_main_previous is None
        or
        stoch_signal_current is None
        or
        stoch_signal_previous is None
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

    bb_upper_current = (
        bb_upper_values[
            signal_index
        ]
    )

    bb_lower_current = (
        bb_lower_values[
            signal_index
        ]
    )

    if (
        bb_upper_current is None
        or
        bb_lower_current is None
    ):

        return None


    # ========================================================
    # MA
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
            signal_index
        ]
    )

    ma_slow = (
        ma_slow_values[
            signal_index
        ]
    )

    if (
        ma_fast is None
        or
        ma_slow is None
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
            signal_index
        ]
    )

    if rsi is None:
        return None


    # ========================================================
    # REAL STOCHASTIC CROSS
    # ========================================================

    bullish_cross = (
        stoch_main_previous
        <=
        stoch_signal_previous
        and
        stoch_main_current
        >
        stoch_signal_current
    )

    bearish_cross = (
        stoch_main_previous
        >=
        stoch_signal_previous
        and
        stoch_main_current
        <
        stoch_signal_current
    )


    # ========================================================
    # BUY CONDITIONS
    #
    # Bullish stochastic cross
    # in oversold area
    # ========================================================

    buy_ready = (
        bullish_cross
        and
        stoch_main_current
        <=
        STO_OVERSELL_CRS
    )


    # ========================================================
    # SELL CONDITIONS
    #
    # Bearish stochastic cross
    # in overbought area
    # ========================================================

    sell_ready = (
        bearish_cross
        and
        stoch_main_current
        >=
        STO_OVERBUY_CRS
    )


    # ========================================================
    # BOLLINGER FILTER
    #
    # BUY:
    # candle must touch/break lower BB
    #
    # SELL:
    # candle must touch/break upper BB
    # ========================================================

    if buy_ready:

        if current["low"] > bb_lower_current:

            buy_ready = False


    if sell_ready:

        if current["high"] < bb_upper_current:

            sell_ready = False


    # ========================================================
    # MA FILTER
    #
    # BUY -> MA10 >= MA21
    # SELL -> MA10 <= MA21
    # ========================================================

    if buy_ready:

        if ma_fast < ma_slow:

            buy_ready = False


    if sell_ready:

        if ma_fast > ma_slow:

            sell_ready = False


    # ========================================================
    # RSI FILTER
    #
    # BUY cannot happen when RSI is overbought
    #
    # SELL cannot happen when RSI is oversold
    # ========================================================

    if buy_ready:

        if rsi > RSI_OVERBUY:

            buy_ready = False


    if sell_ready:

        if rsi < RSI_OVERSELL:

            sell_ready = False


    # ========================================================
    # FINAL EXECUTIONS
    # ========================================================

    executions = []


    # ========================================================
    # BUY
    #
    # Extension confirmation:
    # stochastic must be above oversold extension
    # ========================================================

    if (
        buy_ready
        and
        stoch_main_current
        >
        STO_OVERSELL_EXT
    ):

        executions.append(
            "BUY"
        )


    # ========================================================
    # SELL
    #
    # Extension confirmation:
    # stochastic must be below overbought extension
    # ========================================================

    if (
        sell_ready
        and
        stoch_main_current
        <
        STO_OVERBUY_EXT
    ):

        executions.append(
            "SELL"
        )


    # ========================================================
    # DEBUG VALUES
    # ========================================================

    return {

        "executions": executions,

        "buy_ready": buy_ready,

        "sell_ready": sell_ready,

        "bullish_cross": bullish_cross,

        "bearish_cross": bearish_cross,

        "stoch_main_current": (
            stoch_main_current
        ),

        "stoch_main_previous": (
            stoch_main_previous
        ),

        "stoch_signal_current": (
            stoch_signal_current
        ),

        "stoch_signal_previous": (
            stoch_signal_previous
        ),

        "bb_lower": (
            bb_lower_current
        ),

        "bb_upper": (
            bb_upper_current
        ),

        "ma_fast": ma_fast,

        "ma_slow": ma_slow,

        "rsi": rsi,

        "current_price": (
            current["close"]
        ),

        "previous_close": (
            previous["close"]
        ),

        "current_candle_time": (
            current["time"].isoformat()
        ),

        "previous_candle_time": (
            previous["time"].isoformat()
        )
    }


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    signal_type,
    candle
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


    message = (
        f"🚨 ATR3 SIGNAL\n\n"
        f"Signal: {signal_type}\n"
        f"Symbol: {SYMBOL}\n"
        f"Price: {candle['close']:.2f}\n"
        f"Time: {now_utc_string()} UTC\n\n"
        f"Timeframe: {TIMEFRAME}"
    )


    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )


    try:

        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            },
            timeout=10
        )


        # ----------------------------------------------------
        # Do NOT blindly assume success
        # ----------------------------------------------------

        if response.status_code != 200:

            log.error(
                "Telegram HTTP ERROR | "
                "status=%s | body=%s",
                response.status_code,
                response.text
            )

            return False


        try:

            telegram_result = (
                response.json()
            )

        except Exception:

            telegram_result = None


        if (
            not telegram_result
            or
            not telegram_result.get(
                "ok",
                False
            )
        ):

            log.error(
                "Telegram API ERROR | %s",
                response.text
            )

            return False


        log.info(
            "TELEGRAM SENT SUCCESSFULLY | "
            "%s | %s",
            SYMBOL,
            signal_type
        )

        return True


    except Exception as exc:

        log.exception(
            "Telegram connection error: %r",
            exc
        )

        return False


# ============================================================
# TELEGRAM TEST
# ============================================================

def test_telegram():

    if not TELEGRAM_TOKEN:

        return {
            "success": False,
            "error": "TELEGRAM_TOKEN is empty"
        }


    if not TELEGRAM_CHAT_ID:

        return {
            "success": False,
            "error": "TELEGRAM_CHAT_ID is empty"
        }


    message = (
        "✅ ATR3 Telegram TEST\n"
        f"Symbol: {SYMBOL}\n"
        f"Time: {now_utc_string()} UTC"
    )


    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )


    try:

        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            },
            timeout=10
        )


        log.info(
            "Telegram test response: %s",
            response.text
        )


        if response.status_code != 200:

            return {
                "success": False,
                "status_code": response.status_code,
                "response": response.text
            }


        result = response.json()


        return {
            "success": bool(
                result.get(
                    "ok",
                    False
                )
            ),
            "response": result
        }


    except Exception as exc:

        log.exception(
            "Telegram test failed"
        )

        return {
            "success": False,
            "error": str(exc)
        }


# ============================================================
# EXECUTE TRADE
# ============================================================

def execute_trade(
    signal_type,
    candles
):

    if len(candles) < 2:

        log.error(
            "Cannot execute trade: "
            "not enough candles"
        )

        return False


    # --------------------------------------------------------
    # Last CLOSED candle
    # --------------------------------------------------------

    candle = candles[-2]


    log.info(
        "EXECUTE %s | "
        "Symbol=%s | "
        "Price=%.2f | "
        "Candle=%s",
        signal_type,
        SYMBOL,
        candle["close"],
        candle["time"].isoformat()
    )


    success = send_telegram(
        signal_type,
        candle
    )


    if success:

        log.info(
            "Signal delivery successful: %s",
            signal_type
        )

    else:

        log.error(
            "Signal delivery FAILED: %s",
            signal_type
        )


    return success


# ============================================================
# MAIN STRATEGY LOOP
# ============================================================

def strategy_loop():

    global last_signal_time
    global last_price
    global last_update
    global last_processed_candle
    global last_signal
    global last_signal_error


    log.info(
        "================================================"
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
        "POLL_SECONDS=%s",
        POLL_SECONDS
    )

    log.info(
        "COOLDOWN_MINUTES=%s",
        COOLDOWN_MINUTES
    )

    log.info(
        "================================================"
    )


    while True:

        try:

            candles = get_klines()


            if len(candles) < 60:

                log.warning(
                    "Not enough candles: %d",
                    len(candles)
                )

                time.sleep(
                    POLL_SECONDS
                )

                continue


            # =================================================
            # CURRENT FORMING PRICE
            # =================================================

            last_price = (
                candles[-1]["close"]
            )

            last_update = (
                now_utc_string()
            )


            log.info(
                "Market | "
                "Current price=%.2f | "
                "Closed candle=%s",
                last_price,
                candles[-2][
                    "time"
                ].isoformat()
            )


            # =================================================
            # PROCESS LAST CLOSED CANDLE ONLY
            # =================================================

            closed_candle_id = (
                candles[-2]["time_ms"]
            )


            if (
                last_processed_candle
                ==
                closed_candle_id
            ):

                time.sleep(
                    POLL_SECONDS
                )

                continue


            # =================================================
            # Mark candle as processed
            # =================================================

            last_processed_candle = (
                closed_candle_id
            )


            # =================================================
            # CALCULATE SIGNAL
            # =================================================

            result = calculate_signal(
                candles
            )


            if result is None:

                log.warning(
                    "Indicator calculation "
                    "returned None"
                )

                time.sleep(
                    POLL_SECONDS
                )

                continue


            # =================================================
            # DEBUG LOG
            # =================================================

            log.info(
                "DEBUG | "
                "BUY_READY=%s | "
                "SELL_READY=%s | "
                "BULL_CROSS=%s | "
                "BEAR_CROSS=%s | "
                "STO=%.2f | "
                "STO_SIG=%.2f | "
                "RSI=%.2f | "
                "MA10=%.2f | "
                "MA21=%.2f | "
                "BB_LOW=%.2f | "
                "BB_HIGH=%.2f",
                result["buy_ready"],
                result["sell_ready"],
                result["bullish_cross"],
                result["bearish_cross"],
                result["stoch_main_current"],
                result["stoch_signal_current"],
                result["rsi"],
                result["ma_fast"],
                result["ma_slow"],
                result["bb_lower"],
                result["bb_upper"]
            )


            # =================================================
            # COOLDOWN
            # =================================================

            cooldown_seconds = (
                COOLDOWN_MINUTES
                * 60
            )


            elapsed = (
                time.time()
                -
                last_signal_time
            )


            if elapsed < cooldown_seconds:

                remaining = (
                    cooldown_seconds
                    -
                    elapsed
                )

                log.info(
                    "COOLDOWN ACTIVE | "
                    "Remaining %.0f seconds",
                    remaining
                )

                time.sleep(
                    POLL_SECONDS
                )

                continue


            # =================================================
            # EXECUTIONS
            # =================================================

            executions = (
                result["executions"]
            )


            if not executions:

                log.info(
                    "NO SIGNAL | "
                    "BUY_READY=%s | "
                    "SELL_READY=%s",
                    result["buy_ready"],
                    result["sell_ready"]
                )

                time.sleep(
                    POLL_SECONDS
                )

                continue


            # =================================================
            # SEND SIGNALS
            #
            # Normally there should only be one.
            # =================================================

            for signal_type in executions:

                log.info(
                    "============================================"
                )

                log.info(
                    "SIGNAL DETECTED: %s",
                    signal_type
                )

                log.info(
                    "Candle: %s",
                    result[
                        "current_candle_time"
                    ]
                )

                log.info(
                    "Price: %.2f",
                    result[
                        "current_price"
                    ]
                )

                log.info(
                    "============================================"
                )


                success = execute_trade(
                    signal_type,
                    candles
                )


                if success:

                    last_signal = (
                        signal_type
                    )

                    last_signal_time = (
                        time.time()
                    )

                    last_signal_error = None

                else:

                    last_signal_error = (
                        f"Telegram failed for "
                        f"{signal_type}"
                    )


        except requests.exceptions.RequestException as exc:

            last_signal_error = (
                f"Network error: {exc}"
            )

            log.error(
                "Network error: %r",
                exc
            )


        except Exception as exc:

            last_signal_error = str(
                exc
            )

            log.exception(
                "Strategy loop error"
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

        "status": "running",

        "strategy": "ATR3",

        "symbol": SYMBOL,

        "timeframe": TIMEFRAME,

        "price": last_price,

        "last_update": last_update,

        "last_signal": last_signal,

        "last_error": last_signal_error

    })


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return jsonify({

        "status": "ok",

        "strategy": "ATR3",

        "symbol": SYMBOL

    })


# ============================================================
# PRICE
# ============================================================

@app.get("/price")
def price():

    return jsonify({

        "symbol": SYMBOL,

        "price": last_price,

        "last_update": last_update

    })


# ============================================================
# STATUS
# ============================================================

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

        "status": "running",

        "strategy": "ATR3",

        "symbol": SYMBOL,

        "timeframe": TIMEFRAME,

        "last_price": last_price,

        "last_update": last_update,

        "last_processed_candle": (
            last_processed_candle
        ),

        "last_signal": last_signal,

        "last_signal_error": (
            last_signal_error
        ),

        "cooldown_minutes": (
            COOLDOWN_MINUTES
        ),

        "cooldown_remaining_seconds": (
            round(
                cooldown_remaining,
                1
            )
        )

    })


# ============================================================
# TELEGRAM TEST ROUTE
# ============================================================

@app.get("/test-telegram")
def telegram_test_route():

    result = test_telegram()

    if result.get(
        "success",
        False
    ):

        return jsonify({
            "status": "ok",
            "message": (
                "Telegram test sent successfully"
            ),
            "result": result
        })


    return jsonify({
        "status": "error",
        "message": (
            "Telegram test failed"
        ),
        "result": result
    }), 500


# ============================================================
# START STRATEGY
# ============================================================

def start_strategy():

    thread = threading.Thread(
        target=strategy_loop,
        daemon=True
    )

    thread.start()

    log.info(
        "Strategy background thread started"
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    start_strategy()


    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )


    log.info(
        "Starting Flask server "
        "on port %d",
        port
    )


    app.run(
        host="0.0.0.0",
        port=port,
        threaded=True
    )
