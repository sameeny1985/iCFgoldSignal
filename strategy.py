import time
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd


@dataclass
class StrategyConfig:
    # همان inputهای EA
    risk_percent: float = 3.0
    stop_loss_percent: float = 3.0
    take_profit_percent: float = 6.0

    rsi_period: int = 8
    rsi_overbuy: float = 70.0
    rsi_oversell: float = 30.0

    sto_overbuy_crs: float = 70.0
    sto_oversell_crs: float = 30.0

    sto_overbuy_ext: float = 80.0
    sto_oversell_ext: float = 20.0

    bb_period: int = 20
    bb_dev: float = 2.0

    ma1_period: int = 10
    ma2_period: int = 21
    ma3_period: int = 30
    ma4_period: int = 50

    adx_period: int = 8
    adx_trend_level: float = 20.0

    flat_candle_lookback: int = 22
    min_trend_candles: int = 17

    cooldown_minutes: int = 3


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def rsi_wilder(close: pd.Series, period: int) -> pd.Series:
    """
    Wilder RSI مطابق روش استاندارد MQL5 RSI.
    """
    delta = close.diff()

    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

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

    rs = avg_gain / avg_loss.replace(0, np.nan)

    result = 100 - (100 / (1 + rs))

    # وقتی loss صفر است RSI عملاً 100 است.
    result = result.where(
        ~((avg_loss == 0) & (avg_gain > 0)),
        100.0
    )

    return result


def stochastic_sma(
    df: pd.DataFrame,
    k_period: int = 5,
    slowing: int = 3,
    d_period: int = 3
):
    """
    معادل iStochastic(
        symbol,
        PERIOD_M1,
        5,
        3,
        3,
        MODE_SMA,
        STO_LOWHIGH
    )

    Main = Slow %K
    Signal = SMA(Main, 3)
    """

    low_min = df["low"].rolling(
        k_period,
        min_periods=k_period
    ).min()

    high_max = df["high"].rolling(
        k_period,
        min_periods=k_period
    ).max()

    denominator = (high_max - low_min)

    fast_k = 100.0 * (
        (df["close"] - low_min) /
        denominator.replace(0, np.nan)
    )

    # slowing = 3
    main = fast_k.rolling(
        slowing,
        min_periods=slowing
    ).mean()

    signal = main.rolling(
        d_period,
        min_periods=d_period
    ).mean()

    return main, signal


def mql5_bollinger_bug_compatible(
    close: pd.Series,
    period: int,
    bb_dev: float
):
    """
    این قسمت عمداً رفتار کد MQL5 ارسال‌شده را تقلید می‌کند.

    کد اصلی:

        iBands(
            _Symbol,
            PERIOD_M1,
            BB_Period,
            BB_Dev,
            0,
            PRICE_CLOSE
        );

    Signature واقعی iBands:

        period
        bands_shift
        deviation
        applied_price

    بنابراین با BB_Dev=2 و آرگومان بعدی 0:

        bands_period = 20
        bands_shift  = 2
        deviation    = 0

    در نتیجه upper/lower از نظر مقدار با SMA برابر می‌شوند.
    """

    middle = sma(close, period)

    # deviation=0 در کد اصلی
    upper = middle.copy()
    lower = middle.copy()

    # نکته: shift در مقدار CopyBuffer به شکل مستقیم
    # در محاسبه ما لازم نیست؛ زیرا EA مقدار buffer را
    # برای positionهای خودش می‌خواند.
    return upper, middle, lower


def prepare_indicators(df: pd.DataFrame, cfg: StrategyConfig):
    df = df.copy()

    # Stochastic
    stoch_main, stoch_signal = stochastic_sma(
        df,
        k_period=5,
        slowing=3,
        d_period=3
    )

    df["stoch_main"] = stoch_main
    df["stoch_signal"] = stoch_signal

    # Bollinger - intentionally compatible with supplied MQL5
    bb_upper, bb_middle, bb_lower = mql5_bollinger_bug_compatible(
        df["close"],
        cfg.bb_period,
        cfg.bb_dev
    )

    df["bb_upper"] = bb_upper
    df["bb_lower"] = bb_lower
    df["bb_middle"] = bb_middle

    # MA
    df["ma_fast"] = sma(df["close"], cfg.ma1_period)
    df["ma_slow"] = sma(df["close"], cfg.ma2_period)

    # RSI
    df["rsi"] = rsi_wilder(
        df["close"],
        cfg.rsi_period
    )

    return df


