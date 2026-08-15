# atr3 Stochastic Telegram Bot (Exact Python Port of atr3.mq5)

این ربات **عیناً** منطق فایل `atr3.mq5` را پیاده‌سازی کرده است.

## منطق سیگنال (دقیقاً مثل MQ5)

- اندیکاتور: **Stochastic (5, 3, 3, SMA, Low/High)**
- تایم‌فریم: فقط **M1**
- **سیگنال خرید (Buy Signal)**:
  ```
  stochMain[1] < stochSignal[1] && stochMain[0] > stochSignal[0] && stochMain[0] <= 20
  ```
- **سیگنال فروش (Sell Signal)**:
  ```
  stochMain[1] > stochSignal[1] && stochMain[0] < stochSignal[0] && stochMain[0] >= 80
  ```

### نکته مهم (مثل کد اصلی)
در کد اصلی MQ5 سیگنال‌ها **برعکس** اجرا می‌شوند:
- اگر `buySignal == true` → پیام **SELL** ارسال می‌شود
- اگر `sellSignal == true` → پیام **BUY** ارسال می‌شود

این رفتار عمداً حفظ شده است.

## پارامترهای استفاده نشده
پارامترهای RSI، Bollinger Bands، MAها، ADX و ... در کد اصلی تعریف شده‌اند اما **هیچ‌کدام در منطق OnTick استفاده نشده‌اند**. در نسخه پایتون هم همین‌طور هستند (فقط برای سازگاری نگه داشته شده‌اند).

## نصب و اجرا محلی

```bash
git clone <your-repo>
cd stoch-telegram-bot
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# فایل .env را ویرایش کنید
python main.py
```

## دیپلوی روی Render

1. ریپو را روی GitHub بگذارید.
2. در [Render.com](https://render.com) یک **Background Worker** جدید بسازید.
3. Repository را وصل کنید.
4. تنظیمات:
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`
5. در بخش Environment Variables این‌ها را اضافه کنید:

| Key                  | Value                                      |
|----------------------|--------------------------------------------|
| TELEGRAM_TOKEN       | `8808022991:AAFmonV527NXUTIE5zpvAmvzRboS0MSEB0w` |
| TELEGRAM_CHAT_ID     | `-1003698594050`                           |
| SYMBOL               | `EURUSD` (یا هر جفت‌ارز دیگر)             |
| TWELVE_DATA_API_KEY  | کلید رایگان از [twelvedata.com](https://twelvedata.com) |
| POLL_INTERVAL_SEC    | `15`                                       |
| COOLDOWN_MINUTES     | `3`                                        |

> **توصیه قوی**: حتماً یک API Key رایگان از Twelve Data بگیرید. بدون آن ربات از yfinance استفاده می‌کند که برای داده M1 پیوسته محدودیت دارد.

## فرمت پیام تلگرام (عین MQ5)

```
📈 Signal: SELL
Symbol: EURUSD
Price: 1.08542
Time: 2026-08-15 21:15
```

## ساختار فایل‌ها

```
stoch-telegram-bot/
├── main.py              # منطق اصلی (دقیق مطابق atr3.mq5)
├── requirements.txt
├── Procfile             # برای Render Background Worker
├── .env.example
└── README.md
```

## نکات امنیتی
توکن تلگرام در فایل اصلی MQ5 به صورت hard-code نوشته شده بود. در این نسخه می‌توانید آن را از طریق Environment Variable مدیریت کنید.

---
**نسخه**: 1.1 (مطابق version اصلی MQ5)
