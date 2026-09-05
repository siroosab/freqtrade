"""Live per-pair controls shared by the strategy and the API server."""

from copy import deepcopy
from threading import RLock
from typing import Any


_DEFAULT_SETTINGS: dict[str, dict[str, Any]] = {
    "pre_trade": {
        "long_enabled": True,
        "short_enabled": True,
        "long_price_min": None,
        "long_price_max": None,
        "short_price_min": None,
        "short_price_max": None,
        "entry_size_mode": "percent",
        "entry_size_value": 1.0,
        "leverage": 1,
        "entry_signal": "all",
        "entry_strictness": 50,
        "entry_tag": "",
    },
    "risk": {
        "stoploss_enabled": False,
        "stoploss_mode": "percent",
        "stoploss_price": None,
        "stoploss_percent": None,
        "averaging_enabled": False,
        "averaging_trigger_mode": "percent",
        "averaging_trigger_value": None,
        "averaging_size_mode": "percent",
        "averaging_size_value": None,
        "take_profit_enabled": False,
        "take_profit_percent": None,
        "inactivity_exit_enabled": False,
        "inactivity_minutes": None,
        "inactivity_loss_percent": None,
        "trailing_stop_enabled": False,
        "trailing_stop_percent": None,
        "liquidation_buffer_percent": None,
    },
}


class PairControlStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._settings: dict[str, dict[str, dict[str, Any]]] = {}

    def get(self, pair: str) -> dict[str, dict[str, Any]]:
        with self._lock:
            settings = self._settings.get(pair, _DEFAULT_SETTINGS)
            return deepcopy(settings)

    def set(self, pair: str, updates: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        with self._lock:
            current = self._settings.setdefault(pair, deepcopy(_DEFAULT_SETTINGS))
            for section in ("pre_trade", "risk"):
                values = updates.get(section)
                if values is not None:
                    current[section].update(values)
            return deepcopy(current)

    def snapshot(self) -> dict[str, dict[str, dict[str, Any]]]:
        with self._lock:
            return deepcopy(self._settings)


pair_control_store = PairControlStore()


def get_pair_control(pair: str) -> dict[str, dict[str, Any]]:
    """Read live UI controls for a pair from inside a strategy."""
    return pair_control_store.get(pair)
