#!/usr/bin/env python3
"""
Exact Python port of atr3.mq5 (Multi Logic Expert)
- Timeframe: M1 only
- Indicator: Stochastic (5, 3, 3, SMA, Low/High)
- Buy signal  → sends SELL  (exactly as original MQ5)
- Sell signal → sends BUY   (exactly as original MQ5)
- Cooldown: 3 minutes
- Telegram notification identical format
"""

import os
import time
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

import requests
import pandas as pd
import numpy as np
from dotenv import load_dotenv

load_dotenv()

# ======================== CONFIG (same as MQ5 inputs) ========================
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "3.0"))
STOP_LOSS_PERCENT = float(os.getenv("STOP_LOSS_PERCENT", "3.0"))
TAKE_PROFIT_PERCENT = float(os.getenv("TAKE_PROFIT_PERCENT", "6.0"))

RSI_PERIOD = int(os.getenv("RSI_PERIOD", "8"))
RSI_OVERBUY = int(os.getenv("RSI_OVERBUY", "70"))
RSI_OVERSELL = int(os.getenv("RSI_OVERSELL", "30"))

BB_PERIOD = int(os.getenv("BB_PERIOD", "20"))
BB_DEV = float(os.getenv("BB_DEV", "2.0"))
MA1_PERIOD = int(os.getenv("MA1_PERIOD", "10"))
MA2_PERIOD = int(os.getenv("MA2_PERIOD", "21"))
MA3_PERIOD = int(os.getenv("MA3_PERIOD", "30"))
MA4_PERIOD = int(os.getenv("MA4_PERIOD", "50"))
ADX_PERIOD = int(os.getenv("ADX_PERIOD", "8"))
ADX_TREND_LEVEL = float(os.getenv("ADX_TREND_LEVEL", "20.0"))
FLAT_CANDLE_LOOKBACK = int(os.getenv("FLAT_CANDLE_LOOKBACK", "22"))
MIN_TREND_CANDLES = int(os.getenv("MIN_TREND_CANDLES", "17"))

COOLDOWN_MINUTES = int(os.getenv("COOLDOWN_MINUTES", "3"))

# Telegram (same as original)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8808022991:AAFmonV527NXUTIE5zpvAmvzRboS0MSEB0w")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1003698594050")

# Symbol & Data provider
SYMBOL = os.getenv("SYMBOL", "EURUSD")          # change if needed
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "")  # free key from twelvedata.com
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "15"))  # how often to check

# Stochastic parameters (hard-coded in original MQ5)
STOCH_K = 5
STOCH_D = 3
STOCH_SLOWING = 3

# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("atr3-bot")

last_signal_time: float = 0.0


