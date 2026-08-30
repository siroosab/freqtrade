import json
from datetime import UTC, datetime
from pathlib import Path

from pandas import DataFrame


def _should_log_pair(strategy: object, pair: str) -> bool:
    mode = getattr(strategy, "hyperopt_summary_mode", "all")
    if mode == "all" or mode != "pair":
        return True

    selected = getattr(strategy, "hyperopt_summary_pairs", None)
    single = getattr(strategy, "hyperopt_summary_pair", None)

    normalized_pair = pair.strip().lower()
    allowed = []
    if isinstance(selected, list):
        allowed.extend(str(item).strip().lower() for item in selected)
    if single is not None:
        allowed.append(str(single).strip().lower())

    if not allowed:
        return True

    return normalized_pair in allowed or normalized_pair.replace(":usdt", "") in allowed


def _read_pair_status(strategy: object, pair: str) -> dict:
    config = getattr(strategy, "config", {})
    user_data_dir = config.get("user_data_dir") if config else None
    if not user_data_dir:
        return {}

    status_file = Path(user_data_dir) / "hyperopt_results" / "scheduler_status.json"
    try:
        statuses = json.loads(status_file.read_text(encoding="utf-8"))
        return statuses.get(pair, {})
    except (OSError, ValueError, TypeError):
        return {}


def _format_elapsed(timestamp: str) -> str:
    try:
        completed_at = datetime.fromisoformat(timestamp)
        if completed_at.tzinfo is None:
            completed_at = completed_at.replace(tzinfo=UTC)
        elapsed_hours = max(
            0.0, (datetime.now(UTC) - completed_at).total_seconds() / 3600
        )
        return f"{elapsed_hours:.1f}h ago"
    except (TypeError, ValueError):
        return "unknown"


def log_hyperopt_summary(
    strategy: object, dataframe: DataFrame, metadata: dict, *, force: bool = False
) -> str | None:
    if dataframe is None or getattr(dataframe, "empty", True):
        return None

    pair = metadata.get("pair", "unknown") if metadata else "unknown"
    if not _should_log_pair(strategy, pair):
        return None

    timeframe = metadata.get("timeframe", getattr(strategy, "timeframe", "unknown"))
    rows = len(dataframe)

    last = dataframe.iloc[-1]
    close = float(last["close"]) if "close" in dataframe.columns else 0.0
    rsi = float(last["rsi"]) if "rsi" in dataframe.columns else 0.0
    ema_fast = float(last["ema_fast"]) if "ema_fast" in dataframe.columns else 0.0
    ema_slow = float(last["ema_slow"]) if "ema_slow" in dataframe.columns else 0.0
    adx = float(last["adx"]) if "adx" in dataframe.columns else 0.0

    summary = (
        f"Hyperopt summary | pair={pair} tf={timeframe} rows={rows} "
        f"close={close:.6f} rsi={rsi:.2f} ema_fast={ema_fast:.6f} "
        f"ema_slow={ema_slow:.6f} adx={adx:.2f}"
    )
    status = _read_pair_status(strategy, pair)
    if status:
        result = status.get("status", "unknown")
        elapsed = _format_elapsed(status.get("timestamp"))
        summary += f" | last_hyperopt={elapsed} result={result}"
        if result == "success" and status.get("loss") is not None:
            summary += f" loss={float(status['loss']):.6f}"
    else:
        summary += " | last_hyperopt=unknown result=not_run"

    return summary
