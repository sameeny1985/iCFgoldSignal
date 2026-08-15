# atr3_final.mq5 → Python (Exact Port)

ربات دقیقاً مطابق فایل `atr3_final.mq5` نسخه ۲.۰ نوشته شده است.

## ویژگی‌ها

- **بدون نیاز به API Key داده بازار** (از yfinance استفاده می‌کند)
- منطق Stochastic + Bollinger + MA + RSI دقیقاً مثل MQ5
- سیگنال‌ها **عمداً برعکس** اجرا می‌شوند (مثل کد اصلی):
  - `buyReady` → پیام **SELL**
  - `sellReady` → پیام **BUY**
- کامنت‌های کد اصلی حفظ شده‌اند
- فقط تایم‌فریم M1

## منطق سیگنال (خلاصه)

1. کراس Stochastic در منطقه اشباع (`Sto_OverSell_Crs` / `Sto_OverBuy_Crs`)
2. فیلتر تماس با باند بولینگر
3. فیلتر MA سریع vs کند
4. فیلتر RSI
5. شرط خروج از منطقه اشباع (`Sto_OverSell_Ext` / `Sto_OverBuy_Ext`)
6. ارسال سیگنال برعکس + تلگرام

## دیپلوی روی Render

1. ریپو را روی GitHub بگذارید
2. در Render یک **Background Worker** بسازید
3. تنظیمات:

| فیلد              | مقدار                            |
|-------------------|----------------------------------|
| Runtime           | Python 3                         |
| Build Command     | `pip install -r requirements.txt`|
| **Start Command** | `python main.py`                 |

4. Environment Variables:

```
TELEGRAM_TOKEN=توکن_ربات_شما
TELEGRAM_CHAT_ID=آیدی_چت_یا_کانال
SYMBOL=EURUSD=X
POLL_INTERVAL_SEC=20
CooldownMinutes=3
```

> برای طلا از `XAUUSD=X` و برای بیت‌کوین از `BTC-USD` استفاده کنید.

## اجرای محلی

```bash
pip install -r requirements.txt
cp .env.example .env
# توکن تلگرام را در .env بگذارید
python main.py
```

## ساختار

```
stoch-telegram-bot/
├── main.py
├── requirements.txt
├── Procfile
├── .env.example
├── .gitignore
└── README.md
```