def send_telegram(message: str) -> bool:
    """Exact equivalent of SendTelegram() in MQ5"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Telegram token or chat_id missing")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code == 200:
            logger.info("Telegram message sent successfully")
            return True
        else:
            logger.error(f"Telegram failed: {r.status_code} - {r.text}")
            return False
    except Exception as e:
        logger.error(f"Telegram exception: {e}")
        return False


def calculate_stochastic(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """
    Stochastic Oscillator exactly as MQL5:
    iStochastic(..., 5, 3, 3, MODE_SMA, STO_LOWHIGH)
    %K = SMA( (Close - LowestLow) / (HighestHigh - LowestLow) * 100 , slowing=3 )
    %D = SMA(%K, 3)
    """
    low = df["low"]
    high = df["high"]
    close = df["close"]

    lowest_low = low.rolling(window=STOCH_K).min()
    highest_high = high.rolling(window=STOCH_K).max()

    # Avoid division by zero
    denom = highest_high - lowest_low
    denom = denom.replace(0, np.nan)

    raw_k = 100 * (close - lowest_low) / denom
    # %K with slowing = 3 (SMA)
    k = raw_k.rolling(window=STOCH_SLOWING).mean()
    # %D = SMA of %K with period 3
    d = k.rolling(window=STOCH_D).mean()

    return k, d


def fetch_m1_data(symbol: str, bars: int = 100) -> Optional[pd.DataFrame]:
    """
    Fetch M1 candles.
    Priority:
      1. Twelve Data (recommended free tier)
      2. Fallback to yfinance (limited M1 history)
    """
    if TWELVE_DATA_API_KEY:
        try:
            url = "https://api.twelvedata.com/time_series"
            params = {
                "symbol": symbol,
                "interval": "1min",
                "outputsize": bars,
                "apikey": TWELVE_DATA_API_KEY,
                "format": "JSON",
            }
            r = requests.get(url, params=params, timeout=15)
            data = r.json()
            if "values" not in data:
                logger.error(f"Twelve Data error: {data}")
                return None
            df = pd.DataFrame(data["values"])
            df = df.rename(columns={
                "datetime": "time",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
            })
            df["time"] = pd.to_datetime(df["time"])
            for col in ["open", "high", "low", "close"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.sort_values("time").reset_index(drop=True)
            return df
        except Exception as e:
            logger.error(f"Twelve Data fetch failed: {e}")

    # Fallback: yfinance (works for many forex pairs with =X suffix)
    try:
        import yfinance as yf
        ticker = symbol if symbol.endswith("=X") else f"{symbol}=X"
        # yfinance 1m data is limited to last 7 days
        data = yf.download(ticker, period="1d", interval="1m", progress=False, auto_adjust=True)
        if data.empty:
            logger.error("yfinance returned empty data")
            return None
        data = data.reset_index()
        data.columns = [c.lower() if isinstance(c, str) else c[0].lower() for c in data.columns]
        # keep only needed columns
        cols = ["datetime" if "datetime" in data.columns else "date", "open", "high", "low", "close"]
        # normalize column names
        rename_map = {}
        for c in data.columns:
            cl = str(c).lower()
            if "date" in cl or "time" in cl:
                rename_map[c] = "time"
            elif cl == "open":
                rename_map[c] = "open"
            elif cl == "high":
                rename_map[c] = "high"
            elif cl == "low":
                rename_map[c] = "low"
            elif cl == "close":
                rename_map[c] = "close"
        data = data.rename(columns=rename_map)
        data = data[["time", "open", "high", "low", "close"]].dropna()
        data = data.tail(bars).reset_index(drop=True)
        return data
    except Exception as e:
        logger.error(f"yfinance fallback failed: {e}")
        return None


def check_signals(df: pd.DataFrame) -> Tuple[bool, bool]:
    """
    Exact logic from OnTick():

    // سیگنال خرید:
    // وقتی که خط اصلی (آبی) از زیر خط سیگنال (قرمز) را در زیر یا روی 20 کراس کند
    if(stochMain[1] < stochSignal[1] && stochMain[0] > stochSignal[0] && stochMain[0] <= 20)
       buySignal = true;

    // سیگنال فروش:
    // وقتی که خط اصلی از بالای خط سیگنال را در بالای یا روی 80 کراس کند
    if(stochMain[1] > stochSignal[1] && stochMain[0] < stochSignal[0] && stochMain[0] >= 80)
       sellSignal = true;
    """
    if len(df) < 30:
        return False, False

    k, d = calculate_stochastic(df)

    # We need the last two closed values (index -2 and -1 in series terms)
    # In MQ5: [0] = current (most recent), [1] = previous
    # After ArraySetAsSeries(true) → index 0 is newest
    # In our pandas (oldest first) → iloc[-1] = newest, iloc[-2] = previous

    if pd.isna(k.iloc[-1]) or pd.isna(d.iloc[-1]) or pd.isna(k.iloc[-2]) or pd.isna(d.iloc[-2]):
        return False, False

    stoch_main_0 = k.iloc[-1]   # current
    stoch_main_1 = k.iloc[-2]   # previous
    stoch_signal_0 = d.iloc[-1]
    stoch_signal_1 = d.iloc[-2]

    buy_signal = False
    sell_signal = False

    # Buy signal condition (exact)
    if (stoch_main_1 < stoch_signal_1 and
            stoch_main_0 > stoch_signal_0 and
            stoch_main_0 <= 20):
        buy_signal = True

    # Sell signal condition (exact)
    if (stoch_main_1 > stoch_signal_1 and
            stoch_main_0 < stoch_signal_0 and
            stoch_main_0 >= 80):
        sell_signal = True

    return buy_signal, sell_signal


def execute_trade(signal_type: str, last_price: float):
    """
    Exact equivalent of ExecuteTrade()
    Note: original MQ5 does NOT open real orders – only draws arrow + sends Telegram + Alert
    """
    global last_signal_time

    now = datetime.now(timezone.utc)
    time_str = now.strftime("%Y-%m-%d %H:%M")

    msg = (
        f"📈 Signal: {signal_type}\n"
        f"Symbol: {SYMBOL}\n"
        f"Price: {last_price:.5f}\n"
        f"Time: {time_str}"
    )

    send_telegram(msg)
    logger.info(f"📢 Signal on {SYMBOL} | Type: {signal_type} | Price: {last_price:.5f}")

    last_signal_time = time.time()


def main_loop():
    global last_signal_time
    logger.info(f"✅ atr3 Expert (Python) started | Symbol={SYMBOL} | Cooldown={COOLDOWN_MINUTES}min")

    while True:
        try:
            # Cooldown check (exact)
            if time.time() - last_signal_time < COOLDOWN_MINUTES * 60:
                time.sleep(POLL_INTERVAL_SEC)
                continue

            df = fetch_m1_data(SYMBOL, bars=100)
            if df is None or len(df) < 30:
                logger.warning("Not enough data, retrying...")
                time.sleep(POLL_INTERVAL_SEC)
                continue

            buy_signal, sell_signal = check_signals(df)

            last_price = float(df["close"].iloc[-1])

            # EXACT same inverted logic as original MQ5:
            # if(buySignal) ExecuteTrade(ORDER_TYPE_SELL);
            # else if(sellSignal) ExecuteTrade(ORDER_TYPE_BUY);
            if buy_signal:
                execute_trade("SELL", last_price)
            elif sell_signal:
                execute_trade("BUY", last_price)

        except Exception as e:
            logger.exception(f"Error in main loop: {e}")

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    # Simple health endpoint support for Render (optional)
    # If you deploy as web service you can add Flask, but for background worker this is enough
    main_loop()
