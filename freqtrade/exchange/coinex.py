import logging
from copy import deepcopy
from datetime import datetime

import ccxt

from freqtrade.constants import BuySell
from freqtrade.enums import MarginMode, TradingMode
from freqtrade.exceptions import TemporaryError
from freqtrade.exchange import Exchange
from freqtrade.exchange.exchange_types import CcxtOrder, FtHas
from freqtrade.util.datetime_helpers import dt_from_ts


logger = logging.getLogger(__name__)


class Coinex(Exchange):
    """
    CoinEx exchange class.
    Contains adjustments needed for Freqtrade to work with this exchange.
    """

    _ft_has: FtHas = {
        "ohlcv_has_history": False,
        "l2_limit_range": [20],
        "trades_has_history": False,
        "tickers_have_bid_ask": False,
        "stoploss_on_exchange": False,  # keep False until stop orders are fully tested
        "exchange_has_overrides": {"fetchTrades": False},
        "marketOrderRequiresPrice": True,
        "download_data_parallel_quick": False,
        "ws_enabled": True,
        # Futures defaults
        "ccxt_futures_name": "swap",
        "mark_ohlcv_price": "mark",
        "mark_ohlcv_timeframe": "8h",
        "funding_fee_timeframe": "1h",
    }

    _ft_has_futures: FtHas = {
        # if you later add native stop orders, flip this to True and add mapping/params
        "stoploss_on_exchange": False,
        "stoploss_order_types": {"limit": "limit"},
        "stoploss_blocks_assets": False,
        "stop_price_prop": "stopPrice",
        "funding_fee_timeframe": "1h",
        "funding_fee_candle_limit": 500,
        "uses_leverage_tiers": False,
        "ccxt_futures_name": "swap",
    }

    _supported_trading_mode_margin_pairs: list[tuple[TradingMode, MarginMode]] = [
        (TradingMode.SPOT, MarginMode.NONE),
        (TradingMode.FUTURES, MarginMode.ISOLATED),
        (TradingMode.FUTURES, MarginMode.CROSS),
    ]

    @property
    def _ccxt_config(self) -> dict:
        """
        Extend base ccxt options:
        - Ensure swap default
        - Force defaultSettle to stake currency (usually USDT)
        """
        cfg = super()._ccxt_config
        if self.trading_mode == TradingMode.FUTURES:
            settle = self._config.get("stake_currency", "USDT")
            opts = deepcopy(cfg.get("options", {}))
            opts.update({"defaultType": self._ft_has["ccxt_futures_name"], "defaultSettle": settle})
            cfg = deepcopy(cfg)
            cfg["options"] = opts
        return cfg

    def additional_exchange_init(self) -> None:
        """
        Do not call setPositionMode on CoinEx (not supported yet).
        Optionally set position mode only if exchange reports support.
        """
        try:
            if self.exchange_has("setPositionMode"):
                # One-way mode (hedged=False) if ever supported in the future.
                # Use snake_case method; ccxt maps to setPositionMode internally.
                self._api.set_position_mode(hedged=False)  # type: ignore[attr-defined]
        except ccxt.NotSupported:
            # Silently ignore - CoinEx currently does not support this.
            pass
        except ccxt.BaseError as e:
            # Do not crash startup for optional tweak
            logger.warning(f"additional_exchange_init optional step failed: {e}")

    def get_max_leverage(self, pair: str, stake_amount: float | None) -> float:
        """
        CoinEx: no leverage tiers via API; get from market limits if available.
        """
        if self.trading_mode != TradingMode.FUTURES:
            return 1.0
        market = self.markets.get(pair, {})
        lev_limits = (market.get("limits") or {}).get("leverage") or {}
        max_lev = lev_limits.get("max")
        return float(max_lev) if max_lev is not None else 1.0

    # ...existing code...
    def _lev_prep(self, pair: str, leverage: float, side: BuySell, accept_fail: bool = False):
        """
        CoinEx needs leverage along with setMarginMode(). Override base behaviour.
        """
        if self.trading_mode != TradingMode.FUTURES:
            return super()._lev_prep(pair, leverage, side, accept_fail)

        # Set margin mode (with leverage in params) then set leverage explicitly.
        mm = getattr(self, "margin_mode", None)
        if mm:
            try:
                params = {"leverage": int(leverage) if leverage else 1}
                mode = mm.value.lower() if hasattr(mm, "value") else str(mm).lower()
                if self.exchange_has("setMarginMode"):
                    if hasattr(self._api, "set_margin_mode"):
                        self._api.set_margin_mode(mode, pair, params)  # type: ignore[attr-defined]
                    else:
                        self._api.setMarginMode(mode, pair, params)  # fallback
            except ccxt.BaseError as e:
                if accept_fail:
                    logger.warning(f"set_margin_mode() optional failed: {e}")
                else:
                    raise

        try:
            if self.exchange_has("setLeverage"):
                if hasattr(self._api, "set_leverage"):
                    self._api.set_leverage(int(leverage), pair)  # type: ignore[attr-defined]
                else:
                    self._api.setLeverage(int(leverage), pair)
        except ccxt.BaseError as e:
            if accept_fail:
                logger.warning(f"set_leverage() optional failed: {e}")
            else:
                raise
        return None
