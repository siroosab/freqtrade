"""Futures strategy example driven by the live FreqUI pair-control panel.

Copy this file to the bot's user_data/strategies directory and configure the bot
with strategy=SampleStrategyUI. The UI controls are read-only helpers here; this
example shows where to enforce them in a strategy.
"""

from datetime import datetime
from typing import Any

import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, get_pair_control, stoploss_from_absolute


class SampleStrategyUI(IStrategy):
    INTERFACE_VERSION = 3
    can_short = True
    timeframe = "5m"
    startup_candle_count = 200
    process_only_new_candles = True
    use_custom_stoploss = True
    position_adjustment_enable = True

    minimal_roi = {"0": 0.04}
    stoploss = -0.10
    trailing_stop = False

    def pair_control(self, pair: str) -> dict[str, Any]:
        """Return the live controls configured for this futures pair in FreqUI."""
        return get_pair_control(pair)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=9)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=21)
        dataframe["breakout_high"] = dataframe["high"].rolling(20).max().shift(1)
        dataframe["breakout_low"] = dataframe["low"].rolling(20).min().shift(1)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        controls = self.pair_control(metadata["pair"])["pre_trade"]
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = ""

        strictness = controls["entry_strictness"] / 100
        long_rsi = dataframe["rsi"] < 30 - (10 * strictness)
        short_rsi = dataframe["rsi"] > 70 + (10 * strictness)
        long_ema = (dataframe["ema_fast"] > dataframe["ema_slow"]) & (
            dataframe["ema_fast"].shift(1) <= dataframe["ema_slow"].shift(1)
        )
        short_ema = (dataframe["ema_fast"] < dataframe["ema_slow"]) & (
            dataframe["ema_fast"].shift(1) >= dataframe["ema_slow"].shift(1)
        )
        long_breakout = dataframe["close"] > dataframe["breakout_high"]
        short_breakout = dataframe["close"] < dataframe["breakout_low"]

        signal_map = {
            "rsi": (long_rsi, short_rsi),
            "ema": (long_ema, short_ema),
            "breakout": (long_breakout, short_breakout),
        }
        selected = controls["entry_signal"]
        if selected == "all":
            long_signal = long_rsi & long_ema & long_breakout
            short_signal = short_rsi & short_ema & short_breakout
        else:
            long_signal, short_signal = signal_map[selected]

        if controls["long_enabled"]:
            dataframe.loc[long_signal & (dataframe["volume"] > 0), "enter_long"] = 1
            dataframe.loc[long_signal, "enter_tag"] = controls["entry_tag"] or "ui_long"
        if controls["short_enabled"]:
            dataframe.loc[short_signal & (dataframe["volume"] > 0), "enter_short"] = 1
            dataframe.loc[short_signal, "enter_tag"] = controls["entry_tag"] or "ui_short"
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> bool:
        controls = self.pair_control(pair)["pre_trade"]
        if side == "long":
            return controls["long_enabled"] and self._inside_range(
                rate, controls["long_price_min"], controls["long_price_max"]
            )
        return controls["short_enabled"] and self._inside_range(
            rate, controls["short_price_min"], controls["short_price_max"]
        )

    @staticmethod
    def _inside_range(rate: float, minimum: float | None, maximum: float | None) -> bool:
        return (minimum is None or rate >= minimum) and (maximum is None or rate <= maximum)

    def custom_stake_amount(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        controls = self.pair_control(pair)["pre_trade"]
        if controls["entry_size_mode"] == "usdt":
            requested = controls["entry_size_value"]
        else:
            requested = max_stake * controls["entry_size_value"] / 100
        if min_stake is not None:
            requested = max(requested, min_stake)
        return min(requested, max_stake)

    def leverage(
        self,
        pair: str,
        current_time: datetime,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        side: str,
        **kwargs,
    ) -> float:
        configured = self.pair_control(pair)["pre_trade"]["leverage"]
        return min(float(configured), 5.0, max_leverage)

    def custom_stoploss(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        after_fill: bool,
        **kwargs,
    ) -> float | None:
        risk = self.pair_control(pair)["risk"]
        if not risk["stoploss_enabled"]:
            return None
        if risk["stoploss_mode"] == "price" and risk["stoploss_price"] is not None:
            return stoploss_from_absolute(
                risk["stoploss_price"], current_rate, is_short=trade.is_short, leverage=trade.leverage
            )
        if risk["stoploss_percent"] is not None:
            return abs(risk["stoploss_percent"]) / 100
        return None

    def custom_exit(
        self,
        pair: str,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ) -> str | bool | None:
        risk = self.pair_control(pair)["risk"]
        if risk["take_profit_enabled"] and risk["take_profit_percent"] is not None:
            if current_profit >= risk["take_profit_percent"] / 100:
                return "ui_take_profit"
        if risk["inactivity_exit_enabled"] and risk["inactivity_minutes"] is not None:
            age_minutes = (current_time - trade.open_date_utc).total_seconds() / 60
            loss_limit = risk["inactivity_loss_percent"] or 0
            if age_minutes >= risk["inactivity_minutes"] and current_profit <= loss_limit / 100:
                return "ui_inactivity_exit"
        return None

    def adjust_trade_position(
        self,
        trade: Trade,
        current_time: datetime,
        current_rate: float,
        current_profit: float,
        min_stake: float | None,
        max_stake: float,
        current_entry_rate: float,
        current_exit_rate: float,
        current_entry_profit: float,
        current_exit_profit: float,
        **kwargs,
    ) -> float | None:
        risk = self.pair_control(trade.pair)["risk"]
        if not risk["averaging_enabled"] or risk["averaging_trigger_value"] is None:
            return None
        trigger = risk["averaging_trigger_value"]
        triggered = (
            current_profit <= trigger / 100
            if risk["averaging_trigger_mode"] == "percent"
            else current_profit * trade.stake_amount <= -trigger
        )
        if not triggered:
            return None
        size = (
            trade.stake_amount * risk["averaging_size_value"] / 100
            if risk["averaging_size_mode"] == "percent"
            else risk["averaging_size_value"]
        )
        return min(size, max_stake)
