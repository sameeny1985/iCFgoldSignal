#!/usr/bin/env python3
"""
atr3_final.mq5 → Python (Exact logic)
Works on Render FREE Web Service
- Minimal HTTP server so Render doesn't kill the process
- Main signal loop runs in background thread
- Gold symbol: GC=F (reliable on yfinance)
"""

import os
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Optional, Tuple
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
import pandas as pd
import numpy as np
from dotenv import load_dotenv

load_dotenv()

# ======================== Parameters ========================
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

CooldownMinutes = int(os.getenv("CooldownMinutes", "3"))

telegramToken = os.getenv("TELEGRAM_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")
telegramChatID = os.getenv("TELEGRAM_CHAT_ID", "PUT_YOUR_CHAT_ID_HERE")

# GC=F = Gold Futures (most reliable free source)
SYMBOL = os.getenv("SYMBOL", "GC=F")
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "30"))

PORT = int(os.getenv("PORT", "10000"))

# ==============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("atr3-final")

lastSignalTime: float = 0.0


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"atr3 bot is running")

    def log_message(self, format, *args):
        pass  # silence default HTTP logs


def start_http_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    logger.info(f"HTTP health server started on port {PORT}")
    server.serve_forever()


def SendTelegram(message: str) -> bool:
    if not telegramToken or telegramToken == "PUT_YOUR_BOT_TOKEN_HERE":
        logger.error("Telegram token not set")
        return False
    if not telegramChatID or telegramChatID == "PUT_YOUR_CHAT_ID_HERE":
        logger.error("Telegram chat_id not set")
        return False

    url = f"https://api.telegram.org/bot{telegramToken}/sendMessage"
    payload = {"chat_id": telegramChatID, "text": message}
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code == 200:
            logger.info("Telegram message sent successfully")
            return True
        logger.error(f"Telegram failed: {r.status_code} - {r.text}")
        return False
    except Exception as e:
        logger.error(f"Telegram exception: {e}")
        return False


def fetch_m1_data(symbol: str, bars: int = 100) -> Optional[pd.DataFrame]:
    try:
        import yfinance as yf

        data = yf.download(
            symbol,
            period="5d",
            interval="1m",
            progress=False,
            auto_adjust=True,
            threads=False,
        )

        if data is None or data.empty:
            logger.error("yfinance returned empty data")
            return None

        # Flatten MultiIndex columns
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = [c[0] if isinstance(c, tuple) else c for c in data.columns]

        data = data.reset_index()
        data.columns = [str(c).lower().strip() for c in data.columns]

        # Find time column
        time_col = None
        for candidate in ["datetime", "date", "time"]:
            if candidate in data.columns:
                time_col = candidate
                break
        if time_col is None:
            logger.error(f"No time column. Available: {list(data.columns)}")
            return None

        data = data.rename(columns={time_col: "time"})

        for col in ["open", "high", "low", "close"]:
            if col not in data.columns:
                logger.error(f"Missing {col}. Available: {list(data.columns)}")
                return None
            data[col] = pd.to_numeric(data[col], errors="coerce")

        data = data[["time", "open", "high", "low", "close"]].dropna()
        data = data.tail(bars).reset_index(drop=True)

        if len(data) < 30:
            logger.warning(f"Only {len(data)} bars")
            return None

        logger.info(f"Fetched {len(data)} bars | Last close: {float(data['close'].iloc[-1]):.2f}")
        return data

    except Exception as e:
        logger.error(f"fetch_m1_data failed: {e}")
        return None