# ...existing code...

    def dry_run_liquidation_price(
        self,
        pair: str,
        open_rate: float,
        is_short: bool,
        amount: float,
        stake_amount: float,
        leverage: float,
        wallet_balance: float,
        open_trades: list,
    ) -> float | None:
        """
        Defer to base Exchange implementation (isolated linear swap).
        """
        return super().dry_run_liquidation_price(
            pair=pair,
            open_rate=open_rate,
            is_short=is_short,
            amount=amount,
            stake_amount=stake_amount,
            leverage=leverage,
            wallet_balance=wallet_balance,
            open_trades=open_trades,
        )

    def get_funding_fees(
        self, pair: str, amount: float, is_short: bool, open_date: datetime
    ) -> float:
        """
        Use base logic: live -> fetchFundingHistory; dry-run -> mark/funding calc.
        """
        return super().get_funding_fees(pair, amount, is_short, open_date)

    def _adjust_coinex_order(self, order: dict) -> dict:
        """
        Sanitize/normalize CoinEx order objects so Freqtrade can reliably track them.
        """
        if not isinstance(order, dict):
            return order

        # 1) Ensure id is a string
        if order.get("id") is not None:
            try:
                order["id"] = str(order["id"])
            except Exception as exc:
                logger.debug(f"CoinEx: failed to cast order id to str: {exc}")

        # 2) Normalize status if exchange returns non-ccxt values via raw info
        #    ccxt usually normalizes to open/closed/canceled, but be defensive.
        self._normalize_status(order)

        # 3) Fix broken or missing lastTradeTimestamp (critical for order refind logic)
        self._fix_last_trade_ts(order)

        # 4) Some CoinEx responses omit average for partially/fully filled orders.
        #    Compute from trades so Freqtrade can set proper open/close rates.
        self._backfill_average_from_trades(order)

        return order

    @staticmethod
    def _is_valid_ts(ts: int | float | None) -> bool:
        if ts is None:
            return False
        try:
            v = float(ts)
        except Exception:
            return False
        # Accept seconds or milliseconds, positive and plausible epoch
        return v > 1e8 and v < 1e13

    def _normalize_status(self, order: dict) -> None:
        status = order.get("status")
        if status in (None, "open", "closed", "canceled"):
            return
        raw = (order.get("info") or {}).get("status") or status
        mapping = {
            "cancelled": "canceled",
            "finished": "closed",
            "filled": "closed",
            "done": "closed",
        }
        order["status"] = mapping.get(str(raw).lower(), status)

    def _fix_last_trade_ts(self, order: dict) -> None:
        ltt = order.get("lastTradeTimestamp")
        if self._is_valid_ts(ltt):
            return
        # Use creation/update timestamp as fallback when filled
        ts = order.get("timestamp") or (order.get("info") or {}).get("update_time")
        if self._is_valid_ts(ts) and (order.get("filled") or 0) > 0 and order.get("status") in (
            "closed",
            "canceled",
        ):
            order["lastTradeTimestamp"] = ts
        else:
            # As a last resort, drop the invalid field to let core default to now()
            order.pop("lastTradeTimestamp", None)

    def _backfill_average_from_trades(self, order: dict) -> None:
        try:
            if (
                order.get("average") is None
                and (order.get("filled") or 0) > 0
                and order.get("status") in ("closed", "canceled")
            ):
                # since must be a datetime - derive from order timestamp if available
                ts = order.get("timestamp")
                since_dt = dt_from_ts(ts) if ts else None
                trades = self.get_trades_for_order(
                    str(order["id"]),
                    order.get("symbol"),
                    since=since_dt or datetime.utcfromtimestamp(0),
                )
                if trades:
                    total_amt = sum(t.get("amount", 0) for t in trades)
                    if total_amt:
                        total_cost = sum(
                            t.get("price", 0) * t.get("amount", 0) for t in trades
                        )
                        order["average"] = total_cost / total_amt
        except Exception as e:
            # Do not break order processing due to ancillary average calc
            logger.debug(f"CoinEx order average backfill failed: {e}")

    def fetch_order(self, order_id: str, pair: str, params: dict | None = None) -> CcxtOrder:
        """
        Fetch single order and apply CoinEx-specific normalization.
        """
        try:
            order = super().fetch_order(order_id, pair, params)
            return self._adjust_coinex_order(order)
        except TemporaryError as e:
            # Base layer may have already translated ccxt.ExchangeError into TemporaryError.
            msg = str(e).lower()
            if any(
                x in msg
                for x in [
                    "order not exists",
                    "order_not_exists",
                    "order not exist",
                    "order_not_exist",
                ]
            ):
                fake: CcxtOrder = {
                    "id": str(order_id),
                    "symbol": pair,
                    "status": "canceled",
                    "amount": 0.0,
                    "filled": 0.0,
                    "remaining": 0.0,
                    "fee": {},
                    "info": {"error": "order not exists"},
                }
                return self._adjust_coinex_order(fake)
            raise
        except ccxt.ExchangeError as e:
            # CoinEx sometimes returns generic ExchangeError with message "order not exists"
            msg = str(e).lower()
            if any(
                x in msg
                for x in [
                    "order not exists",
                    "order_not_exists",
                    "order not exist",
                    "order_not_exist",
                ]
            ):
                # Synthesize a canceled order so Freqtrade can close local open-order state
                fake: CcxtOrder = {
                    "id": str(order_id),
                    "symbol": pair,
                    "status": "canceled",
                    "amount": 0.0,
                    "filled": 0.0,
                    "remaining": 0.0,
                    "fee": {},
                    "info": {"error": "order not exists"},
                }
                return self._adjust_coinex_order(fake)
            raise

    def fetch_orders(
        self, pair: str, since: datetime, params: dict | None = None
    ) -> list[CcxtOrder]:
        """
        Fetch multiple orders and apply CoinEx-specific normalization.
        """
        orders = super().fetch_orders(pair, since, params)
        return [self._adjust_coinex_order(o) for o in orders] 
