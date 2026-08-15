import os
import time
from datetime import datetime
import ccxt
import pandas as pd
import pandas_ta as ta
import requests
from flask import Flask

# تنظیمات از Environment Variables
TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN", "8669710314:AAGzGTAfGoNGnE6eELqxZVe4SWDTDBE0Qlc"
)
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-1003714269439")
SYMBOL = os.getenv("SYMBOL", "BTC/USDT")  # نماد قابل تعویض در اینوایرنمنت

# پارامترهای ورودی دقیقا مطابق MQL5
RSI_Period = 8
RSI_OverBuy = 70
RSI_OverSell = 30
Sto_OverBuy_Crs = 70
Sto_OverSell_Crs = 30
Sto_OverBuy_Ext = 80
Sto_OverSell_Ext = 20

BB_Period = 20
BB_Dev = 2.0
MA1_Period = 10
MA2_Period = 21

CooldownMinutes = 3
last_signal_time = 0

# اتصال به صرافی (بایننس به عنوان جایگزین استاندارد بین‌المللی قیمت)
exchange = ccxt.binance(
    {"enableRateLimit": True, "options": {"defaultType": "spot"}}
)

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is running!"


def send_telegram(message):
  url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
  payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
  try:
    requests.post(url, data=payload, timeout=5)
  except Exception as e:
    print(f"Telegram error: {e}")


def check_and_run_strategy():
  global last_signal_time

  try:
    # گرفتن داده‌های کندل 1 دقیقه اخیر (تایم‌فریم M1)
    bars = exchange.fetch_ohlcv(SYMBOL, timeframe="1m", limit=100)
    df = pd.DataFrame(
        bars, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )

    if len(df) < 50:
      return

    # محاسبه اندیکاتورها دقیقاً مطابق متاتریدر
    # 1. استوکاستیک (5, 3, 3)
    stoch = df.ta.stoch(
        high="high",
        low="low",
        close="close",
        k=5,
        d=3,
        smooth_k=3,
    )
    # نام ستون‌ها معمولا STOCHk_5_3_3 و STOCHd_5_3_3 است
    df["stoch_main"] = stoch["STOCHk_5_3_3"]
    df["stoch_signal"] = stoch["STOCHd_5_3_3"]

    # 2. باندهای بولینگر
    bb = df.ta.bbands(close="close", length=BB_Period, std=BB_Dev)
    df["bb_lower"] = bb[f"BBL_{BB_Period}_{BB_Dev}"]
    df["bb_upper"] = bb[f"BBU_{BB_Period}_{BB_Dev}"]

    # 3. میانگین‌های متحرک
    df["ma_fast"] = df.ta.sma(length=MA1_Period)
    df["ma_slow"] = df.ta.sma(length=MA2_Period)

    # 4. RSI
    df["rsi"] = df.ta.rsi(length=RSI_Period)

    # ایندکس -1 کندل جاری و -2 کندل قبلی بسته شده است
    i = -2  # مشابه کندل [1] در متاتریدر
    curr = -1  # مشابه کندل [0] در متاتریدر

    stoch_main_prev, stoch_main_curr = df["stoch_main"].iloc[i], df["stoch_main"].iloc[curr]
    stoch_sig_prev, stoch_sig_curr = df["stoch_signal"].iloc[i], df["stoch_signal"].iloc[curr]

    low_curr = df["low"].iloc[curr]
    high_curr = df["high"].iloc[curr]
    close_curr = df["close"].iloc[curr]

    bb_lower_curr = df["bb_lower"].iloc[curr]
    bb_upper_curr = df["bb_upper"].iloc[curr]

    ma_fast_curr = df["ma_fast"].iloc[curr]
    ma_slow_curr = df["ma_slow"].iloc[curr]
    rsi_curr = df["rsi"].iloc[curr]

    buy_ready = False
    sell_ready = False

    # 1️⃣ کراس استوکاستیک در منطقه اشباع (منطق دقیق فایل شما)
    if (
        stoch_main_prev < stoch_sig_prev
        and stoch_main_curr > stoch_sig_curr
        and stoch_main_curr <= Sto_OverSell_Crs
    ):
      buy_ready = True
    if (
        stoch_main_prev > stoch_sig_prev
        and stoch_main_curr < stoch_sig_curr
        and stoch_main_curr >= Sto_OverBuy_Crs
    ):
      sell_ready = True

    # 2️⃣ فیلتر بولینگر
    if buy_ready and low_curr > bb_lower_curr:
      buy_ready = False
    if sell_ready and high_curr < bb_upper_curr:
      sell_ready = False

    # فیلتر میانگین‌ها
    if buy_ready and ma_fast_curr < ma_slow_curr:
      buy_ready = False
    if sell_ready and ma_fast_curr > ma_slow_curr:
      sell_ready = False

    # فیلتر RSI
    if buy_ready and rsi_curr > RSI_OverBuy:
      buy_ready = False
    if sell_ready and rsi_curr < RSI_OverSell:
      sell_ready = False

    # بررسی کول‌داون (Cooldown)
    current_time = time.time()
    if current_time - last_signal_time < CooldownMinutes * 60:
      return

    # 5️⃣ اجرای معامله و ارسال سیگنال (مشابه مرحله اول و دوم کدهای شما)
    signal_type = None
    if buy_ready and stoch_main_curr > Sto_OverSell_Ext:
      signal_type = "SELL"  # توجه: در کد شما بخش اول بای‌ردی به سل ختم می‌شود
    elif sell_ready and stoch_main_curr < Sto_OverBuy_Ext:
      signal_type = "BUY"

    if not signal_type:
      if buy_ready and stoch_main_curr > Sto_OverSell_Ext:
        signal_type = "SELL"
      if sell_ready and stoch_main_curr < Sto_OverBuy_Ext:
        signal_type = "BUY"

    if signal_type:
      last_signal_time = current_time
      time_str = datetime.now().strftime("%Y-%m-%d %H:%M")
      msg = (
          f"📈 <b>Signal: {signal_type}</b>\n"
          f"Symbol: {SYMBOL}\n"
          f"Price: {close_curr:.5f}\n"
          f"Time: {time_str}"
      )
      send_telegram(msg)

  except Exception as e:
    print(f"Error in strategy execution: {e}")


def worker():
  while True:
    check_and_run_strategy()
    time.sleep(15)  # بررسی هر 15 ثانیه برای همگام‌سازی با کندل‌های M1


if __name__ == "__main__":
  import threading

  t = threading.Thread(target=worker, daemon=True)
  t.start()

  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port)
