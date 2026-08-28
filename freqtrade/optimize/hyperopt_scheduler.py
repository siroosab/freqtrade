"""Scheduled, pair-by-pair data download and hyperopt orchestration."""

import logging
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from freqtrade.constants import Config
from freqtrade.enums import RunMode
from freqtrade.misc import file_dump_json, pair_to_filename


logger = logging.getLogger(__name__)


class HyperoptScheduler:
    """Run isolated hyperopt jobs for configured pairs on a fixed interval."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.settings = config.get("hyperopt_scheduler", {})
        if not isinstance(self.settings, dict):
            raise ValueError("hyperopt_scheduler must be a dictionary")

    def get_pairs(self) -> list[str]:
        pairs = self.settings.get("pairs")
        if pairs is None:
            pairs = self.config.get("pairs") or self.config.get("exchange", {}).get(
                "pair_whitelist", []
            )
        if not isinstance(pairs, list) or not all(isinstance(pair, str) for pair in pairs):
            raise ValueError("hyperopt_scheduler.pairs must be a list of pair names")
        return list(dict.fromkeys(pairs))

    def _download_pair(self, pair: str) -> None:
        from freqtrade.data.history import download_data_main

        download_config = deepcopy(self.config)
        download_config["runmode"] = RunMode.UTIL_EXCHANGE
        download_config["pairs"] = [pair]
        download_config["timeframes"] = self.settings.get(
            "timeframes", [self.config.get("timeframe", "5m")]
        )
        if "days" in self.settings:
            download_config["days"] = self.settings["days"]
            download_config.pop("timerange", None)
        download_data_main(download_config)

    def _run_pair(self, pair: str) -> dict[str, Any] | None:
        from freqtrade.optimize.hyperopt import Hyperopt

        self._download_pair(pair)

        hyperopt_config = deepcopy(self.config)
        hyperopt_config["runmode"] = RunMode.HYPEROPT
        hyperopt_config["pairs"] = [pair]
        hyperopt_config.setdefault("exchange", {})["pair_whitelist"] = [pair]
        hyperopt_config["epochs"] = self.settings.get("epochs", hyperopt_config.get("epochs", 100))
        hyperopt_config["spaces"] = self.settings.get(
            "spaces", hyperopt_config.get("spaces", ["default"])
        )
        hyperopt_config["disableparamexport"] = True

        hyperopt = Hyperopt(hyperopt_config)
        hyperopt.start()
        best = hyperopt.current_best_epoch
        if not best:
            logger.warning("No hyperopt result was produced for %s", pair)
            return None

        result_dir = Path(self.config["user_data_dir"]) / "hyperopt_results" / "by_pair"
        result_dir.mkdir(parents=True, exist_ok=True)
        result = {
            "strategy_name": hyperopt.hyperopter.get_strategy_name(),
            "pair": pair,
            "params": best["params"],
            "loss": best["loss"],
            "results_metrics": best.get("results_metrics", {}),
        }
        file_dump_json(result_dir / f"{pair_to_filename(pair)}.json", result, log=False)
        self._update_strategy_params(hyperopt, pair, best["params"])
        return result

    def _update_strategy_params(self, hyperopt: Any, pair: str, params: dict[str, Any]) -> None:
        from freqtrade.optimize.hyperopt_tools import HyperoptTools

        strategy_file = Path(hyperopt.hyperopter.backtesting.strategy.__file__).with_suffix(".json")
        strategy_params = HyperoptTools.load_params(strategy_file) if strategy_file.is_file() else {}
        strategy_params["strategy_name"] = hyperopt.hyperopter.get_strategy_name()
        strategy_params.setdefault("params", {}).setdefault("pairs", {})[pair] = params
        file_dump_json(strategy_file, strategy_params, log=False)

    def run_once(self) -> list[dict[str, Any]]:
        from filelock import FileLock, Timeout

        results = []
        lock = FileLock(self.config["user_data_dir"] / "hyperopt.lock")
        try:
            with lock.acquire(timeout=1):
                for pair in self.get_pairs():
                    logger.info("Starting scheduled hyperopt for %s", pair)
                    result = self._run_pair(pair)
                    if result:
                        results.append(result)
        except Timeout:
            logger.info("Another running instance of freqtrade Hyperopt detected.")
        return results

    def run_forever(self) -> None:
        interval_hours = float(self.settings.get("interval_hours", 24))
        if interval_hours <= 0:
            raise ValueError("hyperopt_scheduler.interval_hours must be greater than zero")

        run_on_start = self.settings.get("run_on_start", True)
        while True:
            if run_on_start:
                self.run_once()
            run_on_start = True
            time.sleep(interval_hours * 3600)