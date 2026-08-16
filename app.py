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
# FLASK / RENDER WEB SERVICE
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


# ------------------------------------------------------------
# Original MQL5 inputs
# ------------------------------------------------------------

RISK_PERCENT = float(
    os.getenv("RISK_PERCENT", "3.0")
)

STOP_LOSS_PERCENT = float(
    os.getenv("STOP_LOSS_PERCENT", "3.0")
)

TAKE_PROFIT_PERCENT = float(
    os.getenv("TAKE_PROFIT_PERCENT", "6.0")
)

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

MA3_PERIOD = int(
    os.getenv("MA3_PERIOD", "30")
)

MA4_PERIOD = int(
    os.getenv("MA4_PERIOD", "50")
)

ADX_PERIOD = int(
    os.getenv("ADX_PERIOD", "8")
)

ADX_TREND_LEVEL = float(
    os.getenv("ADX_TREND_LEVEL", "20")
)

FLAT_CANDLE_LOOKBACK = int(
    os.getenv("FLAT_CANDLE_LOOKBACK", "22")
)

MIN_TREND_CANDLES = int(
    os.getenv("MIN_TREND_CANDLES", "17")
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


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return None


# ============================================================
# BINANCE M1 OHLC
# ============================================================

def get_klines():
    """
    Equivalent source for the MqlRates array.

    Binance returns candles chronologically:
        oldest -> newest

    The newest candle is the current/forming M1 candle.

    MQL5:
        price[0] = current candle
        price[1] = previous candle
    """

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
# SIMPLE MOVING AVERAGE
# ============================================================

def sma(values, period):
    """
    Returns a list where each element corresponds
    to the same candle index.

    None means insufficient history.
    """

    result = [None] * len(values)

    if period <= 0:
        return result

    running_sum = 0.0

    for i, value in enumerate(values):

        running_sum += value

        if i >= period:
            running_sum -= values[i - period]

        if i >= period - 1:
            result[i] = running_sum / period

    return result


# ============================================================
# STANDARD DEVIATION
# ============================================================

def rolling_std(values, period):
    """
    Population standard deviation.

    This is used to mirror the standard deviation
    used by the MQL5 Bollinger implementation.
    """

    result = [None] * len(values)

    if period <= 0:
        return result

    for i in range(period - 1, len(values)):

        window = values[
            i - period + 1:i + 1
        ]

        mean = sum(window) / period

        variance = sum(
            (x - mean) ** 2
            for x in window
        ) / period

        result[i] = variance ** 0.5

    return result


# ============================================================
# BOLLINGER BANDS
# ============================================================

def bollinger_bands(closes, period, deviation):

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

    return middle, upper, lower


# ============================================================
# STOCHASTIC 5,3,3
# ============================================================

def stochastic_5_3_3(
    highs,
    lows,
    closes
):
    """
    MQL5:

        iStochastic(
            symbol,
            PERIOD_M1,
            5,
            3,
            3,
            MODE_SMA,
            STO_LOWHIGH
        )

    Main:
        raw %K over 5 candles,
        then SMA(3).

    Signal:
        SMA(3) of Main.
    """

    length = len(closes)

    raw_k = [None] * length

    # --------------------------------------------------------
    # Raw stochastic
    # --------------------------------------------------------

    for i in range(4, length):

        lowest = min(
            lows[i - 4:i + 1]
        )

        highest = max(
            highs[i - 4:i + 1]
        )

        denominator = highest - lowest

        if denominator == 0:

            raw_k[i] = 0.0

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
    # Slowing = 3
    # --------------------------------------------------------

    main = [None] * length

    for i in range(length):

        if i < 6:
            continue

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
    # Signal = SMA(3) of Main
    # --------------------------------------------------------

    signal = [None] * length

    for i in range(length):

        if i < 8:
            continue

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

    return main, signal


# ============================================================
# RSI 8 - WILDER
# ============================================================

def rsi_wilder(closes, period):
    """
    RSI using Wilder smoothing, corresponding
    to the standard MQL5 RSI calculation.
    """

    result = [None] * len(closes)

    if len(closes) <= period:
        return result

    gains = [0.0] * len(closes)
    losses = [0.0] * len(closes)

    for i in range(1, len(closes)):

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
        ) / period
    )

    avg_loss = (
        sum(
            losses[1:period + 1]
        ) / period
    )

    if avg_loss == 0:
        result[period] = 100.0
    else:
        rs = avg_gain / avg_loss
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
                avg_gain * (period - 1)
            )
            + gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss * (period - 1)
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
# MQL5 COPYBUFFER INDEX EMULATION
# ============================================================

