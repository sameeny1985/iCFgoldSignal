#!/usr/bin/env python3
"""
Exact Python port of atr3_final.mq5 (version 2.0)
Multi Logic Expert – Stochastic + Bollinger + MA + RSI
- Timeframe: M1 only
- Signals are intentionally inverted (buyReady → SELL, sellReady → BUY)
- No external market-data API key required (uses yfinance)
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

# ======================== Input Parameters (same as MQ5) ========================
RiskPercent = float(os.getenv("RiskPercent", "3.0"))
StopLossPercent = float(os.getenv("StopLossPercent", "3.0"))
TakeProfitPercent = float(os.getenv("TakeProfitPercent", "6.0"))

RSI_Period = int(os.getenv("RSI_Period", "8"))
RSI_OverBuy = int(os.getenv("RSI_OverBuy", "70"))
RSI_OverSell = int(os.getenv("RSI_OverSell", "30"))

Sto_OverBuy_Crs = int(os.getenv("Sto_OverBuy_Crs", "70"))
Sto_OverSell_Crs = int(os.getenv("Sto_OverSell_Crs", "30"))
Sto_OverBuy_Ext = int(os.getenv("Sto_OverBuy_Ext", "80"))
Sto_OverSell_Ext = int(os.getenv("Sto_OverSell_Ext", "20"))

BB_Period = int(os.getenv("BB_Period", "20"))
BB_Dev = float(os.getenv("BB_Dev", "2.0"))

MA1_Period = int(os.getenv("MA1_Period", "10"))
MA2_Period = int(os.getenv("MA2_Period", "21"))
MA3_Period = int(os.getenv("MA3_Period", "30"))
MA4_Period = int(os.getenv("MA4_Period", "50"))
# MA_Method = MODE_SMA (hard-coded)

ADX_Period = int(os.getenv("ADX_Period", "8"))
ADX_TrendLevel = float(os.getenv("ADX_TrendLevel", "20.0"))
FlatCandleLookback = int(os.getenv("FlatCandleLookback", "22"))
MinTrendCandles = int(os.getenv("MinTrendCandles", "17"))

CooldownMinutes = int(os.getenv("CooldownMinutes", "3"))

# Telegram (set via env or leave empty – you must fill them)
telegramToken = os.getenv("TELEGRAM_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")
telegramChatID = os.getenv("TELEGRAM_CHAT_ID", "PUT_YOUR_CHAT_ID_HERE")

# Symbol (yfinance style for forex: EURUSD=X)
SYMBOL = os.getenv("SYMBOL", "EURUSD=X")
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "20"))

# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("atr3-final")

lastSignalTime: float = 0.0


def SendTelegram(message: str) -> bool:
    """Exact equivalent of SendTelegram() in MQ5"""
    if not telegramToken or telegramToken == "PUT_YOUR_BOT_TOKEN_HERE":
        logger.error("Telegram token not set")
        return False
    if not telegramChatID or telegramChatID == "PUT_YOUR_CHAT_ID_HERE":
        logger.error("Telegram chat_id not set")
        return False

    url = f"https://api.telegram.org/bot{telegramToken}/sendMessage"
    payload = {
        "chat_id": telegramChatID,
        "text": message,
    }
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code == 200:
            logger.info("Telegram message sent successfully")
            return True
        else:
            logger.error(f"Telegram send failed: {r.status_code} - {r.text}")
            return False
    except Exception as e:
        logger.error(f"Telegram exception: {e}")
        return False


def CalculateLot() -> float:
    """Kept for compatibility – not used for actual trading in original"""
    # In cloud we have no account balance, so just return a dummy
    return 0.01


def fetch_m1_data(symbol: str, bars: int = 100) -> Optional[pd.DataFrame]:
    """
    Fetch M1 candles using yfinance – NO API KEY required.
    For forex use symbols like EURUSD=X, GBPUSD=X, XAUUSD=X, etc.
    """
    try:
        import yfinance as yf
        # yfinance 1m data is limited to last ~7 days
        data = yf.download(
            symbol,
            period="2d",
            interval="1m",
            progress=False,
            auto_adjust=True,
            threads=False,
        )
        if data is None or data.empty:
            logger.error("yfinance returned empty data")
            return None

        data = data.reset_index()
        # Normalize column names
        cols = {}
        for c in data.columns:
            cl = str(c).lower()
            if "date" in cl or "time" in cl or "datetime" in cl:
                cols[c] = "time"
            elif cl == "open":
                cols[c] = "open"
            elif cl == "high":
                cols[c] = "high"
            elif cl == "low":
                cols[c] = "low"
            elif cl == "close":
                cols[c] = "close"
        data = data.rename(columns=cols)

        needed = ["time", "open", "high", "low", "close"]
        for col in needed:
            if col not in data.columns:
                logger.error(f"Missing column: {col}")
                return None

        data = data[needed].dropna()
        data = data.tail(bars).reset_index(drop=True)

        # Ensure numeric
        for col in ["open", "high", "low", "close"]:
            data[col] = pd.to_numeric(data[col], errors="coerce")

        data = data.dropna().reset_index(drop=True)
        if len(data) < 30:
            logger.warning(f"Only {len(data)} bars available")
            return None
        return data
    except Exception as e:
        logger.error(f"fetch_m1_data failed: {e}")
        return None


def calculate_stochastic(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """
    Stochastic Oscillator exactly as MQL5:
    iStochastic(..., 5, 3, 3, MODE_SMA, STO_LOWHIGH)
    """
    low = df["low"]
    high = df["high"]
    close = df["close"]

    lowest_low = low.rolling(window=5).min()
    highest_high = high.rolling(window=5).max()
    denom = highest_high - lowest_low
    denom = denom.replace(0, np.nan)

    raw_k = 100.0 * (close - lowest_low) / denom
    # %K with slowing = 3 (SMA)
    k = raw_k.rolling(window=3).mean()
    # %D = SMA of %K with period 3
    d = k.rolling(window=3).mean()
    return k, d


def calculate_bollinger(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """iBands(..., BB_Period, BB_Dev, 0, PRICE_CLOSE)"""
    mid = df["close"].rolling(window=BB_Period).mean()
    std = df["close"].rolling(window=BB_Period).std(ddof=0)  # population std like many platforms
    upper = mid + BB_Dev * std
    lower = mid - BB_Dev * std
    return upper, lower


def calculate_ma(df: pd.DataFrame, period: int) -> pd.Series:
    """iMA SMA"""
    return df["close"].rolling(window=period).mean()


def calculate_rsi(df: pd.DataFrame, period: int) -> pd.Series:
    """iRSI"""
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def ExecuteTrade(order_type: str, df: pd.DataFrame):
    """
    Exact equivalent of ExecuteTrade()
    Uses the previous candle (index 1 in series terms) like the MQ5 code.
    """
    global lastSignalTime

    if len(df) < 2:
        return

    # In MQ5: price[1] after ArraySetAsSeries → previous completed candle
    # In our df (oldest → newest): iloc[-2]
    prev = df.iloc[-2]
    signal_time = prev["time"]
    candle_high = float(prev["high"])
    candle_low = float(prev["low"])
    close_price = float(prev["close"])

    signal_type = "BUY" if order_type == "BUY" else "SELL"

    # Telegram message – exact format
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    msg = (
        f"📈 Signal: {signal_type}\n"
        f"Symbol: {SYMBOL}\n"
        f"Price: {close_price:.5f}\n"
        f"Time: {now_str}"
    )
    SendTelegram(msg)

    logger.info(f"📢 Signal on {SYMBOL} | Type: {signal_type} | Price: {close_price:.5f}")
    lastSignalTime = time.time()


def OnTick():
    """Exact port of OnTick() logic including comments and order of operations"""
    global lastSignalTime

    # Cooldown
    if time.time() - lastSignalTime < CooldownMinutes * 60:
        return

    df = fetch_m1_data(SYMBOL, bars=100)
    if df is None or len(df) < 30:
        return

    # ---------- Stochastic ----------
    stochMain, stochSignal = calculate_stochastic(df)
    # We need the last three values; newest = iloc[-1]  (corresponds to MQ5 [0])
    if (pd.isna(stochMain.iloc[-1]) or pd.isna(stochSignal.iloc[-1]) or
            pd.isna(stochMain.iloc[-2]) or pd.isna(stochSignal.iloc[-2])):
        return

    # MQ5 series: [0]=current, [1]=previous
    sMain0 = float(stochMain.iloc[-1])
    sMain1 = float(stochMain.iloc[-2])
    sSig0  = float(stochSignal.iloc[-1])
    sSig1  = float(stochSignal.iloc[-2])

    # ---------- Bollinger ----------
    bbUpper, bbLower = calculate_bollinger(df)
    if pd.isna(bbUpper.iloc[-1]) or pd.isna(bbLower.iloc[-1]):
        return
    bbU0 = float(bbUpper.iloc[-1])
    bbL0 = float(bbLower.iloc[-1])

    # price[0] = current bar
    price0_low  = float(df["low"].iloc[-1])
    price0_high = float(df["high"].iloc[-1])

    # ----------------- مرحله اول ----------------- #
    buyReady = False
    sellReady = False

    # 1. کراس خطوط استوکاستیک در منطقه اشباع
    # if(stochMain[1] < stochSignal[1] && stochMain[0] > stochSignal[0] && stochMain[0] <= Sto_OverSell_Crs)
        # buyReady = true;
    # if(stochMain[1] > stochSignal[1] && stochMain[0] < stochSignal[0] && stochMain[0] >= Sto_OverBuy_Crs)
        # sellReady = true;

    # 2. قیمت به یا عبور از باند بولینگر
    #if(buyReady && price[0].low > bbLower[0]) buyReady = false;
    #if(sellReady && price[0].high < bbUpper[0]) sellReady = false;
    #if(buyReady && price[1].low < bbLower[1] && price[0].close > bbLower[0])
    #    buyReady = true;
    #else
    #    buyReady = false;

    #if(sellReady && price[1].high > bbUpper[1] && price[0].close < bbUpper[0])
    #    sellReady = true;
    #else
    #    sellReady = false;

    # 1️⃣ کراس استوکاستیک در منطقه اشباع
    if (sMain1 < sSig1 and sMain0 > sSig0 and sMain0 <= Sto_OverSell_Crs):
        buyReady = True
    if (sMain1 > sSig1 and sMain0 < sSig0 and sMain0 >= Sto_OverBuy_Crs):
        sellReady = True

    # 2️⃣ فیلتر بولینگر - فقط تماس با باند
    if buyReady and price0_low > bbL0:
        buyReady = False   # هنوز به باند نرسیده
    if sellReady and price0_high < bbU0:
        sellReady = False  # هنوز به باند بالا نرسیده

    # فیلتر میانگین‌ها
    maFast = calculate_ma(df, MA1_Period)
    maSlow = calculate_ma(df, MA2_Period)
    if pd.isna(maFast.iloc[-1]) or pd.isna(maSlow.iloc[-1]):
        return
    maF0 = float(maFast.iloc[-1])
    maS0 = float(maSlow.iloc[-1])

    if buyReady and maF0 < maS0:
        buyReady = False
    if sellReady and maF0 > maS0:
        sellReady = False

    # فیلتر RSI
    rsi_series = calculate_rsi(df, RSI_Period)
    if pd.isna(rsi_series.iloc[-1]):
        return
    rsi = float(rsi_series.iloc[-1])

    if buyReady and rsi > RSI_OverBuy:
        buyReady = False
    if sellReady and rsi < RSI_OverSell:
        sellReady = False

    # 5️⃣ اجرای معامله
    if buyReady and sMain0 > Sto_OverSell_Ext:
        ExecuteTrade("SELL", df)          # intentionally inverted
    if sellReady and sMain0 < Sto_OverBuy_Ext:
        ExecuteTrade("BUY", df)           # intentionally inverted

    # ----------------- مرحله دوم ----------------- #
    # خروج خطوط استوکاستیک از منطقه اشباع
    if buyReady and sMain0 > Sto_OverSell_Ext:  # && stochSignal[0] > Sto_OverSell_Ext)
        ExecuteTrade("SELL", df)          # intentionally inverted
    if sellReady and sMain0 < Sto_OverBuy_Ext:  # && stochSignal[0] < Sto_OverBuy_Ext)
        ExecuteTrade("BUY", df)           # intentionally inverted


def main_loop():
    logger.info(f"✅ atr3_final Expert (Python) started | Symbol={SYMBOL} | Cooldown={CooldownMinutes}min")
    logger.info("No market-data API key required (using yfinance)")

    while True:
        try:
            OnTick()
        except Exception as e:
            logger.exception(f"Error in main loop: {e}")
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main_loop()