def calculate_stochastic(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    low = df["low"]
    high = df["high"]
    close = df["close"]
    lowest_low = low.rolling(5).min()
    highest_high = high.rolling(5).max()
    denom = (highest_high - lowest_low).replace(0, np.nan)
    raw_k = 100.0 * (close - lowest_low) / denom
    k = raw_k.rolling(3).mean()
    d = k.rolling(3).mean()
    return k, d


def calculate_bollinger(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    mid = df["close"].rolling(BB_Period).mean()
    std = df["close"].rolling(BB_Period).std(ddof=0)
    return mid + BB_Dev * std, mid - BB_Dev * std


def calculate_ma(df: pd.DataFrame, period: int) -> pd.Series:
    return df["close"].rolling(period).mean()


def calculate_rsi(df: pd.DataFrame, period: int) -> pd.Series:
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def ExecuteTrade(order_type: str, df: pd.DataFrame):
    global lastSignalTime
    if len(df) < 2:
        return

    close_price = float(df.iloc[-2]["close"])
    signal_type = "BUY" if order_type == "BUY" else "SELL"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    msg = (
        f"📈 Signal: {signal_type}\n"
        f"Symbol: {SYMBOL}\n"
        f"Price: {close_price:.2f}\n"
        f"Time: {now_str}"
    )
    SendTelegram(msg)
    logger.info(f"📢 Signal | {signal_type} | {close_price:.2f}")
    lastSignalTime = time.time()


def OnTick():
    global lastSignalTime

    if time.time() - lastSignalTime < CooldownMinutes * 60:
        return

    df = fetch_m1_data(SYMBOL, 100)
    if df is None or len(df) < 30:
        return

    stochMain, stochSignal = calculate_stochastic(df)
    if any(pd.isna(x) for x in [stochMain.iloc[-1], stochSignal.iloc[-1],
                                 stochMain.iloc[-2], stochSignal.iloc[-2]]):
        return

    sMain0 = float(stochMain.iloc[-1])
    sMain1 = float(stochMain.iloc[-2])
    sSig0 = float(stochSignal.iloc[-1])
    sSig1 = float(stochSignal.iloc[-2])

    bbUpper, bbLower = calculate_bollinger(df)
    if pd.isna(bbUpper.iloc[-1]) or pd.isna(bbLower.iloc[-1]):
        return
    bbU0 = float(bbUpper.iloc[-1])
    bbL0 = float(bbLower.iloc[-1])

    price0_low = float(df["low"].iloc[-1])
    price0_high = float(df["high"].iloc[-1])

    buyReady = False
    sellReady = False

    # 1️⃣ کراس استوکاستیک در منطقه اشباع
    if sMain1 < sSig1 and sMain0 > sSig0 and sMain0 <= Sto_OverSell_Crs:
        buyReady = True
    if sMain1 > sSig1 and sMain0 < sSig0 and sMain0 >= Sto_OverBuy_Crs:
        sellReady = True

    # 2️⃣ فیلتر بولینگر
    if buyReady and price0_low > bbL0:
        buyReady = False
    if sellReady and price0_high < bbU0:
        sellReady = False

    # فیلتر میانگین‌ها
    maFast = calculate_ma(df, MA1_Period)
    maSlow = calculate_ma(df, MA2_Period)
    if pd.isna(maFast.iloc[-1]) or pd.isna(maSlow.iloc[-1]):
        return
    if buyReady and float(maFast.iloc[-1]) < float(maSlow.iloc[-1]):
        buyReady = False
    if sellReady and float(maFast.iloc[-1]) > float(maSlow.iloc[-1]):
        sellReady = False

    # فیلتر RSI
    rsi = calculate_rsi(df, RSI_Period)
    if pd.isna(rsi.iloc[-1]):
        return
    rsi_val = float(rsi.iloc[-1])
    if buyReady and rsi_val > RSI_OverBuy:
        buyReady = False
    if sellReady and rsi_val < RSI_OverSell:
        sellReady = False

    # 5️⃣ اجرای معامله (عمداً برعکس مثل MQ5)
    if buyReady and sMain0 > Sto_OverSell_Ext:
        ExecuteTrade("SELL", df)
    if sellReady and sMain0 < Sto_OverBuy_Ext:
        ExecuteTrade("BUY", df)

    # مرحله دوم (مثل MQ5)
    if buyReady and sMain0 > Sto_OverSell_Ext:
        ExecuteTrade("SELL", df)
    if sellReady and sMain0 < Sto_OverBuy_Ext:
        ExecuteTrade("BUY", df)


def bot_loop():
    logger.info(f"✅ atr3_final started | Symbol={SYMBOL} | Cooldown={CooldownMinutes}min")
    while True:
        try:
            OnTick()
        except Exception as e:
            logger.exception(f"Error in bot loop: {e}")
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    # Start bot logic in background thread
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()

    # Start HTTP server (required for free Web Service on Render)
    start_http_server()