def copybuffer_last_three(values):
    """
    IMPORTANT:

    In the original MQL5 code:

        CopyBuffer(handle, 0, 0, 3, array)

    is called on arrays that are NOT explicitly
    ArraySetAsSeries().

    Therefore the requested current/previous/older
    values are physically represented as:

        array[0] = shift 2
        array[1] = shift 1
        array[2] = shift 0

    We intentionally reproduce that behavior.

    Python source is chronological:

        values[-1] = shift 0
        values[-2] = shift 1
        values[-3] = shift 2
    """

    if len(values) < 3:
        return None

    shift_2 = values[-3]
    shift_1 = values[-2]
    shift_0 = values[-1]

    return [
        shift_2,
        shift_1,
        shift_0
    ]


# ============================================================
# SIGNAL ENGINE
# ============================================================

def calculate_signal(candles):

    if len(candles) < 30:
        return None

    opens = [
        c["open"]
        for c in candles
    ]

    highs = [
        c["high"]
        for c in candles
    ]

    lows = [
        c["low"]
        for c in candles
    ]

    closes = [
        c["close"]
        for c in candles
    ]

    # ========================================================
    # STOMAIN / STOSIGNAL
    # ========================================================

    stoch_main_values, stoch_signal_values = (
        stochastic_5_3_3(
            highs,
            lows,
            closes
        )
    )

    stoch_main = copybuffer_last_three(
        stoch_main_values
    )

    stoch_signal = copybuffer_last_three(
        stoch_signal_values
    )

    if stoch_main is None:
        return None

    if stoch_signal is None:
        return None

    if any(
        value is None
        for value in stoch_main
    ):
        return None

    if any(
        value is None
        for value in stoch_signal
    ):
        return None

    # ========================================================
    # BOLLINGER
    # ========================================================

    bb_middle_values, bb_upper_values, bb_lower_values = (
        bollinger_bands(
            closes,
            BB_PERIOD,
            BB_DEV
        )
    )

    bb_upper = copybuffer_last_three(
        bb_upper_values
    )

    bb_lower = copybuffer_last_three(
        bb_lower_values
    )

    if bb_upper is None or bb_lower is None:
        return None

    if any(
        value is None
        for value in bb_upper
    ):
        return None

    if any(
        value is None
        for value in bb_lower
    ):
        return None

    # ========================================================
    # MQL5 price[] WITH ArraySetAsSeries(true)
    #
    # price[0] = current candle
    # price[1] = previous candle
    # ========================================================

    current = candles[-1]
    previous = candles[-2]

    # ========================================================
    # STAGE ONE
    # ========================================================

    # EXACTLY as original:
    #
    # buyReady = false;
    # sellReady = false;
    #
    # Then the original code eventually executes:
    #
    # buyReady = true;
    # sellReady = true;

    buy_ready = True
    sell_ready = True

    # --------------------------------------------------------
    # Active stochastic crossover
    # --------------------------------------------------------

    if (
        stoch_main[1] < stoch_signal[1]
        and
        stoch_main[0] > stoch_signal[0]
        and
        stoch_main[0] <= STO_OVERSELL_CRS
    ):
        buy_ready = True

    if (
        stoch_main[1] > stoch_signal[1]
        and
        stoch_main[0] < stoch_signal[0]
        and
        stoch_main[0] >= STO_OVERBUY_CRS
    ):
        sell_ready = True

    # ========================================================
    # BOLLINGER FILTER
    #
    # EXACT ORIGINAL:
    #
    # if(buyReady && price[0].low > bbLower[0])
    #     buyReady = false;
    #
    # if(sellReady && price[0].high < bbUpper[0])
    #     sellReady = false;
    # ========================================================

    if (
        buy_ready
        and
        current["low"] > bb_lower[0]
    ):
        buy_ready = False

    if (
        sell_ready
        and
        current["high"] < bb_upper[0]
    ):
        sell_ready = False

    # ========================================================
    # MA10 / MA21
    # ========================================================

    ma_fast_values = sma(
        closes,
        MA1_PERIOD
    )

    ma_slow_values = sma(
        closes,
        MA2_PERIOD
    )

    ma_fast = ma_fast_values[-1]
    ma_slow = ma_slow_values[-1]

    if ma_fast is None or ma_slow is None:
        return None

    if (
        buy_ready
        and
        ma_fast < ma_slow
    ):
        buy_ready = False

    if (
        sell_ready
        and
        ma_fast > ma_slow
    ):
        sell_ready = False

    # ========================================================
    # RSI
    # ========================================================

    rsi_values = rsi_wilder(
        closes,
        RSI_PERIOD
    )

    rsi = rsi_values[-1]

    if rsi is None:
        return None

    if (
        buy_ready
        and
        rsi > RSI_OVERBUY
    ):
        buy_ready = False

    if (
        sell_ready
        and
        rsi < RSI_OVERSELL
    ):
        sell_ready = False

    # ========================================================
    # RESULT
    # ========================================================

    executions = []

    # ========================================================
    # 5) EXECUTE TRADE
    #
    # EXACT ORIGINAL:
    #
    # if(buyReady && stochMain[0] > Sto_OverSell_Ext)
    #     ExecuteTrade(SELL);
    #
    # if(sellReady && stochMain[0] < Sto_OverBuy_Ext)
    #     ExecuteTrade(BUY);
    # ========================================================

    if (
        buy_ready
        and
        stoch_main[0] > STO_OVERSELL_EXT
    ):
        executions.append("SELL")

    if (
        sell_ready
        and
        stoch_main[0] < STO_OVERBUY_EXT
    ):
        executions.append("BUY")

    # ========================================================
    # STAGE TWO
    #
    # EXACT ORIGINAL
    #
    # if(buyReady && stochMain[0] > Sto_OverSell_Ext)
    #     ExecuteTrade(SELL);
    #
    # if(sellReady && stochMain[0] < Sto_OverBuy_Ext)
    #     ExecuteTrade(BUY);
    #
    # This means the same condition can generate a second
    # ExecuteTrade during the same OnTick.
    # We intentionally preserve it.
    # ========================================================

    if (
        buy_ready
        and
        stoch_main[0] > STO_OVERSELL_EXT
    ):
        executions.append("SELL")

    if (
        sell_ready
        and
        stoch_main[0] < STO_OVERBUY_EXT
    ):
        executions.append("BUY")

    return {
        "executions": executions,

        "buy_ready": buy_ready,
        "sell_ready": sell_ready,

        "stoch_main_0": stoch_main[0],
        "stoch_main_1": stoch_main[1],

        "stoch_signal_0": stoch_signal[0],
        "stoch_signal_1": stoch_signal[1],

        "bb_lower_0": bb_lower[0],
        "bb_upper_0": bb_upper[0],

        "ma_fast": ma_fast,
        "ma_slow": ma_slow,

        "rsi": rsi,

        "current_price": current["close"],
        "previous_close": previous["close"],

        "current_candle_time": current["time"].isoformat(),
        "previous_candle_time": previous["time"].isoformat()
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
            "TELEGRAM_TOKEN is not configured"
        )
        return False

    if not TELEGRAM_CHAT_ID:
        log.error(
            "TELEGRAM_CHAT_ID is not configured"
        )
        return False

    message = (
        f"📈 Signal: {signal_type}\n"
        f"Symbol: {SYMBOL}\n"
        f"Price: {candle['close']:.5f}\n"
        f"Time: {now_utc_string()}"
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

        response.raise_for_status()

        log.info(
            "Telegram signal sent: %s %s",
            SYMBOL,
            signal_type
        )

        return True

    except Exception as exc:

        log.error(
            "Telegram error: %r",
            exc
        )

        return False


# ============================================================
# EXECUTE TRADE
# ============================================================

def execute_trade(
    signal_type,
    candles
):

    if len(candles) < 2:
        return

    # ========================================================
    # EXACT MQL5:
    #
    # price[1] is used for the signal price
    # ========================================================

    candle = candles[-2]

    send_telegram(
        signal_type,
        candle
    )


# ============================================================
# MAIN STRATEGY LOOP
# ============================================================

def strategy_loop():

    global last_signal_time
    global last_price
    global last_update
    global last_processed_candle

    log.info(
        "ATR3 started | SYMBOL=%s | TIMEFRAME=%s",
        SYMBOL,
        TIMEFRAME
    )

    while True:

        try:

            candles = get_klines()

            if len(candles) < 30:

                log.warning(
                    "Not enough candles: %d",
                    len(candles)
                )

                time.sleep(
                    POLL_SECONDS
                )

                continue

            last_price = candles[-1]["close"]

            last_update = now_utc_string()

            log.info(
                "Fetched %d bars | Last close: %.2f",
                len(candles),
                last_price
            )

            # =================================================
            # MQL5 OnTick is called many times inside current
            # candle.
            #
            # To avoid replaying the exact same condition
            # thousands of times through Binance polling,
            # process each M1 candle once.
            #
            # This is the safe server equivalent.
            # =================================================

            current_candle_id = (
                candles[-1]["time_ms"]
            )

            if (
                last_processed_candle
                == current_candle_id
            ):

                time.sleep(
                    POLL_SECONDS
                )

                continue

            last_processed_candle = (
                current_candle_id
            )

            # =================================================
            # COOLDOWN
            #
            # Exact concept of:
            #
            # TimeCurrent() - lastSignalTime
            # < CooldownMinutes * 60
            # =================================================

            if (
                time.time()
                - last_signal_time
                < COOLDOWN_MINUTES * 60
            ):

                log.info(
                    "Cooldown active"
                )

                time.sleep(
                    POLL_SECONDS
                )

                continue

            # =================================================
            # CALCULATE SIGNAL
            # =================================================

            result = calculate_signal(
                candles
            )

            if result is None:

                log.warning(
                    "Indicator calculation unavailable"
                )

                time.sleep(
                    POLL_SECONDS
                )

                continue

            executions = result[
                "executions"
            ]

            if executions:

                log.info(
                    "SIGNAL | %s | "
                    "BUY_READY=%s | "
                    "SELL_READY=%s | "
                    "STO=%.4f | RSI=%.4f | "
                    "MA10=%.4f | MA21=%.4f",
                    executions,
                    result["buy_ready"],
                    result["sell_ready"],
                    result["stoch_main_0"],
                    result["rsi"],
                    result["ma_fast"],
                    result["ma_slow"]
                )

                # =================================================
                # IMPORTANT:
                #
                # Original ExecuteTrade updates lastSignalTime
                # after sending.
                #
                # If two ExecuteTrade calls happen in same OnTick,
                # original MQL can send both.
                #
                # We preserve that behavior here.
                # =================================================

                for signal_type in executions:

                    execute_trade(
                        signal_type,
                        candles
                    )

                    last_signal_time = (
                        time.time()
                    )

            else:

                log.info(
                    "No signal | "
                    "BUY_READY=%s | "
                    "SELL_READY=%s | "
                    "STO=%.4f | RSI=%.4f",
                    result["buy_ready"],
                    result["sell_ready"],
                    result["stoch_main_0"],
                    result["rsi"]
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
        "status": "running",
        "strategy": "ATR3",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "price": last_price,
        "last_update": last_update
    })


@app.get("/health")
def health():

    return jsonify({
        "status": "ok",
        "strategy": "ATR3",
        "symbol": SYMBOL
    })


@app.get("/price")
def price():

    return jsonify({
        "symbol": SYMBOL,
        "price": last_price,
        "last_update": last_update
    })


@app.get("/status")
def status():

    return jsonify({
        "status": "running",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "last_price": last_price,
        "last_update": last_update,
        "last_processed_candle": last_processed_candle,
        "cooldown_minutes": COOLDOWN_MINUTES
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
        os.getenv("PORT", "10000")
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