class ATR3Strategy:
    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg
        self.last_signal_time = 0.0

    def cooldown_active(self) -> bool:
        if self.last_signal_time <= 0:
            return False

        return (
            time.time() - self.last_signal_time
            < self.cfg.cooldown_minutes * 60
        )

    def calculate(self, df: pd.DataFrame):
        """
        اجرای منطق OnTick.

        خروجی:
            ["BUY"]
            ["SELL"]
            []
            یا در حالت duplicate واقعی EA:
            ["SELL", "SELL"]
            ["BUY", "BUY"]
        """

        if len(df) < 100:
            return []

        df = prepare_indicators(df, self.cfg)

        # ==========================================================
        # تقلید CopyRates(..., 0, 100, price) با ArraySetAsSeries(true)
        #
        # در نتیجه:
        #
        # price[0] = current candle
        # price[1] = previous closed candle
        #
        # ==========================================================

        current = df.iloc[-1]
        previous = df.iloc[-2]

        # ==========================================================
        # تقلید CopyBuffer(stoch, 0, 0, 3, stochMain)
        #
        # طبق مستندات MQL5، قدیمی‌ترین عضو ابتدای حافظه قرار می‌گیرد.
        #
        # بنابراین برای سه مقدار:
        #
        # stochMain[0] = 2 bars ago
        # stochMain[1] = 1 bar ago
        # stochMain[2] = current
        #
        # کد اصلی از [0] و [1] استفاده می‌کند.
        # ==========================================================

        sm0 = df["stoch_main"].iloc[-3]
        sm1 = df["stoch_main"].iloc[-2]
        sm2 = df["stoch_main"].iloc[-1]

        ss0 = df["stoch_signal"].iloc[-3]
        ss1 = df["stoch_signal"].iloc[-2]
        ss2 = df["stoch_signal"].iloc[-1]

        # Bollinger نیز به همان ترتیب CopyBuffer
        bb_lower_0 = df["bb_lower"].iloc[-3]
        bb_upper_0 = df["bb_upper"].iloc[-3]

        # ==========================================================
        # مرحله اول
        # ==========================================================

        buy_ready = False
        sell_ready = False

        # 1. Stochastic cross
        if (
            sm1 < ss1
            and sm0 > ss0
            and sm0 <= self.cfg.sto_oversell_crs
        ):
            buy_ready = True

        if (
            sm1 > ss1
            and sm0 < ss0
            and sm0 >= self.cfg.sto_overbuy_crs
        ):
            sell_ready = True

        # ==========================================================
        # Bollinger
        #
        # کد MQL5:
        #
        # if(buyReady && price[0].low > bbLower[0])
        #     buyReady = false;
        #
        # if(sellReady && price[0].high < bbUpper[0])
        #     sellReady = false;
        #
        # ==========================================================

        if buy_ready and current["low"] > bb_lower_0:
            buy_ready = False

        if sell_ready and current["high"] < bb_upper_0:
            sell_ready = False

        # ==========================================================
        # MA filter
        #
        # CopyBuffer(handle, 0, 0, 1, maFast)
        # => current value
        # ==========================================================

        ma_fast = current["ma_fast"]
        ma_slow = current["ma_slow"]

        if buy_ready and ma_fast < ma_slow:
            buy_ready = False

        if sell_ready and ma_fast > ma_slow:
            sell_ready = False

        # ==========================================================
        # RSI
        # ==========================================================

        rsi = current["rsi"]

        if buy_ready and rsi > self.cfg.rsi_overbuy:
            buy_ready = False

        if sell_ready and rsi < self.cfg.rsi_oversell:
            sell_ready = False

        signals = []

        # ==========================================================
        # اجرای معامله - دقیقاً مثل کد اصلی
        #
        # نکته مهم:
        # buyReady -> SELL
        # sellReady -> BUY
        # ==========================================================

        if buy_ready and sm0 > self.cfg.sto_oversell_ext:
            signals.append("SELL")

        if sell_ready and sm0 < self.cfg.sto_overbuy_ext:
            signals.append("BUY")

        # ==========================================================
        # مرحله دوم
        #
        # این قسمت در EA تکرار مرحله قبل است.
        # عمداً حذف نشده تا رفتار دقیق کد اصلی حفظ شود.
        # ==========================================================

        if buy_ready and sm0 > self.cfg.sto_oversell_ext:
            signals.append("SELL")

        if sell_ready and sm0 < self.cfg.sto_overbuy_ext:
            signals.append("BUY")

        if signals:
            self.last_signal_time = time.time()

        return signals
