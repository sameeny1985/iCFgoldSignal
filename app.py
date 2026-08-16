import os
import time
import threading
import logging
from datetime import datetime, timezone

import requests
import numpy as np
import pandas as pd
from flask import Flask, jsonify


# ============================================================
# INPUT PARAMETERS - EXACTLY FROM MQL5
# ============================================================

RiskPercent = float(os.getenv("RiskPercent", "3.0"))
StopLossPercent = float(os.getenv("StopLossPercent", "3.0"))
TakeProfitPercent = float(os.getenv("TakeProfitPercent", "6.0"))

RSI_Period = int(os.getenv("RSI_Period", "8"))
RSI_OverBuy = float(os.getenv("RSI_OverBuy", "70"))
RSI_OverSell = float(os.getenv("RSI_OverSell", "30"))

Sto_OverBuy_Crs = float(
    os.getenv("Sto_OverBuy_Crs", "70")
)

Sto_OverSell_Crs = float(
    os.getenv("Sto_OverSell_Crs", "30")
)

Sto_OverBuy_Ext = float(
    os.getenv("Sto_OverBuy_Ext", "80")
)

Sto_OverSell_Ext = float(
    os.getenv("Sto_OverSell_Ext", "20")
)

BB_Period = int(os.getenv("BB_Period", "20"))
BB_Dev = float(os.getenv("BB_Dev", "2.0"))

MA1_Period = int(os.getenv("MA1_Period", "10"))
MA2_Period = int(os.getenv("MA2_Period", "21"))
MA3_Period = int(os.getenv("MA3_Period", "30"))
MA4_Period = int(os.getenv("MA4_Period", "50"))

ADX_Period = int(os.getenv("ADX_Period", "8"))
ADX_TrendLevel = float(
    os.getenv("ADX_TrendLevel", "20.0")
)

FlatCandleLookback = int(
    os.getenv("FlatCandleLookback", "22")
)

MinTrendCandles = int(
    os.getenv("MinTrendCandles", "17")
)

CooldownMinutes = int(
    os.getenv("CooldownMinutes", "3")
)


# ============================================================
# SYMBOL
# ============================================================

SYMBOL = os.getenv(
    "SYMBOL",
    "BTCUSDT"
).upper()


# ============================================================
# BINANCE
# ============================================================

BINANCE_KLINES = (
    "https://api.binance.com/api/v3/klines"
)

BINANCE_TICKER = (
    "https://api.binance.com/api/v3/ticker/price"
)


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN"
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID"
)


# ============================================================
# RUNTIME
# ============================================================

POLL_SECONDS = float(
    os.getenv("POLL_SECONDS", "1")
)

# MQL5 درخواست 100 کندل دارد
CANDLE_LIMIT = 100


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# STATE
# ============================================================

lastSignalTime = 0.0

last_price = None

last_candle = None

last_signal = None

last_error = None


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("atr3")


# ============================================================
# GET BINANCE TICKER
# ============================================================

def get_ticker():

    r = requests.get(
        BINANCE_TICKER,
        params={
            "symbol": SYMBOL
        },
        timeout=10
    )

    r.raise_for_status()

    return float(
        r.json()["price"]
    )


# ============================================================
# GET M1 KLINES
# ============================================================

def get_klines():

    r = requests.get(
        BINANCE_KLINES,
        params={
            "symbol": SYMBOL,
            "interval": "1m",
            "limit": CANDLE_LIMIT
        },
        timeout=10
    )

    r.raise_for_status()

    raw = r.json()

    rows = []

    for x in raw:

        rows.append({
            "time": int(x[0]),
            "open": float(x[1]),
            "high": float(x[2]),
            "low": float(x[3]),
            "close": float(x[4]),
            "volume": float(x[5])
        })

    return rows


# ============================================================
# DATAFRAME
# ============================================================

def make_dataframe(rows):

    df = pd.DataFrame(rows)

    df["time"] = pd.to_datetime(
        df["time"],
        unit="ms",
        utc=True
    )

    return df.reset_index(
        drop=True
    )


# ============================================================
# SMA
# ============================================================

def SMA(series, period):

    return series.rolling(
        period,
        min_periods=period
    ).mean()


# ============================================================
# RSI
# ============================================================

