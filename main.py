#!/usr/bin/env python3
"""
Exact core logic from atr3_final.mq5
Temporarily ONLY Stochastic cross + zone (BB/MA/RSI filters disabled for test)
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

Sto_OverBuy_Crs = int(os.getenv("Sto_OverBuy_Crs", "70"))
Sto_OverSell_Crs = int(os.getenv("Sto_OverSell_Crs", "30"))
Sto_OverBuy_Ext = int(os.getenv("Sto_OverBuy_Ext", "80"))
Sto_OverSell_Ext = int(os.getenv("Sto_OverSell_Ext", "20"))

CooldownMinutes = int(os.getenv("CooldownMinutes", "3"))

telegramToken = os.getenv("TELEGRAM_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")
telegramChatID = os.getenv("TELEGRAM_CHAT_ID", "PUT_YOUR_CHAT_ID_HERE")

SYMBOL = os.getenv("SYMBOL", "BTC-USD")
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "20"))
PORT = int(os.getenv("PORT", "10000"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("atr3")

lastSignalTime = 0.0


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")
    def log_message(self, format, *args):
        pass


def start_http_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    logger.info(f"HTTP server on port {PORT}")
    server.serve_forever()


def SendTelegram(message: str) -> bool:
    if not telegramToken or "PUT_YOUR" in telegramToken:
        logger.error("TELEGRAM_TOKEN not set")
        return False
    if not telegramChatID or "PUT_YOUR" in telegramChatID:
        logger.error("TELEGRAM_CHAT_ID not set")
        return False
    url = f"https://api.telegram.org/bot{telegramToken}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": telegramChatID, "text": message}, timeout=10)
        if r.status_code == 200:
            logger.info("Telegram OK")
            return True
        logger.error(f"Telegram fail: {r.status_code} {r.text}")
        return False
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False


def fetch_m1_data(symbol: str, bars: int = 100) -> Optional[pd.DataFrame]:
    try:
        import yfinance as yf
        data = yf.download(symbol, period="5d", interval="1m", progress=False, auto_adjust=True, threads=False)
        if data is None or data.empty:
            logger.error("empty data")
            return None
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = [c[0] if isinstance(c, tuple) else c for c in data.columns]
        data = data.reset_index()
        data.columns = [str(c).lower().strip() for c in data.columns]
        time_col = None
        for c in ["datetime", "date", "time"]:
            if c in data.columns:
                time_col = c
                break
        if time_col is None:
            logger.error(f"no time col: {list(data.columns)}")
            return None
        data = data.rename(columns={time_col: "time"})
        for col in ["open", "high", "low", "close"]:
            if col not in data.columns:
                logger.error(f"missing {col}")
                return None
            data[col] = pd.to_numeric(data[col], errors="coerce")
        data = data[["time", "open", "high", "low", "close"]].dropna().tail(bars).reset_index(drop=True)
        if len(data) < 30:
            return None
        logger.info(f"Fetched {len(data)} bars | close={float(data['close'].iloc[-1]):.2f}")
        return data
    except Exception as e:
        logger.error(f"fetch error: {e}")
        return None


def calculate_stochastic(df: pd.DataFrame):
    low = df["low"]
    high = df["high"]
    close = df["close"]
    lowest = low.rolling(5).min()
    highest = high.rolling(5).max()
    denom = (highest - lowest).replace(0, np.nan)
    raw_k = 100.0 * (close - lowest) / denom
    k = raw_k.rolling(3).mean()
    d = k.rolling(3).mean()
    return k, d


def ExecuteTrade(order_type: str, price: float):
    global lastSignalTime
    signal_type = "BUY" if order_type == "BUY" else "SELL"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    msg = f"📈 Signal: {signal_type}\nSymbol: {SYMBOL}\nPrice: {price:.2f}\nTime: {now_str}"
    SendTelegram(msg)
    logger.info(f"SIGNAL SENT | {signal_type} | {price:.2f}")
    lastSignalTime = time.time()


def OnTick():
    global lastSignalTime
    if time.time() - lastSignalTime < CooldownMinutes * 60:
        return

    df = fetch_m1_data(SYMBOL, 100)
    if df is None or len(df) < 30:
        return

    stochMain, stochSignal = calculate_stochastic(df)
    if any(pd.isna(x) for x in [stochMain.iloc[-1], stochSignal.iloc[-1], stochMain.iloc[-2], stochSignal.iloc[-2]]):
        return

    # MQ5: [0] = current, [1] = previous
    sMain0 = float(stochMain.iloc[-1])
    sMain1 = float(stochMain.iloc[-2])
    sSig0  = float(stochSignal.iloc[-1])
    sSig1  = float(stochSignal.iloc[-2])

    buyReady = False
    sellReady = False

    # 1️⃣ کراس استوکاستیک در منطقه اشباع (عین MQ5)
    if (sMain1 < sSig1 and sMain0 > sSig0 and sMain0 <= Sto_OverSell_Crs):
        buyReady = True
    if (sMain1 > sSig1 and sMain0 < sSig0 and sMain0 >= Sto_OverBuy_Crs):
        sellReady = True

    # ===== فیلترهای BB / MA / RSI موقتاً خاموش برای تست =====
    # if buyReady and price0_low > bbL0: buyReady = False
    # if sellReady and price0_high < bbU0: sellReady = False
    # if buyReady and maF0 < maS0: buyReady = False
    # if sellReady and maF0 > maS0: sellReady = False
    # if buyReady and rsi > RSI_OverBuy: buyReady = False
    # if sellReady and rsi < RSI_OverSell: sellReady = False

    logger.info(f"CHECK | buyReady={buyReady} sellReady={sellReady} | Main0={sMain0:.2f} Sig0={sSig0:.2f} | Main1={sMain1:.2f} Sig1={sSig1:.2f}")

    # 5️⃣ اجرای معامله (عمداً برعکس مثل MQ5)
    if buyReady and sMain0 > Sto_OverSell_Ext:
        ExecuteTrade("SELL", float(df["close"].iloc[-1]))
    if sellReady and sMain0 < Sto_OverBuy_Ext:
        ExecuteTrade("BUY", float(df["close"].iloc[-1]))

    # مرحله دوم (عین MQ5)
    if buyReady and sMain0 > Sto_OverSell_Ext:
        ExecuteTrade("SELL", float(df["close"].iloc[-1]))
    if sellReady and sMain0 < Sto_OverBuy_Ext:
        ExecuteTrade("BUY", float(df["close"].iloc[-1]))


def bot_loop():
    logger.info(f"ATR3 started | SYMBOL={SYMBOL} | ONLY Stoch cross (filters OFF for test)")
    while True:
        try:
            OnTick()
        except Exception as e:
            logger.exception(str(e))
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()
    start_http_server()