def RSI(series, period):

    delta = series.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    rs = (
        avg_gain /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    result = (
        100 -
        (
            100 /
            (1 + rs)
        )
    )

    result = result.mask(
        (avg_loss == 0) &
        (avg_gain > 0),
        100.0
    )

    return result


# ============================================================
# STOCHASTIC 5,3,3
#
# MQL:
#
# iStochastic(
#   _Symbol,
#   PERIOD_M1,
#   5,
#   3,
#   3,
#   MODE_SMA,
#   STO_LOWHIGH
# );
#
# ============================================================

def stochastic(df):

    lowest = (
        df["low"]
        .rolling(
            5,
            min_periods=5
        )
        .min()
    )

    highest = (
        df["high"]
        .rolling(
            5,
            min_periods=5
        )
        .max()
    )

    denominator = (
        highest - lowest
    )

    raw_k = (
        100 *
        (
            (df["close"] - lowest) /
            denominator.replace(
                0,
                np.nan
            )
        )
    )

    # slowing = 3, MODE_SMA
    main = (
        raw_k
        .rolling(
            3,
            min_periods=3
        )
        .mean()
    )

    # signal = SMA(main, 3)
    signal = (
        main
        .rolling(
            3,
            min_periods=3
        )
        .mean()
    )

    return main, signal


# ============================================================
# PREPARE INDICATORS
# ============================================================

def prepare(df):

    # --------------------------------------------------------
    # Stochastic
    # --------------------------------------------------------

    (
        df["stoch_main"],
        df["stoch_signal"]
    ) = stochastic(df)

    # --------------------------------------------------------
    # MA
    #
    # MQL:
    #
    # iMA(..., MA1_Period, 0, MODE_SMA, PRICE_CLOSE)
    # --------------------------------------------------------

    df["ma_fast"] = SMA(
        df["close"],
        MA1_Period
    )

    df["ma_slow"] = SMA(
        df["close"],
        MA2_Period
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    df["rsi"] = RSI(
        df["close"],
        RSI_Period
    )

    # --------------------------------------------------------
    # EXACT MQL5 iBands CALL
    #
    # iBands(
    #     _Symbol,
    #     PERIOD_M1,
    #     BB_Period,
    #     BB_Dev,
    #     0,
    #     PRICE_CLOSE
    # );
    #
    # MQL5 signature:
    #
    # iBands(
    #   symbol,
    #   period,
    #   bands_period,
    #   bands_shift,
    #   deviation,
    #   applied_price
    # )
    #
    # پس در کد اصلی:
    #
    # bands_period = 20
    # bands_shift  = 2
    # deviation    = 0
    #
    # بنابراین Upper/Lower = Middle
    #
    # عمداً اصلاح نمی‌کنیم.
    # --------------------------------------------------------

    middle = SMA(
        df["close"],
        BB_Period
    )

    df["bb_middle"] = middle

    df["bb_upper"] = middle
    df["bb_lower"] = middle

    return df


# ============================================================
# MQL5 CopyBuffer BEHAVIOR
#
# بسیار مهم:
#
# CopyBuffer() حتی اگر مقصد Series باشد، داده را
# به ترتیب oldest -> newest داخل آرایه قرار می‌دهد.
#
# بنابراین برای:
#
# CopyBuffer(handle,0,0,3,array)
#
# داریم:
#
# array[0] = shift 2
# array[1] = shift 1
# array[2] = shift 0
#
# این دقیقاً چیزی است که برای EA تو لازم داریم.
# ============================================================

def copybuffer3(series):

    values = series.iloc[-3:].to_numpy()

    return {
        0: values[0],
        1: values[1],
        2: values[2]
    }


# ============================================================
# EXECUTE TRADE
# ============================================================

def ExecuteTrade(order_type, df, ticker):

    global lastSignalTime
    global last_signal

    # --------------------------------------------------------
    # CopyRates(_Symbol, PERIOD_M1, 0, 2, price)
    #
    # ArraySetAsSeries(price,true)
    #
    # price[0] = current candle
    # price[1] = previous candle
    # --------------------------------------------------------

    current = df.iloc[-1]
    previous = df.iloc[-2]

    signal_time = previous["time"]

    candle_high = previous["high"]
    candle_low = previous["low"]

    # --------------------------------------------------------
    # MQL:
    #
    # signalType = BUY / SELL
    #
    # price = price[1].close
    #
    # --------------------------------------------------------

    signal_type = (
        "BUY"
        if order_type == "BUY"
        else "SELL"
    )

    # --------------------------------------------------------
    # TimeTradeServer()
    # --------------------------------------------------------

    server_time = datetime.now(
        timezone.utc
    ).strftime(
        "%Y.%m.%d %H:%M"
    )

    # --------------------------------------------------------
    # دقیقاً پیام MQL
    # --------------------------------------------------------

    message = (
        f"📈 Signal: {signal_type}\n"
        f"Symbol: {SYMBOL}\n"
        f"Price: {previous['close']:.5f}\n"
        f"Time: {server_time}"
    )

    send_telegram(message)

    # --------------------------------------------------------
    # MQL:
    #
    # lastSignalTime = TimeCurrent();
    # --------------------------------------------------------

    lastSignalTime = time.time()

    last_signal = {
        "type": signal_type,
        "symbol": SYMBOL,
        "price": float(
            previous["close"]
        ),
        "candle_time": signal_time.isoformat(),
        "server_time": server_time
    }

    logger.info(
        "SIGNAL => %s | %s | %.5f | candle=%s",
        signal_type,
        SYMBOL,
        previous["close"],
        signal_time
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if (
        not TELEGRAM_TOKEN or
        not TELEGRAM_CHAT_ID
    ):

        logger.error(
            "TELEGRAM_TOKEN / TELEGRAM_CHAT_ID missing"
        )

        return False

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
            timeout=5
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):

            logger.error(
                "Telegram response: %s",
                result
            )

            return False

        logger.info(
            "Telegram sent"
        )

        return True

    except Exception as e:

        logger.error(
            "Telegram send failed: %r",
            e
        )

        return False


# ============================================================
# COOLDOWN
# ============================================================

def cooldown_active():

    if lastSignalTime == 0:
        return False

    return (
        time.time() -
        lastSignalTime
    ) < (
        CooldownMinutes * 60
    )


# ============================================================
# ONTICK - EXACT PORT OF MQL
# ============================================================

def OnTick():

    # --------------------------------------------------------
    # if(!CheckTimeframe()) return;
    #
    # Python always uses Binance 1m here.
    # --------------------------------------------------------

    if cooldown_active():

        return

    # --------------------------------------------------------
    # CopyRates(... 0,100,price)
    # --------------------------------------------------------

    rows = get_klines()

    if len(rows) < 30:

        return

    df = make_dataframe(
        rows
    )

    df = prepare(df)

    # --------------------------------------------------------
    # Stochastic:
    #
    # CopyBuffer(..., 0, 3, stochMain)
    # CopyBuffer(..., 1, 3, stochSignal)
    # --------------------------------------------------------

    stochMain = copybuffer3(
        df["stoch_main"]
    )

    stochSignal = copybuffer3(
        df["stoch_signal"]
    )

    # --------------------------------------------------------
    # Bollinger
    # --------------------------------------------------------

    bbUpper = copybuffer3(
        df["bb_upper"]
    )

    bbLower = copybuffer3(
        df["bb_lower"]
    )

    # --------------------------------------------------------
    # CopyRates series
    #
    # price[0] = current
    # price[1] = previous
    # --------------------------------------------------------

    price0 = df.iloc[-1]
    price1 = df.iloc[-2]

    # --------------------------------------------------------
    # buyReady = false
    # sellReady = false
    # --------------------------------------------------------

    buyReady = False
    sellReady = False

    # ========================================================
    # مرحله اول
    # ========================================================

    # --------------------------------------------------------
    # 1️⃣ کراس استوکاستیک
    #
    # EXACT
    # --------------------------------------------------------

    if (
        stochMain[1] <
        stochSignal[1]
        and
        stochMain[0] >
        stochSignal[0]
        and
        stochMain[0] <=
        Sto_OverSell_Crs
    ):

        buyReady = True

    if (
        stochMain[1] >
        stochSignal[1]
        and
        stochMain[0] <
        stochSignal[0]
        and
        stochMain[0] >=
        Sto_OverBuy_Crs
    ):

        sellReady = True

    # --------------------------------------------------------
    # 2️⃣ Bollinger
    #
    # EXACT:
    #
    # if(buyReady && price[0].low > bbLower[0])
    #     buyReady = false;
    #
    # if(sellReady && price[0].high < bbUpper[0])
    #     sellReady = false;
    # --------------------------------------------------------

    if (
        buyReady
        and
        price0["low"] >
        bbLower[0]
    ):

        buyReady = False

    if (
        sellReady
        and
        price0["high"] <
        bbUpper[0]
    ):

        sellReady = False

    # ========================================================
    # MA FILTER
    # ========================================================

    # MQL CopyBuffer(..., 0, 1, maFast)
    #
    # اینجا [0] مقدار shift=0 است.
    # یعنی current candle.

    maFast = df["ma_fast"].iloc[-1]
    maSlow = df["ma_slow"].iloc[-1]

    if (
        buyReady
        and
        maFast < maSlow
    ):

        buyReady = False

    if (
        sellReady
        and
        maFast > maSlow
    ):

        sellReady = False

    # ========================================================
    # RSI
    # ========================================================

    rsi = df["rsi"].iloc[-1]

    if (
        buyReady
        and
        rsi > RSI_OverBuy
    ):

        buyReady = False

    if (
        sellReady
        and
        rsi < RSI_OverSell
    ):

        sellReady = False

    # ========================================================
    # 5️⃣ اجرای معامله
    #
    # EXACTLY AS MQL
    #
    # buyReady -> SELL
    # sellReady -> BUY
    # ========================================================

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # اگر هر دو شرط برقرار باشند، MQL هر دو ExecuteTrade
    # را پشت سر هم اجرا می‌کند.
    # --------------------------------------------------------

    if (
        buyReady
        and
        stochMain[0] >
        Sto_OverSell_Ext
    ):

        ExecuteTrade(
            "SELL",
            df,
            get_ticker()
        )

    if (
        sellReady
        and
        stochMain[0] <
        Sto_OverBuy_Ext
    ):

        ExecuteTrade(
            "BUY",
            df,
            get_ticker()
        )

    # ========================================================
    # مرحله دوم
    # ========================================================

    # --------------------------------------------------------
    # EXACT
    #
    # if(buyReady && stochMain[0] > Sto_OverSell_Ext)
    #     ExecuteTrade(ORDER_TYPE_SELL);
    #
    # if(sellReady && stochMain[0] < Sto_OverBuy_Ext)
    #     ExecuteTrade(ORDER_TYPE_BUY);
    # --------------------------------------------------------

    if (
        buyReady
        and
        stochMain[0] >
        Sto_OverSell_Ext
    ):

        ExecuteTrade(
            "SELL",
            df,
            get_ticker()
        )

    if (
        sellReady
        and
        stochMain[0] <
        Sto_OverBuy_Ext
    ):

        ExecuteTrade(
            "BUY",
            df,
            get_ticker()
        )


# ============================================================
# WORKER
# ============================================================

def strategy_loop():

    global last_price
    global last_candle
    global last_error

    logger.info(
        "========================================"
    )

    logger.info(
        "ATR3 Python started"
    )

    logger.info(
        "SYMBOL = %s",
        SYMBOL
    )

    logger.info(
        "TIMEFRAME = M1"
    )

    logger.info(
        "========================================"
    )

    while True:

        try:

            # Ticker فقط برای نمایش وضعیت
            # منطق استراتژی روی OHLC M1 است.
            last_price = get_ticker()

            rows = get_klines()

            if rows:

                last_candle = rows[-1]

            # اجرای OnTick
            OnTick()

            last_error = None

        except Exception as e:

            last_error = repr(e)

            logger.exception(
                "OnTick ERROR"
            )

        time.sleep(
            POLL_SECONDS
        )


# ============================================================
# WEB
# ============================================================

@app.get("/")
def home():

    return jsonify({
        "status": "running",
        "symbol": SYMBOL,
        "timeframe": "M1",
        "ticker": last_price,
        "last_candle": last_candle,
        "last_signal": last_signal,
        "last_error": last_error
    })


@app.get("/health")
def health():

    return jsonify({
        "status": "ok",
        "symbol": SYMBOL
    })


@app.get("/price")
def price():

    return jsonify({
        "symbol": SYMBOL,
        "ticker": last_price,
        "candle": last_candle
    })


@app.get("/debug")
def debug():

    return jsonify({
        "symbol": SYMBOL,
        "ticker": last_price,
        "last_candle": last_candle,
        "last_signal": last_signal,
        "last_signal_time": lastSignalTime,
        "last_error": last_error
    })


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    worker = threading.Thread(
        target=strategy_loop,
        daemon=True
    )

    worker.start()

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
